import AVFoundation
import Foundation
import Sentry
import Speech

enum SpeechError: LocalizedError {
    case notAuthorized
    case unavailable
    case nothingHeard

    var errorDescription: String? {
        switch self {
        case .notAuthorized: "Microphone or speech recognition permission was denied."
        case .unavailable: "Speech recognition is unavailable right now."
        case .nothingHeard: "Didn't hear anything. Try again."
        }
    }
}

/// One utterance from the phone microphone via Apple Speech.
/// Starts only when `listen` is called and fully releases the audio session afterwards.
/// Not the glasses mic: routing input to the glasses over Bluetooth HFP drops the DAT display
/// link within a second (Sentry, 2026-10-28: every display send then fails "not connected").
/// Hands audio buffers from the realtime tap to whichever recognition request is current.
private final class RequestBox: @unchecked Sendable {
    private let lock = NSLock()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var buffers = 0
    private var peak: Float = 0

    func set(_ new: SFSpeechAudioBufferRecognitionRequest?) {
        lock.lock(); defer { lock.unlock() }
        request = new
    }

    func append(_ buffer: AVAudioPCMBuffer) {
        var loudest: Float = 0
        if let samples = buffer.floatChannelData?[0] {
            for i in 0..<Int(buffer.frameLength) { loudest = max(loudest, abs(samples[i])) }
        }
        lock.lock(); defer { lock.unlock() }
        request?.append(buffer)
        buffers += 1
        peak = max(peak, loudest)
    }

    /// Audio seen since the last call: how many buffers and the loudest sample (0 to 1).
    func takeLevel() -> (buffers: Int, peak: Float) {
        lock.lock(); defer { lock.unlock() }
        defer { buffers = 0; peak = 0 }
        return (buffers, peak)
    }
}

/// ponytail: phone mic; DAT Speech (`session.addSpeech()`, glasses mic, experimental) is the upgrade.
@MainActor
final class SpeechListener {
    /// Runs `action` after `seconds` unless cancelled. (`try? await Task.sleep` swallows cancellation, so a cancelled timer fired at once.)
    static func after(_ seconds: Double, _ action: @escaping @MainActor () -> Void) -> Task<Void, Never> {
        Task { @MainActor in
            do { try await Task.sleep(for: .seconds(seconds)) } catch { return }
            action()
        }
    }

    private var continuation: CheckedContinuation<String, Error>?
    private var engine: AVAudioEngine?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var silence: Task<Void, Never>?
    private var cap: Task<Void, Never>?
    private var latest = ""
    private let box = RequestBox()
    private var recognizer: SFSpeechRecognizer?
    /// Words kept from recognition passes that ended early; `generation` drops callbacks from old passes.
    private var committed = ""
    private var generation = 0
    private var restarts = 0
    private var routeObserver: NSObjectProtocol?
    private var onPartial: (@MainActor (String) -> Void)?

    func listen(onPartial: @escaping @MainActor (String) -> Void) async throws -> String {
        guard await Self.authorize() else { throw SpeechError.notAuthorized }
        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US")), recognizer.isAvailable else { throw SpeechError.unavailable }

        let audio = AVAudioSession.sharedInstance()
        // Phone mic only: activating the glasses' Bluetooth mic (HFP) drops the DAT display session within seconds.
        try audio.setCategory(.record, mode: .default)
        try audio.setActive(true, options: .notifyOthersOnDeactivation)
        let inputs = audio.availableInputs ?? []
        if let mic = inputs.first(where: { $0.portType == .builtInMic }) {
            try? audio.setPreferredInput(mic)  // best effort: failing here must not leave the session active
        }
        Log.info("speech", "listening; available \(inputs.map { "\($0.portType.rawValue):\($0.portName)" }); input \(audio.currentRoute.inputs.map(\.portName))")

        self.recognizer = recognizer
        self.onPartial = onPartial
        latest = ""
        committed = ""
        restarts = 0

        let engine = AVAudioEngine()
        self.engine = engine
        installTap(on: engine)
        // A Bluetooth route change stops the engine silently, which ends the capture mid-sentence.
        routeObserver = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main) { @Sendable [weak self] _ in
            Task { @MainActor in self?.restartAfterRouteChange() }
        }
        engine.prepare()
        do { try engine.start() } catch {
            // Nothing is listening yet, so finish() would not run: release the tap, observer and audio session here.
            engine.inputNode.removeTap(onBus: 0)
            if let routeObserver { NotificationCenter.default.removeObserver(routeObserver) }
            routeObserver = nil
            self.engine = nil
            try? audio.setActive(false, options: .notifyOthersOnDeactivation)
            throw error
        }

