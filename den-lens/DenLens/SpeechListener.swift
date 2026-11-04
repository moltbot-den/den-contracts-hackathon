import AVFoundation
import Foundation
import MWDATCore
import MWDATSpeech
import Speech

enum SpeechError: LocalizedError {
    case notAuthorized
    case unavailable
    case nothingHeard
    /// The glasses mic failed before hearing anything: the caller falls back to the phone mic.
    case glassesMicFailed

    var errorDescription: String? {
        switch self {
        case .notAuthorized: "Microphone or speech recognition permission was denied."
        case .unavailable: "Speech recognition is unavailable right now."
        case .nothingHeard: "Didn't hear anything. Try again."
        case .glassesMicFailed: "The glasses microphone is unavailable."
        }
    }
}

/// One utterance from the phone microphone via on-device Apple Speech when available.
/// Starts only when `listen` is called and fully releases the audio session afterwards.
/// Not the glasses mic: routing input to the glasses over Bluetooth HFP drops the DAT display
/// link within a second (Sentry, 2026-10-28: every display send then fails "not connected").
/// ponytail: phone mic; DAT Speech (`session.addSpeech()`, glasses mic, experimental) is the upgrade.
@MainActor
final class SpeechListener {
    private var continuation: CheckedContinuation<String, Error>?
    private var engine: AVAudioEngine?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var silence: Task<Void, Never>?
    private var cap: Task<Void, Never>?
    private var latest = ""
    private var onPartial: (@MainActor (String) -> Void)?
    private var glasses: Speech?
    private var glassesTokens: [AnyListenerToken] = []

    /// One utterance from the glasses microphone: DAT Speech recognizes on the glasses, so no phone audio
    /// session is touched. Same end-of-utterance rules as the phone path.
    func listen(glasses speech: Speech, onPartial: @escaping @MainActor (String) -> Void) async throws -> String {
        self.onPartial = onPartial
        latest = ""
        glasses = speech
        Log.info("speech", "listening on the glasses mic")
        return try await withCheckedThrowingContinuation { cont in
            continuation = cont
            glassesTokens = [
                speech.transcriptionPublisher.listen { result in
                    Task { @MainActor [weak self] in self?.handle(text: result.text, isFinal: result.isFinal, failed: false) }
                },
                speech.errorPublisher.listen { error in
                    Task { @MainActor [weak self] in
                        Log.info("speech", "glasses mic error: \(error)")
                        guard let self else { return }
                        if self.latest.isEmpty { self.finish(.failure(SpeechError.glassesMicFailed)) } else { self.finish(nil) }
                    }
                },
            ]
            speech.start()
            silence = Task { [weak self] in
                try? await Task.sleep(for: .seconds(8))
                self?.finish(.failure(SpeechError.nothingHeard), onlyIfEmpty: true)
            }
            cap = Task { [weak self] in
                try? await Task.sleep(for: .seconds(15))
                self?.finish(nil)
            }
        }
    }

    func listen(onPartial: @escaping @MainActor (String) -> Void) async throws -> String {
        guard await Self.authorize() else { throw SpeechError.notAuthorized }
        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US")), recognizer.isAvailable else { throw SpeechError.unavailable }

        let audio = AVAudioSession.sharedInstance()
        try audio.setCategory(.record, mode: .measurement)
        try audio.setActive(true)
        if let builtIn = audio.availableInputs?.first(where: { $0.portType == .builtInMic }) {
            try audio.setPreferredInput(builtIn)
        }
        Log.info("speech", "listening; on-device \(recognizer.supportsOnDeviceRecognition); input \(audio.currentRoute.inputs.map(\.portType.rawValue))")

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition
        request.contextualStrings = ["Den", "Hey Den", "Moltbot Den", "budget", "dollars"]
        self.request = request
        self.onPartial = onPartial
        latest = ""

        let engine = AVAudioEngine()
        self.engine = engine
        let input = engine.inputNode
        nonisolated(unsafe) let req = request
        input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { @Sendable buffer, _ in req.append(buffer) }
        engine.prepare()
        try engine.start()

        return try await withCheckedThrowingContinuation { cont in
            continuation = cont
            task = recognizer.recognitionTask(with: request) { @Sendable [weak self] result, error in
                let text = result?.bestTranscription.formattedString
                let isFinal = result?.isFinal ?? false
                let failed = error != nil
                Task { @MainActor in self?.handle(text: text, isFinal: isFinal, failed: failed) }
            }
            // No speech at all within 8 s, or an utterance longer than 15 s, ends the capture.
            silence = Task { [weak self] in
                try? await Task.sleep(for: .seconds(8))
                self?.finish(.failure(SpeechError.nothingHeard), onlyIfEmpty: true)
            }
            cap = Task { [weak self] in
                try? await Task.sleep(for: .seconds(15))
                self?.finish(nil)
            }
        }
    }

    func cancel() { finish(.failure(CancellationError())) }

    private func handle(text: String?, isFinal: Bool, failed: Bool) {
        if let text, !text.isEmpty, text != latest {
            latest = text
            onPartial?(text)
            // End of utterance: 1.6 s without the transcript changing.
            silence?.cancel()
            silence = Task { [weak self] in
                try? await Task.sleep(for: .seconds(1.6))
                self?.finish(nil)
            }
        }
        if isFinal || failed { finish(nil) }
    }

    private func finish(_ failure: Result<String, Error>?, onlyIfEmpty: Bool = false) {
        guard let cont = continuation else { return }
        if onlyIfEmpty && !latest.isEmpty { return }
        continuation = nil
        Log.info("speech", "stopped; \(latest.count) chars; \(failure.map { "\($0)" } ?? "end of utterance")")
        silence?.cancel()
        cap?.cancel()
        engine?.inputNode.removeTap(onBus: 0)
        engine?.stop()
        request?.endAudio()
        task?.cancel()
        engine = nil; request = nil; task = nil; onPartial = nil
        if let glasses {
            glasses.stop()
            glassesTokens = []
            self.glasses = nil
        } else {
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        }
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
