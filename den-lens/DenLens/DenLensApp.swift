import MWDATCore
import Sentry
import SwiftUI

@main
struct DenLensApp: App {
    @UIApplicationDelegateAdaptor(PushDelegate.self) private var pushDelegate
    @State private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase

    init() {
        // Crashes, hangs and flow errors go to Sentry (moltbot-den/den-lens). No PII, no screenshots.
        SentrySDK.start { o in
            o.dsn = "https://5ca62065314ba50139ab317a00b16d22@o4511101269180416.ingest.us.sentry.io/4512331167629312"
            o.sendDefaultPii = false
        }
        do { try Wearables.configure() } catch {
            NSLog("[DenLens] Wearables.configure failed: \(error)")
        }
        let model = AppModel()
        PushDelegate.inbox = model.inbox
        _model = State(wrappedValue: model)
    }

    var body: some Scene {
        WindowGroup {
            ContentView(model: model)
                .onOpenURL { url in
                    // The dashboard QR is denlens://pair?code=...; Meta's callback carries metaWearablesAction.
                    if let code = ApproverInbox.pairingCode(from: url) { Task { await model.inbox.pair(code: code) } }
                    else { model.glasses.handle(url: url) }
                }
                // Ask for speech and microphone on first launch, like any voice app; later launches return at once.
                .task { _ = await SpeechListener.authorize() }
                .onChange(of: scenePhase) { _, phase in if phase == .active { Task { await model.inbox.refresh() } } }
        }
    }
}

@Observable
@MainActor
final class AppModel {
    let glasses = GlassesController()
    let flow: LensFlow
    let approvals = ApprovalService.live()
    let inbox: ApproverInbox
    var hasKey = Keychain.loadAPIKey() != nil
    var approverStatus = ""
    var mockError: String?
    @ObservationIgnored private var mockFile: URL?

    init() {
        let approvals = approvals
        flow = LensFlow(glasses: glasses, approvals: approvals)
        inbox = ApproverInbox(approvals: approvals)
        inbox.onFront = { [weak self] item in self?.showOnGlasses(item) }
        approverStatus = Self.status(available: approvals != nil)
        glasses.onAsk = { [weak self] in self?.flow.start() }
        glasses.onDescribe = { [weak self] in self?.flow.describe() }
        Task { [weak self] in  // crewmates' photo requests; the flow shows them only when the lens is idle
            while self != nil {
                if self?.hasKey == true { await self?.flow.checkPhotoRequests() }
                try? await Task.sleep(for: .seconds(10))
            }
        }
    }

    /// The glasses show the oldest pending request when nothing else is on them. Approving is a pinch, then Face ID on the phone.
    private func showOnGlasses(_ item: InboxItem?) {
        guard glasses.isReady, case .idle = flow.phase else { return }
        guard let item else { return flow.reset() }
        glasses.show(Cards.inboxRequest(
            title: item.title, lines: item.lines, pending: inbox.items.count,
            onApprove: { [weak self] in Task { await self?.inbox.decide(item, approve: true) } },
            onDeny: { [weak self] in Task { await self?.inbox.decide(item, approve: false) } }))
    }

    var canEnrollApprover: Bool { approvals != nil && hasKey && !ApprovalState.enrolled }

    private static func status(available: Bool) -> String {
        !available ? "No Secure Enclave on this device: approvals cannot be signed here."
            : ApprovalState.enrolled ? "Registered. Each approval asks for Face ID."
            : "Not registered. Registering makes every plan approval need Face ID, and mainnet spend requires it."
    }

    func enrollApprover() async {
        do {
            try await approvals?.ensureRegistered()
            approverStatus = Self.status(available: true)
        } catch {
            approverStatus = error.localizedDescription
        }
    }

    func saveKey(_ key: String) {
        Keychain.saveAPIKey(key)
        hasKey = Keychain.loadAPIKey() != nil
    }

    /// Simulated Ray-Ban Display for the simulator or a demo without glasses. Its photo is a synthetic image.
    func setMock(_ on: Bool) {
        mockError = nil
        flow.cancel()
        glasses.disconnect()
        if let mockFile { try? FileManager.default.removeItem(at: mockFile) }
        mockFile = nil
        guard on else { return glasses.disableMock() }
        do {
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("denlens-mock-\(UUID().uuidString).jpg")
            let img = UIGraphicsImageRenderer(size: CGSize(width: 800, height: 600)).image { ctx in
                UIColor.darkGray.setFill()
                ctx.fill(CGRect(x: 0, y: 0, width: 800, height: 600))
                UIColor.orange.setFill()
                ctx.fill(CGRect(x: 250, y: 200, width: 300, height: 200))
            }
            try img.jpegData(compressionQuality: 0.8)?.write(to: url)
            mockFile = url
            try glasses.enableMock(photoFile: url)
        } catch {
            mockError = error.localizedDescription
        }
    }
}
