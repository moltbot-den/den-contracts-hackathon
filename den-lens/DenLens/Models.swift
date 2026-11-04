import Foundation

/// Wire types for the Moltbot Den backend contract (see docs/hackathon/HACKATHON-PRD.md 5.6).
/// Decoding is tolerant: the backend is built in parallel, so optional fields default instead of failing.

struct IntentRequest: Encodable {
    let utterance: String
    let imageJpegB64: String
}

struct WatchRequest: Encodable {
    let imageJpegB64: String
    let steps: [String]
    let index: Int
}

struct WatchResult: Decodable, Equatable {
    let done: Bool
    let note: String
}

/// A crewmate asks the wearer for a photo (POST /lens/photo-requests).
struct PhotoRequest: Decodable, Equatable {
    let id: String
    let requester: String
    let reason: String
}

struct PhotoRequests: Decodable {
    let requests: [PhotoRequest]
}

/// No image means decline.
struct PhotoAnswerBody: Encodable {
    let imageJpegB64: String?
}

struct PhotoAnswered: Decodable {
    let state: String
}

struct LensTarget: Decodable, Equatable {
    let label: String
    let box: [Double]?
}

struct LensIntent: Decodable, Equatable {
    let goal: String
    let budgetCents: Int
    let acceptanceHints: [String]
    let target: LensTarget?
    let cropJpegB64: String?
    /// Storage refs of the saved crop; passed back unchanged on contract create.
    let evidence: [String]

    init(goal: String, budgetCents: Int, acceptanceHints: [String] = [], target: LensTarget? = nil, cropJpegB64: String? = nil, evidence: [String] = []) {
        self.goal = goal
        self.budgetCents = budgetCents
        self.acceptanceHints = acceptanceHints
        self.target = target
        self.cropJpegB64 = cropJpegB64
        self.evidence = evidence
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        goal = try c.decode(String.self, forKey: .goal)
        budgetCents = try c.decode(Int.self, forKey: .budgetCents)
        acceptanceHints = try c.decodeIfPresent([String].self, forKey: .acceptanceHints) ?? []
        target = try c.decodeIfPresent(LensTarget.self, forKey: .target)
        cropJpegB64 = try c.decodeIfPresent(String.self, forKey: .cropJpegB64)
        evidence = try c.decodeIfPresent([String].self, forKey: .evidence) ?? []
    }

    private enum CodingKeys: String, CodingKey { case goal, budgetCents, acceptanceHints, target, cropJpegB64, evidence }
}

struct LensSource: Decodable, Equatable {
    let title: String
    let url: String
}

/// Who got the task when Den handed it to the wearer's own agents (POST /lens/ask `crew`).
struct CrewHandoff: Decodable, Equatable {
    let crew: String
    let threadId: String?
    let sentTo: [String]

    init(crew: String, threadId: String?, sentTo: [String] = []) {
        self.crew = crew
        self.threadId = threadId
        self.sentTo = sentTo
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        crew = try c.decode(String.self, forKey: .crew)
        threadId = try c.decodeIfPresent(String.self, forKey: .threadId)
        sentTo = try c.decodeIfPresent([String].self, forKey: .sentTo) ?? []
    }

    private enum CodingKeys: String, CodingKey { case crew, threadId, sentTo }
}

/// POST /lens/ask: an answer now, plus a crew handoff and/or a hire offer when Den chose them.
struct LensAnswer: Decodable, Equatable {
    let answer: String
    let steps: [String]
    let sources: [LensSource]
    let crew: CrewHandoff?
    let hire: LensIntent?

    init(answer: String, steps: [String] = [], sources: [LensSource] = [], crew: CrewHandoff? = nil, hire: LensIntent? = nil) {
        self.answer = answer
        self.steps = steps
        self.sources = sources
        self.crew = crew
        self.hire = hire
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        answer = try c.decode(String.self, forKey: .answer)
        steps = try c.decodeIfPresent([String].self, forKey: .steps) ?? []
        sources = try c.decodeIfPresent([LensSource].self, forKey: .sources) ?? []
        crew = try c.decodeIfPresent(CrewHandoff.self, forKey: .crew)
        hire = try c.decodeIfPresent(LensIntent.self, forKey: .hire)
    }

    private enum CodingKeys: String, CodingKey { case answer, steps, sources, crew, hire }
}

struct CrewReply: Decodable, Equatable, Identifiable {
    let id: String
    let fromAgent: String
    let body: String
}

struct CrewReplies: Decodable {
    let replies: [CrewReply]
}

struct DiagramRequest: Encodable {
    let step: String
    let context: String
}

struct Diagram: Decodable {
    let imageJpegB64: String
}

struct GlassesLink: Decodable, Equatable {
    let url: URL
}

struct CrewInvite: Decodable, Equatable {
    let crew: String
    let members: [String]
    let message: String
}

struct ContractRequest: Encodable {
    let goal: String
    let budgetCents: Int
    let surface = "lens"
    let acceptanceHints: [String]
    let evidence: [String]
    var requireApproval = false
    let `public` = true
}

struct ContractJob: Decodable, Equatable, Identifiable {
    let id: String
    let capability: String
    let state: String
    let assignedAgent: String?
    let paidCents: Int

    init(id: String, capability: String, state: String, assignedAgent: String? = nil, paidCents: Int = 0) {
        self.id = id
        self.capability = capability
        self.state = state
        self.assignedAgent = assignedAgent
        self.paidCents = paidCents
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        capability = try c.decodeIfPresent(String.self, forKey: .capability) ?? "job"
        state = try c.decodeIfPresent(String.self, forKey: .state) ?? "pending"
        assignedAgent = try c.decodeIfPresent(String.self, forKey: .assignedAgent)
        paidCents = try c.decodeIfPresent(Int.self, forKey: .paidCents) ?? 0
    }

    private enum CodingKeys: String, CodingKey { case id, capability, state, assignedAgent, paidCents }
}

struct Contract: Decodable, Equatable {
    let id: String
    let state: String
    let jobs: [ContractJob]
    let spentCents: Int
    let deliverable: String?
    let refundCreditsCents: Int
    let pauseReason: String?

    init(id: String, state: String, jobs: [ContractJob] = [], spentCents: Int = 0, deliverable: String? = nil, refundCreditsCents: Int = 0, pauseReason: String? = nil) {
        self.pauseReason = pauseReason
        self.id = id
        self.state = state
        self.jobs = jobs
        self.spentCents = spentCents
        self.deliverable = deliverable
        self.refundCreditsCents = refundCreditsCents
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        state = try c.decodeIfPresent(String.self, forKey: .state) ?? "unknown"
        jobs = try c.decodeIfPresent([ContractJob].self, forKey: .jobs) ?? []
        spentCents = try c.decodeIfPresent(Int.self, forKey: .spentCents) ?? 0
        deliverable = try c.decodeIfPresent(String.self, forKey: .deliverable)
        refundCreditsCents = try c.decodeIfPresent(Int.self, forKey: .refundCreditsCents) ?? 0
        pauseReason = try c.decodeIfPresent(String.self, forKey: .pauseReason)
    }

    private enum CodingKeys: String, CodingKey { case id, state, jobs, spentCents, deliverable, refundCreditsCents, pauseReason }

    /// The API's terminal states (`TERMINAL` in services/contracts/store.py).
    var awaitingApproval: Bool { state == "paused" && (pauseReason ?? "").hasPrefix("awaiting_") }

    var isTerminal: Bool {
        ["done", "failed", "cancelled"].contains(state)
    }
}

func dollars(_ cents: Int) -> String {
    String(format: "$%d.%02d", cents / 100, abs(cents) % 100)
}
