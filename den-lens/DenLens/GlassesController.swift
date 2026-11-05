import Foundation
import MWDATCamera
import MWDATCore
import MWDATDisplay
import MWDATInputs
import MWDATMockDevice
import Observation
import Sentry
import UIKit

enum GlassesError: LocalizedError {
    case notConnected
    case cameraUnavailable
    case permissionDenied
    case timeout(String)
    case photo(String)

    var errorDescription: String? {
        switch self {
        case .notConnected: "Glasses are not connected."
        case .cameraUnavailable: "The glasses camera is not available."
        case .permissionDenied: "Camera permission was not granted in the Meta AI app."
        case .timeout(let what): "Timed out waiting for \(what)."
        case .photo(let msg): "Photo capture failed: \(msg)"
        }
    }
}

private enum PhotoOutcome: Sendable {
    case started
    case data(Data)
    case failed(String)
}

/// Owns the DAT session: one DeviceSession with Display attached for its whole life, and a
/// Camera whose standalone Photo capability is started only for the duration of one explicit capture.
@Observable
@MainActor
final class GlassesController {
    private(set) var status = "Not connected"
    private(set) var registration: RegistrationState = .unavailable
    private(set) var isReady = false
    private(set) var usingMock = false
    private(set) var mockPreview: UIView?
    var errorMessage: String? {
        didSet { if let errorMessage { Log.info("glasses", "error: \(errorMessage)"); SentrySDK.capture(message: "glasses: \(errorMessage)") } }
    }

    @ObservationIgnored private let wearables: WearablesInterface
    @ObservationIgnored private var session: DeviceSession?
    @ObservationIgnored private var display: Display?
    @ObservationIgnored private var camera: Camera?
    @ObservationIgnored private var displayToken: AnyListenerToken?
    @ObservationIgnored private var errorTask: Task<Void, Never>?
    @ObservationIgnored private var stateTask: Task<Void, Never>?
    @ObservationIgnored private var registrationTask: Task<Void, Never>?
    @ObservationIgnored private var mockGlasses: (any MockGlasses)?
    @ObservationIgnored private var inputsTask: Task<Void, Never>?
    @ObservationIgnored private var inputsToken: AnyListenerToken?
    /// Inputs drops events until it is active (activation is asynchronous).
    @ObservationIgnored private(set) var inputsActive = false
    @ObservationIgnored private var voice: VoiceInvocationsStream?
    @ObservationIgnored private var voiceTokens: [AnyListenerToken] = []
    @ObservationIgnored private var voiceTask: Task<Void, Never>?
    /// The capture or action button on the glasses (Inputs), and "Hey Meta, start Den" (Voice Invocations).
    @ObservationIgnored var onAsk: (@MainActor () -> Void)?
    @ObservationIgnored var onDescribe: (@MainActor () -> Void)?

    // Throttled display path.
    @ObservationIgnored private var throttle = SendThrottle(interval: 2)
    @ObservationIgnored private var pending: FlexBox?
    @ObservationIgnored private var deferTask: Task<Void, Never>?
    @ObservationIgnored private var sendTail: Task<Void, Never>?
    @ObservationIgnored private(set) var sentCount = 0

    init(wearables: WearablesInterface = Wearables.shared) {
        self.wearables = wearables
        registration = wearables.registrationState
        registrationTask = Task { [weak self] in
            for await state in wearables.registrationStateStream() {
                Log.info("glasses.registration", "\(state)")
                self?.registration = state
            }
        }
        listenForVoiceLaunch()
    }

    // MARK: Hands-free

    /// "Hey Meta, start Den" opens the app and starts an ask. Needs no session, only a known device, so it
    /// starts as soon as the glasses are seen. Experimental: needs Voice Invocations enabled in Wearables Developer Center.
    private func listenForVoiceLaunch() {
        let selector = AutoDeviceSelector(wearables: wearables)
        voiceTask = Task { [weak self] in
            for await device in selector.activeDeviceStream() {
                guard let device, let self else { continue }
                do {
                    if self.voice == nil {
                        let stream = try VoiceInvocationsStream(wearables: self.wearables)
                        self.voiceTokens = [
                            stream.invocationsPublisher.listen { invocation in
                                guard let launch = invocation as? LaunchApp else { return }
                                Task { @MainActor [weak self] in
                                    Log.info("glasses.voice", "launch")
                                    _ = await launch.responseHandle.sendSuccess(actionOutput: nil)
                                    self?.onAsk?()
                                }
                            },
                            stream.errorPublisher.listen { error in Log.info("glasses.voice", "error: \(error)") },
                        ]
                        self.voice = stream
                    }
                    try self.voice?.start(deviceIdentifier: device)
                    Log.info("glasses.voice", "listening on \(device)")
                } catch {
                    Log.info("glasses.voice", "unavailable: \(error)")
                }
            }
        }
    }

