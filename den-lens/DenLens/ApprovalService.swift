import Foundation

struct ApprovalSummary: Decodable, Equatable {
    let flags: [String]
    let budgetCents: Int
    let committedCents: Int
}

/// The approval/1.0 request the API issues for the contract's current state.
struct ApprovalRequest: Decodable, Equatable {
    let requestId: String
    let payloadDigest: String
    let expiresAt: Int
    let summary: ApprovalSummary
    let signatureRequired: Bool

    func isExpired(now: Date = Date()) -> Bool { Int(now.timeIntervalSince1970) >= expiresAt - 5 }

    var flagLabels: [String] {
        summary.flags.map { $0 == "mainnet_spend" ? "Real money (mainnet)" : $0 == "over_budget_replan" ? "Over budget" : $0 }
    }
}

struct DeviceRegistration: Encodable {
    struct Key: Encodable { let format = "sec1"; let value: String }
    struct Attestation: Encodable { let type: String; let data: String; let keyId: String }
    let deviceId: String
    let publicKey: Key
    let attestation: Attestation
    let label: String
    let createdAt: String
}

struct SignedApproval: Encodable {
    let version = "approval/1.0"
    let requestId: String
    let payloadDigest: String
    let decision = "approve"
    let expiresAt: Int
    let deviceId: String
    let form = "p256"
    let signature: String
}

struct ApproveBody: Encodable { let approval: SignedApproval }

private struct Ignored: Decodable {}

private struct DeviceList: Decodable {
    struct Device: Decodable { let deviceId: String }
    let devices: [Device]
}

struct ApprovalService: Sendable {
    let signer: any ApprovalSigner
    var api: LensAPI
    var attestor: any DeviceAttestor = AppAttestor()
    var deviceLabel = "Den Lens iPhone"

    /// nil where there is no Secure Enclave (simulator).
    static func live(api: LensAPI = LensAPI()) -> ApprovalService? {
        guard SecureEnclaveSigner.isAvailable, let signer = try? SecureEnclaveSigner.loadOrCreate() else { return nil }
        return ApprovalService(signer: signer, api: api)
    }

    /// The registration object (ADR 0004) for this phone's key, attested where the device supports App Attest.
    func registration() async -> DeviceRegistration {
        let attestation = await attestor.attest(publicKey: signer.publicKeySEC1, deviceID: signer.deviceID)
        return DeviceRegistration(
            deviceId: signer.deviceID, publicKey: .init(value: base64URL(signer.publicKeySEC1)),
            attestation: .init(type: attestation.type, data: attestation.data, keyId: attestation.keyID), label: deviceLabel,
            createdAt: ISO8601DateFormatter().string(from: Date()))
    }

    /// Registers this phone's key unless the API already lists it. The first registration may use the agent key;
    /// later devices need the dashboard session (ADR 0004).
    func ensureRegistered() async throws {
        let listed: DeviceList = try await api.get("me/approver-devices")
        if !listed.devices.contains(where: { $0.deviceId == signer.deviceID }) {
            let _: Ignored = try await api.post("me/approver-devices", await registration())
        }
        ApprovalState.enrolled = true
    }

    func request(contractID: String) async throws -> ApprovalRequest {
        try await api.get("contracts/\(contractID)/approval-request")
    }

    /// Signs `request` (Face ID prompt) and approves. The signature covers exactly what the card showed.
    func approve(contractID: String, request: ApprovalRequest, now: Date = Date()) async throws -> Contract {
        guard !request.isExpired(now: now) else { throw ApprovalError.expired }
        let message = ApprovalMessage.preimage(
            requestID: request.requestId, payloadDigest: request.payloadDigest, decision: "approve", expiresAt: request.expiresAt)
        let signature = try await signer.sign(message, reason: "Approve spending up to \(dollars(request.summary.budgetCents))")
        let signed = SignedApproval(
            requestId: request.requestId, payloadDigest: request.payloadDigest, expiresAt: request.expiresAt,
            deviceId: signer.deviceID, signature: base64URL(signature))
        return try await api.post("contracts/\(contractID)/approve", ApproveBody(approval: signed))
    }
}
