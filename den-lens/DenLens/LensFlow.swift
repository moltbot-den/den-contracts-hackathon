import AVFoundation
import Foundation
import Observation
import Sentry
import UIKit

enum LensPhase: Equatable {
    case idle
    case connecting
    case listening(partial: String)
    case capturing
    case understanding(utterance: String)
    case answered(LensAnswer, replies: [CrewReply])
    case confirm(LensIntent)
    case hiring
    case approve(ApprovalRequest, Contract, note: String)
    case signing
    case running(Contract, note: String)
    case guide(pages: [GuidePage], index: Int)
    case photoRequest(PhotoRequest)
    case finished(spentCents: Int, refundCents: Int)
    case failed(String)

    var isBusy: Bool {
        switch self {
        case .idle, .finished, .failed, .confirm, .guide, .answered, .approve, .photoRequest: false
        default: true
        }
    }
}

/// The whole wearer journey: trigger -> utterance -> one frame -> intent -> confirm -> contract -> live status -> guide.
/// Every step renders to the glasses (via `Cards`) and to the phone (via `phase`).
@Observable
@MainActor
final class LensFlow {
    private(set) var phase: LensPhase = .idle {
        // Case name only: the breadcrumb trail shows the step a crash happened in, never what was said.
        didSet { Log.info("lens.phase", String(String(describing: phase).prefix { $0 != "(" })) }
    }
    private(set) var budgetCents = 0
    private(set) var contractID: String?
    /// Live mode: nil when off, else the latest warning from Den (empty when all is well).
    private(set) var live: String?

    @ObservationIgnored let glasses: GlassesController
    @ObservationIgnored let api: LensAPI
    @ObservationIgnored let approvals: ApprovalService?
    @ObservationIgnored let speech = SpeechListener()
    @ObservationIgnored private var task: Task<Void, Never>?
    @ObservationIgnored private var watchTask: Task<Void, Never>?
    @ObservationIgnored private var currentContract: Contract?
    @ObservationIgnored private var crewTask: Task<Void, Never>?
    @ObservationIgnored private var liveTask: Task<Void, Never>?
    /// Muse Image diagrams for the current guide, by step index; cleared when a new answer arrives.
    @ObservationIgnored private var diagrams: [Int: UIImage] = [:]
    @ObservationIgnored private var drawing: Set<Int> = []
    @ObservationIgnored private var lastAnswer: LensAnswer?
    @ObservationIgnored private var replies: [CrewReply] = []

    init(glasses: GlassesController, api: LensAPI = LensAPI(), approvals: ApprovalService? = nil) {
        self.glasses = glasses
        self.api = api
        self.approvals = approvals
    }

    // MARK: Trigger

    /// Phone button or the "Ask Den" button on the lens. Capture happens only because of this explicit action.
    func start() {
        guard !phase.isBusy else { return }
        task = Task { await run { try await self.ask() } }
    }

    /// Accessibility: no question needed. Den describes what is in front of the wearer, reads any text, and speaks it.
    func describe() {
        guard !phase.isBusy else { return }
        task = Task { await run { try await self.ask(utterance: Self.describePrompt) } }
    }

    static let describePrompt = "Describe what is in front of me for someone who cannot see it: the main things, " +
        "where they are, and read out any text, labels or signs. Mention anything that could be a hazard."

