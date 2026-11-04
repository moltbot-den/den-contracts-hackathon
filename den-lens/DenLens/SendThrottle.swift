import Foundation

/// Pure state machine for "at most one full-view send per `interval`, latest wins".
/// The caller owns the clock and the sleeping; this only decides.
struct SendThrottle {
    enum Decision: Equatable {
        case sendNow
        /// Wait until this time, then send the newest pending view.
        case deferUntil(Date)
        /// A deferred send is already scheduled; just replace the pending view.
        case coalesce
    }

    let interval: TimeInterval
    private var lastSent: Date?
    private var deferredScheduled = false

    init(interval: TimeInterval = 2) { self.interval = interval }

    mutating func request(at now: Date) -> Decision {
        if deferredScheduled { return .coalesce }
        guard let last = lastSent, now.timeIntervalSince(last) < interval else { return .sendNow }
        deferredScheduled = true
        return .deferUntil(last.addingTimeInterval(interval))
    }

    /// Call after every send, including unthrottled user-driven ones, so the next status update waits.
    mutating func didSend(at now: Date) {
        lastSent = now
        deferredScheduled = false
    }
}