        return try await withCheckedThrowingContinuation { cont in
            continuation = cont
            startRecognition()
            // No speech at all within 8 s, or an utterance longer than 15 s, ends the capture.
            silence = SpeechListener.after(8) { [weak self] in self?.finish(.failure(SpeechError.nothingHeard), onlyIfEmpty: true) }
            cap = SpeechListener.after(15) { [weak self] in self?.finish(nil) }
        }
    }

    private func installTap(on engine: AVAudioEngine) {
        let input = engine.inputNode
        let box = self.box
        input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { @Sendable buffer, _ in box.append(buffer) }
    }

    private func restartAfterRouteChange() {
        guard let engine else { return }
        Log.info("speech", "route changed; input \(AVAudioSession.sharedInstance().currentRoute.inputs.map(\.portType.rawValue))")
        engine.inputNode.removeTap(onBus: 0)
        installTap(on: engine)
        engine.prepare()
        do {
            try engine.start()
            committed = latest
            request?.endAudio()
            startRecognition()
        } catch {
            Log.info("speech", "engine restart failed: \(error)")
            finish(nil)
        }
    }

    /// One recognition pass. The mic keeps running across passes, so a pass that ends early costs no audio session.
    private func startRecognition() {
        guard let recognizer else { return }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.contextualStrings = ["Den", "Hey Den", "Moltbot Den", "budget", "dollars"]
        self.request = request
        box.set(request)
        generation += 1
        let pass = generation
        task = recognizer.recognitionTask(with: request) { @Sendable [weak self] result, error in
            let text = result?.bestTranscription.formattedString
            let isFinal = result?.isFinal ?? false
            let reason = error.map { "\($0)" }
            Task { @MainActor in
                guard let self, pass == self.generation else { return }
                if let reason { Log.info("speech", "pass \(pass) error: \(reason)") }
                self.handle(text: text, isFinal: isFinal, failed: reason != nil)
            }
        }
    }

    func cancel() { finish(.failure(CancellationError())) }

    private func handle(text: String?, isFinal: Bool, failed: Bool) {
        let full = text.flatMap { $0.isEmpty ? nil : (committed.isEmpty ? $0 : committed + " " + $0) }
        if let full, full != latest {
            latest = full
            onPartial?(full)
            // End of utterance: 2.5 s without the transcript changing.
            silence?.cancel()
            silence = SpeechListener.after(2.5) { [weak self] in self?.finish(nil) }
        }
        guard isFinal || failed else { return }
        Log.info("speech", "pass ended; final \(isFinal); failed \(failed); \(latest.count) chars")
        // The phone recognizer can end a pass after a word or two while the wearer is still talking (seen on
        // iOS 27.2). Keep the words and listen on; only the silence and length timers end the capture.
        if engine != nil, restarts < 10 {
            restarts += 1
            committed = latest
            request?.endAudio()
            startRecognition()
        } else {
            finish(nil)
        }
    }

    private func finish(_ failure: Result<String, Error>?, onlyIfEmpty: Bool = false) {
        guard let cont = continuation else { return }
        if onlyIfEmpty && !latest.isEmpty { return }
        continuation = nil
        Log.info("speech", "stopped; \(latest.count) chars; \(failure.map { "\($0)" } ?? "end of utterance")")
        let level = box.takeLevel()
        Log.info("speech", "audio; \(level.buffers) buffers; peak \(level.peak)")
        SentrySDK.capture(message: "speech capture ended: \(latest.count) chars")  // ponytail: demo diagnostics; drop once speech is proven
        silence?.cancel()
        cap?.cancel()
        engine?.inputNode.removeTap(onBus: 0)
        engine?.stop()
        request?.endAudio()
        task?.cancel()
        generation += 1
        box.set(nil)
        if let routeObserver { NotificationCenter.default.removeObserver(routeObserver) }
        routeObserver = nil
        engine = nil; request = nil; task = nil; onPartial = nil; recognizer = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        switch failure {
        case .failure(let e): cont.resume(throwing: e)
        case .success(let s): cont.resume(returning: s)
        case nil:
            if latest.isEmpty { cont.resume(throwing: SpeechError.nothingHeard) } else { cont.resume(returning: latest) }
        }
    }

    /// Apple calls these back on its own queues. In this @MainActor class an unannotated closure is
    /// main-actor isolated, and Swift 6 traps when it runs elsewhere (the crash on first Ask Den), so
    /// every system callback here is nonisolated or @Sendable.
    nonisolated static func authorize() async -> Bool {
        let speech = await withCheckedContinuation { c in
            SFSpeechRecognizer.requestAuthorization { @Sendable status in c.resume(returning: status == .authorized) }
        }
        guard speech else { Log.info("speech", "speech recognition permission denied"); return false }
        let mic = await AVAudioApplication.requestRecordPermission()
        Log.info("speech", "microphone permission \(mic ? "granted" : "denied")")
        return mic
    }
}