    /// Capture or action button press starts an ask. Pinch and swipe already drive the display's own buttons.
    private func attachInputs(_ session: DeviceSession) {
        do {
            // Physical buttons only. Subscribing to the band or touchpad sources takes those events away from the display's own buttons.
            let config = InputsConfiguration(sources: [.captureButton, .actionButton], consumeBack: false)
            guard let inputs = try session.addInputs(configuration: config) else { return }
            let events = inputs.events
            inputsToken = inputs.statePublisher.listen { state in
                Log.info("glasses.inputs", "\(state)")
                Task { @MainActor [weak self] in self?.inputsActive = state == .active }
            }
            inputsTask = Task { [weak self] in
                for await event in events {
                    switch event {
                    case .capture(.shortPress, _, _), .button(.action, _, _):
                        Log.info("glasses.inputs", "ask button")
                        self?.onAsk?()
                    case .capture(.hold, _, _):
                        Log.info("glasses.inputs", "describe button")
                        self?.onDescribe?()
                    case .nav, .select, .back, .button, .capture, .drag:
                        Log.info("glasses.inputs", "ignored \(event)")
                    @unknown default:
                        break
                    }
                }
            }
            Log.info("glasses.inputs", "attached")
        } catch {
            Log.info("glasses.inputs", "unavailable: \(error)")  // not enabled in WDC: the phone and lens buttons still work
        }
    }

    // MARK: Registration

    func register() async {
        do { try await wearables.startRegistration() } catch { errorMessage = error.localizedDescription }
    }

    func handle(url: URL) {
        guard URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.contains(where: { $0.name == "metaWearablesAction" }) == true else { return }
        Task {
            do { _ = try await wearables.handleUrl(url) } catch { errorMessage = error.localizedDescription }
        }
    }

    // MARK: Mock device (simulator and demo without glasses)

    /// `photoFile` is the JPEG the mock camera returns on capture.
    func enableMock(photoFile: URL) throws {
        MockDeviceKit.shared.enable()
        let glasses = try MockDeviceKit.shared.pairGlasses(model: .metaRayBanDisplay)
        glasses.powerOn()
        glasses.unfold()
        glasses.don()
        glasses.services.cameraCapture.setCapturedPhoto(fileURL: photoFile)
        mockGlasses = glasses
        mockPreview = glasses.services.display.createPreviewView()
        usingMock = true
    }

    var mockGlassesForTests: (any MockGlasses)? { mockGlasses }

    func disableMock() {
        MockDeviceKit.shared.disable()
        mockGlasses = nil
        mockPreview = nil
        usingMock = false
    }

    // MARK: Session

    func connect() async throws {
        if isReady { return }
        status = "Connecting"
        Log.info("glasses", "connect; registration \(registration); mock \(usingMock)")
        let selector = AutoDeviceSelector(wearables: wearables, filter: { $0.supportsDisplay() })
        try await waitForDevice(selector)
        Log.info("glasses", "device \(selector.activeDevice.map { "\($0)" } ?? "none")")

        let session = try wearables.createSession(deviceSelector: selector)
        self.session = session
        errorTask = Task { [weak self] in
            for await error in session.errorStream() {
                self?.errorMessage = error.localizedDescription
            }
        }
        // A session that stops on its own (glasses off, out of range, link dropped) is torn down,
        // so the next Ask Den reconnects instead of failing against a dead session.
        stateTask = Task { [weak self] in
            for await state in session.stateStream() {
                Log.info("glasses.session", "\(state)")
                guard state == .stopped, let self, self.session === session else { continue }
                self.teardown()
                self.status = "Disconnected"
            }
        }

        let sessionStates = session.stateStream()
        let started = Task { await Self.wait(for: .started, in: sessionStates) }
        do { try session.start() } catch DeviceSessionError.datAppOnTheGlassesUpdateRequired {
            started.cancel()
            teardown()
            throw DeviceSessionError.datAppOnTheGlassesUpdateRequired
        }
        try await within(seconds: 20, "the glasses session") { await started.value }

        let display = try session.addDisplay()
        self.display = display
        let (states, cont) = AsyncStream.makeStream(of: DisplayState.self)
        displayToken = display.statePublisher.listen { state in
            Log.info("glasses.display", "\(state)")
            cont.yield(state)
        }
        display.start()
        try await within(seconds: 15, "the glasses display") { await Self.wait(for: .started, in: states) }
        attachInputs(session)
        isReady = true
        status = usingMock ? "Connected (simulated glasses)" : "Connected"
    }

    func disconnect() {
        deferTask?.cancel()
        pending = nil
        teardown()
        status = "Not connected"
    }

    private func teardown() {
        display?.stop()
        session?.stop()
        errorTask?.cancel()
        errorTask = nil
        stateTask?.cancel()
        stateTask = nil
        displayToken = nil
        inputsTask?.cancel()
        inputsTask = nil
        inputsToken = nil
        inputsActive = false
        display = nil
        camera = nil
        session = nil
        isReady = false
    }

