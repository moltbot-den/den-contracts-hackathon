import os
import Sentry

/// One call writes to the device log (Console.app, or `log collect` with subsystem
/// com.moltbotden.denlens) and to Sentry's breadcrumb trail, so every reported error
/// carries the steps that led to it. Never log what the wearer said or saw.
enum Log {
    private static let logger = Logger(subsystem: "com.moltbotden.denlens", category: "lens")

    static func info(_ category: String, _ message: String) {
        logger.info("[\(category, privacy: .public)] \(message, privacy: .public)")
        let crumb = Breadcrumb(level: .info, category: category)
        crumb.message = message
        SentrySDK.addBreadcrumb(crumb)
    }
}
