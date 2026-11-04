import Foundation
import Observation
import Sentry
import UIKit
import UserNotifications

/// Agent Secret Store approvals on this phone: pending secret-access and wallet-signing requests, each
/// approved or denied with an approval/1.0 signature from the Secure Enclave key (Face ID per signature).

struct InboxItem: Decodable, Equatable, Identifiable {
    let requestId: String
    let kind: String
    let title: String
    let lines: [String]
    let payloadDigest: String
    let signExpiresAt: Int
    var id: String { requestId }

    func isExpired(now: Date = Date()) -> Bool { Int(now.timeIntervalSince1970) >= signExpiresAt - 5 }
}

struct SignedDecision: Encodable {
    let version = "approval/1.0"
    let requestId: String
    let payloadDigest: String
    let decision: String
    let expiresAt: Int
    let deviceId: String
    let form = "p256"
    let signature: String
}

struct AssAPI: Sendable {
    var baseURL = URL(string: "https://api.agentsecretstore.com/v1/approver/")!
    var session: URLSession = .shared
    var token: @Sendable () -> String? = { Keychain.loadASSToken() }

    private struct Inbox: Decodable { let requests: [InboxItem] }
    private struct Registered: Decodable { let deviceToken: String }
    private struct Empty: Decodable {}

    private func send<T: Decodable>(_ path: String, method: String = "GET", body: (any Encodable)? = nil, authorized: Bool = true, as: T.Type) async throws -> T {
        var r = URLRequest(url: baseURL.appending(path: path))
        r.httpMethod = method
        r.setValue("application/json", forHTTPHeaderField: "Accept")
        if authorized {
            guard let token = token() else { throw ApprovalError.notEnrolled }
            r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            r.httpBody = try LensAPI.encoder.encode(AnyEncodable(body))
        }
        let (data, resp) = try await session.data(for: r)
        guard let http = resp as? HTTPURLResponse else { throw LensAPIError.badResponse }
        guard (200..<300).contains(http.statusCode) else {
            throw LensAPIError.http(http.statusCode, String(decoding: data.prefix(200), as: UTF8.self))
        }
        return try LensAPI.decoder.decode(T.self, from: data)
    }

    func inbox() async throws -> [InboxItem] { try await send("inbox", as: Inbox.self).requests }

    func decide(_ decision: SignedDecision) async throws {
        struct Body: Encodable { let approval: SignedDecision }
        let _: Empty = try await send("decisions", method: "POST", body: Body(approval: decision), as: Empty.self)
    }

    func setPushToken(_ hex: String) async throws {
        struct Body: Encodable { let pushToken: String; let pushEnv: String }
        let _: Empty = try await send("device/push-token", method: "PUT", body: Body(pushToken: hex, pushEnv: Self.pushEnv), as: Empty.self)
    }

    /// Pairs this phone with the account that minted `code` in the dashboard and stores the device token.
    func pair(code: String, registration: DeviceRegistration, pushToken: String?) async throws {
        struct Body: Encodable {
            let pairingCode: String
            let registration: DeviceRegistration
            let keyId: String
            let pushToken: String?
            let pushEnv: String
            func encode(to encoder: Encoder) throws {
                try registration.encode(to: encoder)
                var c = encoder.container(keyedBy: Keys.self)
                try c.encode(pairingCode, forKey: .pairingCode)
                try c.encode(keyId, forKey: .keyId)
                try c.encodeIfPresent(pushToken, forKey: .pushToken)
                try c.encode(pushEnv, forKey: .pushEnv)
            }
            enum Keys: String, CodingKey { case pairingCode, keyId, pushToken, pushEnv }
        }
        let body = Body(pairingCode: code.trimmingCharacters(in: .whitespaces).uppercased(), registration: registration, keyId: registration.attestation.keyId, pushToken: pushToken, pushEnv: Self.pushEnv)
        let out = try await send("devices", method: "POST", body: body, authorized: false, as: Registered.self)
        Keychain.saveASSToken(out.deviceToken)
    }

    #if DEBUG
    static let pushEnv = "sandbox"
    #else
    static let pushEnv = "production"
    #endif
}