    private func waitForDevice(_ selector: AutoDeviceSelector) async throws {
        if selector.activeDevice != nil { return }
        try await within(seconds: 10, "a connected display-capable pair of glasses") {
            await Self.waitForNonNil(selector.activeDeviceStream())
        }
    }

    // MARK: Capture

    /// Captures exactly one photo. Starts the Photo capability, captures, and stops it again.
    /// Returns raw bytes (in memory only); the caller downsizes and discards them.
    func capturePhoto() async throws -> Data {
        guard let session, isReady else { throw GlassesError.notConnected }
        var permission = try await wearables.checkPermissionStatus(.camera)
        if permission != .granted { permission = try await wearables.requestPermission(.camera) }
        Log.info("glasses.camera", "permission \(permission); session \(session.state)")
        guard permission == .granted else { throw GlassesError.permissionDenied }

        if camera == nil { camera = try session.addCamera() }
        Log.info("glasses.camera", camera?.photo == nil ? "no photo capability" : "photo capability ready")
        guard let photo = camera?.photo else { throw GlassesError.cameraUnavailable }

        let bag = ListenerTokenBag()
        let (events, cont) = AsyncStream.makeStream(of: PhotoOutcome.self)
        photo.statePublisher.listen { state in
            Log.info("glasses.photo", "\(state)")
            if state == .started { cont.yield(.started) }
        }.store(in: bag)
        photo.photoDataPublisher.listen { cont.yield(.data($0.imageData)) }.store(in: bag)
        photo.errorPublisher.listen { error in
            Log.info("glasses.photo", "error: \(error)")
            cont.yield(.failed(error.localizedDescription))
        }.store(in: bag)
        let watchdog = Task {
            do { try await Task.sleep(for: .seconds(30)) } catch { return }  // cancelled: the capture already ended
            cont.yield(.failed("timed out"))
        }
        defer {
            watchdog.cancel()
            cont.finish()
            photo.stop()
            bag.clear()
        }

        photo.start()
        var requested = false
        for await outcome in events {
            switch outcome {
            case .started where !requested:
                requested = true
                photo.capturePhoto(resolution: .medium, quality: .medium)
            case .started: break
            case .data(let d): return d
            case .failed(let m): throw GlassesError.photo(m)
            }
        }
        throw GlassesError.photo("cancelled")
    }

    // MARK: Display

    /// `throttled: true` is for live status updates: at most one send per 2 s, newest wins.
    /// User-driven cards (confirm, next/back) go out immediately but still reset the 2 s window.
    func show(_ card: FlexBox, throttled: Bool = false, now: Date = Date()) {
        guard isReady else { return }
        guard throttled else {
            deferTask?.cancel()
            pending = nil
            throttle.didSend(at: now)
            enqueue(card)
            return
        }
        switch throttle.request(at: now) {
        case .sendNow:
            pending = nil
            throttle.didSend(at: now)
            enqueue(card)
        case .coalesce:
            pending = card
        case .deferUntil(let when):
            pending = card
            deferTask = Task { [weak self] in
                try? await Task.sleep(for: .seconds(max(0, when.timeIntervalSinceNow)))
                guard !Task.isCancelled, let self, let latest = self.pending else { return }
                self.pending = nil
                self.throttle.didSend(at: Date())
                self.enqueue(latest)
            }
        }
    }

    private func enqueue(_ card: FlexBox) {
        let previous = sendTail
        sendTail = Task { [weak self] in
            await previous?.value
            guard let self, let display = self.display else { return }
            do {
                try await display.send(card)
                self.sentCount += 1
            } catch {
                self.errorMessage = (error as? DisplayError)?.description ?? error.localizedDescription
            }
        }
    }

    /// Waits for the last queued display send; used by tests.
    func flushSends() async { await sendTail?.value }

    // MARK: Helpers

    nonisolated private static func wait<S: AsyncSequence & Sendable>(for target: S.Element, in stream: S) async -> Bool where S.Element: Equatable & Sendable {
        do { for try await s in stream where s == target { return true } } catch {}
        return false
    }

    nonisolated private static func waitForNonNil<S: AsyncSequence & Sendable, T: Sendable>(_ stream: S) async -> Bool where S.Element == T? {
        do { for try await s in stream where s != nil { return true } } catch {}
        return false
    }

    private func within(seconds: Double, _ what: String, _ work: @escaping @Sendable () async -> Bool) async throws {
        let ok = await withTaskGroup(of: Bool.self) { group in
            group.addTask { await work() }
            group.addTask { try? await Task.sleep(for: .seconds(seconds)); return false }
            let first = await group.next() ?? false
            group.cancelAll()
            return first
        }
        if !ok { throw GlassesError.timeout(what) }
    }
}
