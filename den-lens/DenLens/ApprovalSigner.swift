import CryptoKit
import DeviceCheck
import Foundation
import LocalAuthentication
import Security

/// approval/1.0 on the phone (oneentity docs/adr/0004-approval-protocol.md).
/// The signing key lives in the Secure Enclave and every signature needs Face ID.

enum ApprovalError: LocalizedError, Equatable {
    case secureEnclaveUnavailable
    case keyInvalidated
    case notEnrolled
    case expired

    var errorDescription: String? {
        switch self {
        case .secureEnclaveUnavailable: "This device has no Secure Enclave, so approvals cannot be signed here."
        case .keyInvalidated: "Face ID changed, so the approval key was reset. Register this phone again in Settings."
        case .notEnrolled: "Register this phone as an approver in Settings first."
        case .expired: "The approval request expired. Try again."
        }
    }
}

/// Seam for the Secure Enclave: the simulator has none, so tests use a software key.
protocol ApprovalSigner: Sendable {
    var deviceID: String { get }
    /// Uncompressed SEC1 point, 65 bytes.
    var publicKeySEC1: Data { get }
    /// ECDSA-SHA-256 over `message`, raw 64-byte `r || s`.
    func sign(_ message: Data, reason: String) async throws -> Data
}

enum ApprovalMessage {
    /// The bytes the device signs; SHA-256 of them is the protocol digest. Field rules are in the ADR.
    static func preimage(requestID: String, payloadDigest: String, decision: String, expiresAt: Int) -> Data {
        Data("\(requestID)|\(payloadDigest)|\(decision)|\(expiresAt)".utf8)
    }
}

func base64URL(_ data: Data) -> String {
    data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
}

struct SecureEnclaveSigner: ApprovalSigner {
    private static let service = "com.moltbotden.denlens.approver"
    let deviceID: String
    let publicKeySEC1: Data
    private let keyData: Data

    static var isAvailable: Bool { SecureEnclave.isAvailable }

    /// The existing key, or a new one. `.biometryCurrentSet` ties it to the Face ID enrolled now:
    /// adding a face invalidates the key, and `reset()` plus re-registering recovers.
    static func loadOrCreate() throws -> SecureEnclaveSigner {
        guard isAvailable else { throw ApprovalError.secureEnclaveUnavailable }
        if let data = load("key"), let id = load("id").flatMap({ String(data: $0, encoding: .utf8) }),
           let key = try? SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: data) {
            return SecureEnclaveSigner(deviceID: id, publicKeySEC1: key.publicKey.x963Representation, keyData: data)
        }
        reset()
        var error: Unmanaged<CFError>?
        guard let access = SecAccessControlCreateWithFlags(
            nil, kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly, [.privateKeyUsage, .biometryCurrentSet], &error)
        else { throw error!.takeRetainedValue() as Error }
        let key = try SecureEnclave.P256.Signing.PrivateKey(accessControl: access)
        let id = "denlens-" + UUID().uuidString.lowercased().replacingOccurrences(of: "-", with: "").prefix(24)
        try save("key", key.dataRepresentation)
        try save("id", Data(id.utf8))
        return SecureEnclaveSigner(deviceID: id, publicKeySEC1: key.publicKey.x963Representation, keyData: key.dataRepresentation)
    }

    static func reset() {
        for account in ["key", "id"] { SecItemDelete(query(account) as CFDictionary) }
        ApprovalState.enrolled = false
    }

    func sign(_ message: Data, reason: String) async throws -> Data {
        let keyData = keyData
        return try await Task.detached {
            let context = LAContext()
            context.localizedReason = reason
            // A key invalidated by a Face ID change fails here with an opaque error; Settings offers the reset.
            let key = try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: keyData, authenticationContext: context)
            return try key.signature(for: message).rawRepresentation  // prompts for Face ID
        }.value
    }

    private static func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    }

    private static func load(_ account: String) -> Data? {
        var q = query(account)
        q[kSecReturnData as String] = true
        var out: AnyObject?
        return SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess ? out as? Data : nil
    }

    private static func save(_ account: String, _ data: Data) throws {
        var q = query(account)
        q[kSecValueData as String] = data
        q[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let status = SecItemAdd(q as CFDictionary, nil)
        guard status == errSecSuccess else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
    }
}

/// Whether this phone's key is registered with the API. Kept locally so the flow knows to ask for approval.
enum ApprovalState {
    private static let key = "approver.enrolled"
    static var enrolled: Bool {
        get { UserDefaults.standard.bool(forKey: key) }
        set { UserDefaults.standard.set(newValue, forKey: key) }
    }
}

protocol DeviceAttestor: Sendable {
    func attest(publicKey: Data, deviceID: String) async -> (type: String, data: String, keyID: String)
}

/// App Attest where the device supports it. The API validates Apple's chain and requires the nonce to be
/// SHA-256(signing key || device id), which binds this attestation to the Secure Enclave key. If attesting
/// fails the device registers without one; the API decides whether that is acceptable.
struct AppAttestor: DeviceAttestor {
    func attest(publicKey: Data, deviceID: String) async -> (type: String, data: String, keyID: String) {
        let service = DCAppAttestService.shared
        guard service.isSupported else { return ("none", "", "") }
        do {
            let keyID = try await service.generateKey()
            let hash = Data(SHA256.hash(data: publicKey + Data(deviceID.utf8)))
            return ("app_attest", base64URL(try await service.attestKey(keyID, clientDataHash: hash)), keyID)
        } catch {
            return ("none", "", "")
        }
    }
}