@Observable
@MainActor
final class ApproverInbox {
    private(set) var items: [InboxItem] = []
    private(set) var busy = false
    private(set) var status = ""
    private(set) var paired: Bool
    private(set) var pushToken: String?

    @ObservationIgnored let api: AssAPI
    @ObservationIgnored let approvals: ApprovalService?
    /// Called with the first pending item so the glasses can show it; nil when the queue is empty.
    @ObservationIgnored var onFront: ((InboxItem?) -> Void)?

    init(api: AssAPI = AssAPI(), approvals: ApprovalService?) {
        self.api = api
        self.approvals = approvals
        paired = Keychain.loadASSToken() != nil
    }

    /// `denlens://pair?code=ABCD234567`, the link in the dashboard's pairing QR.
    nonisolated static func pairingCode(from url: URL) -> String? {
        guard url.scheme == "denlens", url.host == "pair",
              let code = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "code" })?.value,
              (6...32).contains(code.count), code.allSatisfy({ $0.isLetter || $0.isNumber }) else { return nil }
        return code.uppercased()
    }

    func pair(code: String) async {
        guard let approvals else { return status = ApprovalError.secureEnclaveUnavailable.localizedDescription }
        do {
            try await api.pair(code: code, registration: await approvals.registration(), pushToken: pushToken)
            paired = true
            status = "Paired. Approvals will arrive here and as notifications."
            await refresh()
        } catch {
            status = error.localizedDescription
        }
    }

    func didRegister(pushToken hex: String) {
        pushToken = hex
        if paired { Task { try? await api.setPushToken(hex) } }
    }

    func refresh() async {
        guard paired else { return }
        do {
            items = try await api.inbox()
            status = items.isEmpty ? "No pending approvals." : ""
        } catch {
            status = error.localizedDescription
        }
        onFront?(items.first)
    }

    /// Signs with the Secure Enclave key (Face ID prompt) and posts the decision.
    func decide(_ item: InboxItem, approve: Bool) async {
        guard let approvals, !busy else { return }
        busy = true
        defer { busy = false }
        do {
            guard !item.isExpired() else { throw ApprovalError.expired }
            let word = approve ? "approve" : "deny"
            let message = ApprovalMessage.preimage(requestID: item.requestId, payloadDigest: item.payloadDigest, decision: word, expiresAt: item.signExpiresAt)
            let signature = try await approvals.signer.sign(message, reason: "\(approve ? "Approve" : "Deny"): \(item.title)")
            try await api.decide(SignedDecision(requestId: item.requestId, payloadDigest: item.payloadDigest, decision: word,
                                                expiresAt: item.signExpiresAt, deviceId: approvals.signer.deviceID, signature: base64URL(signature)))
            status = approve ? "Approved." : "Denied."
        } catch {
            SentrySDK.capture(error: error)  // includes the wearer cancelling Face ID
            status = error.localizedDescription
        }
        await refresh()
    }
}

/// Registers for pushes and routes them to the inbox. The push carries a summary only; the request is fetched fresh.
final class PushDelegate: NSObject, UIApplicationDelegate, @preconcurrency UNUserNotificationCenterDelegate, @unchecked Sendable {
    nonisolated(unsafe) static var inbox: ApproverInbox?

    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        // The permission prompt would sit over the app while unit tests drive the mock glasses.
        guard ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] == nil else { return true }
        UNUserNotificationCenter.current().delegate = self
        Task {
            let granted = (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])) ?? false
            if granted { await MainActor.run { application.registerForRemoteNotifications() } }
        }
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let hex = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { @MainActor in Self.inbox?.didRegister(pushToken: hex) }
    }

    func application(_ application: UIApplication, didReceiveRemoteNotification userInfo: [AnyHashable: Any], fetchCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void) {
        Task {
            await Self.inbox?.refresh()
            completionHandler(.newData)
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification, withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        Task { await Self.inbox?.refresh() }
        completionHandler([.banner, .sound, .list])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse, withCompletionHandler completionHandler: @escaping () -> Void) {
        Task { await Self.inbox?.refresh() }
        completionHandler()
    }
}