    /// Spoken answers through the glasses speakers (phone speaker without glasses). Settings can turn it off.
    @ObservationIgnored private let voice = AVSpeechSynthesizer()
    static var readAloud: Bool {
        get { UserDefaults.standard.object(forKey: "readAloud") as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: "readAloud") }
    }

    private func speak(_ text: String) {
        guard Self.readAloud, !text.isEmpty else { return }
        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
        voice.stopSpeaking(at: .immediate)
        voice.speak(AVSpeechUtterance(string: text))
    }

    func cancel() {
        task?.cancel()
        watchTask?.cancel()
        crewTask?.cancel()
        stopLive()
        speech.cancel()
        reset()
    }

    func reset() {
        phase = .idle
        glasses.show(Cards.idle(onAsk: { [weak self] in self?.start() }, onDescribe: { [weak self] in self?.describe() }))
    }

    private func run(_ work: () async throws -> Void) async {
        do { try await work() } catch is CancellationError {
        } catch {
            SentrySDK.capture(error: error)
            fail(error.localizedDescription)
        }
    }

    private func fail(_ message: String) {
        phase = .failed(message)
        glasses.show(Cards.failure(message, onRetry: { [weak self] in self?.start() }))
    }

    // MARK: Ask

    /// `utterance` given (describe mode) skips listening.
    private func ask(utterance given: String? = nil) async throws {
        phase = .connecting
        try await glasses.connect()

        let utterance: String
        if let given {
            utterance = given
        } else {
            phase = .listening(partial: "")
            glasses.show(Cards.message("Listening…", "Ask anything about what you see."))
            utterance = try await listen()
        }

        phase = .capturing
        glasses.show(Cards.message("Looking…"))
        let raw = try await glasses.capturePhoto()
        // The full frame lives only in these locals: downsized in memory, sent once, never written to disk.
        guard let jpeg = FramePrep.jpeg(from: raw, maxSide: 1024) else { throw LensAPIError.badResponse }
        let b64 = jpeg.base64EncodedString()

        phase = .understanding(utterance: utterance)
        glasses.show(Cards.message("Searching…", utterance))
        let answer = try await api.ask(utterance: utterance, imageJpegB64: b64)
        try Task.checkCancellation()

        currentContract = nil
        diagrams = [:]
        lastAnswer = answer
        replies = []
        showAnswer()
        speak(([answer.answer] + answer.steps).joined(separator: ". "))
        if let thread = answer.crew?.threadId { watchCrew(thread) }
    }

    /// The glasses mic first; the phone mic when the glasses can't listen (not enabled, simulated glasses).
    private func listen() async throws -> String {
        let partial: @MainActor (String) -> Void = { [weak self] in self?.phase = .listening(partial: $0) }
        if let mic = glasses.glassesSpeech() {
            do { return try await speech.listen(glasses: mic, onPartial: partial) } catch SpeechError.glassesMicFailed {}
        }
        return try await speech.listen(onPartial: partial)
    }

    // MARK: Answer and crew

    private func showAnswer() {
        guard let answer = lastAnswer else { return reset() }
        phase = .answered(answer, replies: replies)
        var steps: CardAction?
        var hire: CardAction?
        if !answer.steps.isEmpty { steps = { [weak self] in self?.showSteps() } }
        if answer.hire != nil { hire = { [weak self] in self?.offerHire() } }
        glasses.show(Cards.answer(answer, latestReply: replies.last, onSteps: steps, onHire: hire,
                                  onDone: { [weak self] in self?.cancel() }))
    }

    func showSteps() {
        guard let steps = lastAnswer?.steps, !steps.isEmpty else { return }
        showGuide(steps.enumerated().map { GuidePage(title: "Step \($0.offset + 1)", body: $0.element) }, 0)
    }

    func offerHire() {
        guard let intent = lastAnswer?.hire else { return }
        budgetCents = intent.budgetCents
        phase = .confirm(intent)
        let crop = FramePrep.decodeCrop(b64: intent.cropJpegB64, maxSide: 360)
        glasses.show(Cards.confirm(
            goal: intent.goal, budgetCents: intent.budgetCents, crop: crop,
            onHire: { [weak self] in self?.confirm() },
            onCancel: { [weak self] in self?.showAnswer() }
        ))
    }

    /// Polls the crew thread for 10 minutes; each new reply updates the answer card.
    private func watchCrew(_ threadID: String) {
        crewTask?.cancel()
        crewTask = Task {
            for _ in 0..<150 {
                try? await Task.sleep(for: .seconds(4))
                if Task.isCancelled { return }
                guard let latest = try? await api.crewReplies(threadID: threadID), latest.count > replies.count else { continue }
                replies = latest
                Log.info("lens.crew", "\(latest.count) replies")
                if let r = latest.last { speak("\(r.fromAgent) says: \(r.body)") }
                if case .answered = phase { showAnswer() }
            }
        }
    }

    // MARK: Hire

    func confirm() {
        guard case .confirm(let intent) = phase else { return }
        phase = .hiring
        glasses.show(Cards.message("Hiring…"))
        task = Task {
            await run {
                let created = try await self.api.createContract(ContractRequest(
                    goal: intent.goal, budgetCents: intent.budgetCents, acceptanceHints: intent.acceptanceHints, evidence: intent.evidence,
                    requireApproval: self.approvals != nil && ApprovalState.enrolled))
                self.contractID = created.id
                self.watch(created)
            }
        }
    }

    // MARK: Live status

    private func watch(_ initial: Contract) {
        render(initial, note: "")
        watchTask = Task {
            do {
                for try await event in api.events(contractID: initial.id) {
                    let latest = try await api.contract(id: initial.id)
                    if event.event == "end" || latest.isTerminal { break }
                    if latest.awaitingApproval { await askApproval(latest); continue }
                    render(latest, note: Self.describe(event))
                }
            } catch is CancellationError {
                return
            } catch {
                // SSE gave up: fall back to polling so the wearer still gets a result.
            }
            await finishWatching(id: initial.id)
        }
    }

    // MARK: Approval (approval/1.0)

    /// The plan is waiting for the owner. The card shows budget and risk flags; the pinch signs with Face ID.
    private func askApproval(_ contract: Contract, note: String = "") async {
        if case .approve(_, let shown, _) = phase, shown.id == contract.id, note.isEmpty { return }
        guard let approvals else { return fail("This contract needs approval. Approve it from the web or CLI.") }
        do {
            let request = try await approvals.request(contractID: contract.id)
            currentContract = contract
            phase = .approve(request, contract, note: note)
            glasses.show(Cards.approval(
                budgetCents: request.summary.budgetCents, flags: request.flagLabels, signed: request.signatureRequired, note: note,
                onApprove: { [weak self] in self?.approveNow() },
                onDecline: { [weak self] in self?.declineNow() }))
        } catch {
            fail(error.localizedDescription)
        }
    }

    func approveNow() {
        guard case .approve(var request, let contract, _) = phase, let approvals else { return }
        task = Task {
            do {
                if request.isExpired() {  // the card sat too long: show the fresh request before signing it
                    request = try await approvals.request(contractID: contract.id)
                    return await askApproval(contract, note: "Request refreshed. Check it and approve again.")
                }
                phase = .signing
                glasses.show(Cards.message("Confirm with Face ID", "Look at your phone."))
                let updated = try await approvals.approve(contractID: contract.id, request: request)
                render(updated, note: "Approved")
            } catch is CancellationError {
            } catch {
                SentrySDK.capture(error: error)  // includes the wearer cancelling Face ID
                phase = .approve(request, contract, note: error.localizedDescription)
                glasses.show(Cards.approval(
                    budgetCents: request.summary.budgetCents, flags: request.flagLabels, signed: request.signatureRequired,
                    note: error.localizedDescription,
                    onApprove: { [weak self] in self?.approveNow() }, onDecline: { [weak self] in self?.declineNow() }))
            }
        }
    }

    func declineNow() {
        guard case .approve(_, let contract, _) = phase else { return }
        task = Task {
            await run {
                _ = try await self.api.cancelContract(id: contract.id)
                self.watchTask?.cancel()
                self.reset()
            }
        }
    }

    private func render(_ contract: Contract, note: String) {
        currentContract = contract
        phase = .running(contract, note: note)
        glasses.show(Cards.status(contract, budgetCents: budgetCents, note: note), throttled: true)
    }

    private func finishWatching(id: String) async {
        for _ in 0..<60 {
            if Task.isCancelled { return }
            if let c = try? await api.contract(id: id) {
                if c.isTerminal || !(c.deliverable ?? "").isEmpty { return present(c) }
                render(c, note: "")
            }
            try? await Task.sleep(for: .seconds(5))
        }
        fail("The contract is still running. Check the phone for status.")
    }

    static func describe(_ event: SSEEvent) -> String {
        var detail = ""
        if let data = event.data.data(using: .utf8),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            detail = (obj["message"] ?? obj["reason"] ?? obj["verdict"] ?? obj["state"]).map { "\($0)" } ?? ""
        }
        return detail.isEmpty ? event.event : "\(event.event): \(detail)"
    }

    // MARK: Result

    private func present(_ contract: Contract) {
        currentContract = contract
        let pages = GuideSplitter.pages(from: contract.deliverable ?? "")
        if pages.isEmpty { return showFinal(contract) }
        showGuide(pages, 0)
    }

    func showGuide(_ pages: [GuidePage], _ index: Int) {
        phase = .guide(pages: pages, index: index)
        glasses.show(Cards.guide(
            pages[index], index: index, count: pages.count, image: diagrams[index], live: live,
            onWatch: { [weak self] in self?.toggleLive() },
            onBack: { [weak self] in self?.step(-1) },
            onNext: { [weak self] in self?.step(1) }
        ))
        if currentContract == nil, pages[index].imageURL == nil { draw(pages[index], at: index) }
    }

    /// Steps from an answer get a Muse Image diagram, drawn once per step and shown when it arrives.
    /// A failed drawing leaves the text-only step; nothing is retried.
    private func draw(_ page: GuidePage, at index: Int) {
        guard diagrams[index] == nil, !drawing.contains(index) else { return }
        drawing.insert(index)
        let context = lastAnswer?.answer ?? ""
        Task {
            defer { drawing.remove(index) }
            guard let d = try? await api.diagram(step: page.body, context: context),
                  let data = Data(base64Encoded: d.imageJpegB64), let image = UIImage(data: data) else { return }
            diagrams[index] = image
            if case .guide(_, let now) = phase, now == index { rerenderGuide() }
        }
    }

    func step(_ delta: Int) {
        guard case .guide(let pages, let index) = phase else { return }
        let next = index + delta
        if next < 0 { return }
        if next >= pages.count {
            stopLive()
            if let c = currentContract { return showFinal(c) }
            return showAnswer()
        }
        showGuide(pages, next)
    }

    // MARK: Photo requests from the crew

    /// Shows the oldest photo request from a crewmate, but only when the lens is otherwise idle.
    func checkPhotoRequests() async {
        guard glasses.isReady, case .idle = phase,
              let r = try? await api.photoRequests().first, case .idle = phase else { return }
        phase = .photoRequest(r)
        glasses.show(Cards.photoRequest(r, onSend: { [weak self] in self?.answerPhoto(send: true) },
                                        onDecline: { [weak self] in self?.answerPhoto(send: false) }))
        speak("\(r.requester) wants a photo. \(r.reason)")
    }

    /// Capture happens only on the wearer's Send; Decline hands over nothing.
    func answerPhoto(send: Bool) {
        guard case .photoRequest(let r) = phase else { return }
        phase = send ? .capturing : .idle
        task = Task {
            await run {
                var b64: String?
                if send {
                    self.glasses.show(Cards.message("Looking…"))
                    guard let jpeg = FramePrep.jpeg(from: try await self.glasses.capturePhoto(), maxSide: 1024) else { throw LensAPIError.badResponse }
                    b64 = jpeg.base64EncodedString()
                }
                _ = try await self.api.answerPhoto(id: r.id, imageJpegB64: b64)
                self.reset()
                if send { self.glasses.show(Cards.message("Sent to \(r.requester)")) }
            }
        }
    }

    // MARK: Live mode

    static let liveInterval: Duration = .seconds(5)
    static let liveLimit: Duration = .seconds(600)

    /// "Watch me": a frame every few seconds goes to Den, which moves the guide on when the step is done
    /// and warns when something looks wrong. Stops after 10 minutes, at the end of the guide, or on Stop.
    func toggleLive() {
        if live != nil { return stopLive() }
        guard case .guide = phase else { return }
        live = ""
        rerenderGuide()
        // ponytail: one photo capture per frame; switch to the camera stream if capture latency makes steps lag.
        liveTask = Task {
            let deadline = ContinuousClock.now + Self.liveLimit
            while !Task.isCancelled, ContinuousClock.now < deadline {
                await liveFrame()
                try? await Task.sleep(for: Self.liveInterval)
            }
            if !Task.isCancelled { stopLive() }
        }
    }

    func stopLive() {
        liveTask?.cancel()
        liveTask = nil
        guard live != nil else { return }
        live = nil
        rerenderGuide()
    }

    func diagram(at index: Int) -> UIImage? { diagrams[index] }

    private func rerenderGuide() {
        if case .guide(let pages, let index) = phase { showGuide(pages, index) }
    }

    /// One frame: capture, ask, then advance or warn. A failed frame is skipped; the next one tries again.
    func liveFrame() async {
        guard case .guide(let pages, let index) = phase else { return stopLive() }
        do {
            let raw = try await glasses.capturePhoto()
            guard let jpeg = FramePrep.jpeg(from: raw, maxSide: 512) else { return }
            // Den needs only this step and the next one, so long guides stay within the server's step limit.
            let steps = pages[index..<min(index + 2, pages.count)].map { String(($0.body.isEmpty ? $0.title : "\($0.title): \($0.body)").prefix(200)) }
            let result = try await api.watch(imageJpegB64: jpeg.base64EncodedString(), steps: steps, index: 0)
            // The wearer may have stepped by hand while the frame was in flight; that frame no longer applies.
            guard live != nil, case .guide(_, let now) = phase, now == index else { return }
            if result.done {
                live = ""
                step(1)
                if case .guide(let p, let i) = phase { speak("Done. Next: \(p[i].title)") }
            } else if !result.note.isEmpty, result.note != live {
                live = result.note
                rerenderGuide()
                speak(result.note)
            }
        } catch is CancellationError {
        } catch LensAPIError.rateLimited {
            stopLive()
        } catch {
            Log.info("lens.live", "frame skipped: \(error.localizedDescription)")
        }
    }

    private func showFinal(_ contract: Contract) {
        phase = .finished(spentCents: contract.spentCents, refundCents: contract.refundCreditsCents)
        glasses.show(Cards.final(
            spentCents: contract.spentCents, budgetCents: budgetCents, refundCents: contract.refundCreditsCents,
            onDone: { [weak self] in self?.reset() }
        ))
    }
}
