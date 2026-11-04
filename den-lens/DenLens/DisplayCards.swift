import Foundation
import MWDATDisplay
import UIKit

// Glasses cards. This file deliberately does not import SwiftUI: Text/Button/Image would be ambiguous.
// The display is 600x600; keep copy short and images well under that.

typealias CardAction = @MainActor () -> Void

private func tap(_ action: @escaping CardAction) -> @Sendable () -> Void {
    { Task { @MainActor in action() } }
}

private func clip(_ s: String, _ n: Int) -> String {
    s.count <= n ? s : String(s.prefix(n - 1)) + "…"
}

enum Cards {
    static let hireLabel = "Pinch to hire"

    static func idle(onAsk: @escaping CardAction, onDescribe: @escaping CardAction) -> FlexBox {
        FlexBox(direction: .column, spacing: 12) {
            Text("Den Lens", style: .heading)
            Text("Look at something and ask. Den answers, or gets your agents on it.", style: .body, color: .secondary)
            ButtonGroup {
                Button(label: "Ask Den", style: .primary, iconName: .metaAi, onClick: tap(onAsk)).actionRole(.primary)
                Button(label: "Describe", style: .secondary, onClick: tap(onDescribe))
            }
        }
        .padding(24)
        .background(.card)
    }

    static func message(_ title: String, _ detail: String = "") -> FlexBox {
        FlexBox(direction: .column, spacing: 12) {
            Text(title, style: .heading)
            if !detail.isEmpty { Text(clip(detail, 160), style: .body, color: .secondary) }
        }
        .padding(24)
        .background(.card)
    }

    static func photoRequest(_ r: PhotoRequest, onSend: @escaping CardAction, onDecline: @escaping CardAction) -> FlexBox {
        FlexBox(direction: .column, spacing: 10) {
            Text("\(clip(r.requester, 40)) wants a photo", style: .heading)
            Text(clip(r.reason, 160), style: .body, color: .secondary)
            ButtonGroup {
                Button(label: "Send photo", style: .primary, iconName: .checkmarkCircle, onClick: tap(onSend)).actionRole(.primary)
                Button(label: "Decline", style: .outline, onClick: tap(onDecline))
            }
        }
        .padding(24)
        .background(.card)
    }

    static func answer(_ a: LensAnswer, latestReply: CrewReply?, onSteps: CardAction?, onHire: CardAction?, onDone: @escaping CardAction) -> FlexBox {
        FlexBox(direction: .column, spacing: 10) {
            Text(clip(a.answer, 200), style: .body)
            if let r = latestReply {
                Text(clip("\(r.fromAgent): \(r.body)", 140), style: .body, color: .secondary)
            } else if let crew = a.crew {
                Text(crew.sentTo.isEmpty ? "None of your agents are online" : "Sent to \(crew.sentTo.count) of your agents", style: .meta, color: .secondary)
            }
            if let src = a.sources.first { Text(clip(src.title, 40), style: .meta, color: .secondary) }
            ButtonGroup {
                if let onSteps { Button(label: "Steps", style: .secondary, iconName: .triangleRightVerticalLine, onClick: tap(onSteps)) }
                if let onHire { Button(label: "Hire help", style: .primary, iconName: .checkmarkCircle, onClick: tap(onHire)).actionRole(.primary) }
                Button(label: "Done", style: onHire == nil ? .primary : .outline, iconName: .checkmark, onClick: tap(onDone))
            }
        }
        .padding(24)
        .background(.card)
    }

    static func confirm(goal: String, budgetCents: Int, crop: UIImage?, onHire: @escaping CardAction, onCancel: @escaping CardAction) -> FlexBox {
        FlexBox(direction: .column, spacing: 10) {
            Text(clip(goal, 140), style: .heading)
            Text("Budget \(dollars(budgetCents))", style: .body, color: .secondary)
            if let crop { Image(image: crop, sizePreset: .fill, cornerRadius: .medium) }
            ButtonGroup {
                Button(label: hireLabel, style: .primary, iconName: .checkmarkCircle, onClick: tap(onHire)).actionRole(.primary)
                Button(label: "Cancel", style: .outline, onClick: tap(onCancel))
            }
        }
        .padding(24)
        .background(.card)
    }

    static func approval(budgetCents: Int, flags: [String], signed: Bool, note: String, onApprove: @escaping CardAction, onDecline: @escaping CardAction) -> FlexBox {
        FlexBox(direction: .column, spacing: 10) {
            Text("Approve the plan", style: .heading)
            Text("Up to \(dollars(budgetCents))", style: .body, color: .secondary)
            for flag in flags { Text(clip("! \(flag)", 40), style: .body) }
            if !note.isEmpty { Text(clip(note, 80), style: .meta, color: .secondary) }
            ButtonGroup {
                Button(label: signed ? "Pinch, then Face ID" : "Pinch to approve", style: .primary, iconName: .checkmarkCircle, onClick: tap(onApprove)).actionRole(.primary)
                Button(label: "Decline", style: .outline, onClick: tap(onDecline))
            }
        }
        .padding(24)
        .background(.card)
    }

    /// An Agent Secret Store request waiting on this phone. The pinch opens Face ID on the phone; nothing is approved before that signature.
    static func inboxRequest(title: String, lines: [String], pending: Int, onApprove: @escaping CardAction, onDeny: @escaping CardAction) -> FlexBox {
        FlexBox(direction: .column, spacing: 10) {
            Text(pending > 1 ? "Approval · \(pending) waiting" : "Approval needed", style: .meta, color: .secondary)
            Text(clip(title, 90), style: .heading)
            for line in lines.prefix(3) { Text(clip(line, 60), style: .body, color: .secondary) }
            ButtonGroup {
                Button(label: "Pinch, then Face ID", style: .primary, iconName: .checkmarkCircle, onClick: tap(onApprove)).actionRole(.primary)
                Button(label: "Deny", style: .outline, onClick: tap(onDeny))
            }
        }
        .padding(24)
        .background(.card)
    }

    static func status(_ contract: Contract, budgetCents: Int, note: String) -> FlexBox {
        FlexBox(direction: .column, spacing: 8) {
            Text("Hiring · \(dollars(contract.spentCents)) of \(dollars(budgetCents))", style: .heading)
            for job in contract.jobs.prefix(4) {
                Text(clip("\(job.capability) · \(job.assignedAgent ?? "open") · \(job.state)", 44), style: .body)
            }
            if !note.isEmpty { Text(clip(note, 60), style: .meta, color: .secondary) }
        }
        .padding(24)
        .background(.card)
    }

    /// `live` is nil when live mode is off; otherwise its latest warning (empty when all is well).
    static func guide(_ page: GuidePage, index: Int, count: Int, image: UIImage?, live: String? = nil, onWatch: CardAction? = nil,
                      onBack: @escaping CardAction, onNext: @escaping CardAction) -> FlexBox {
        FlexBox(direction: .column, spacing: 8) {
            Text("Step \(index + 1) of \(count)" + (live == nil ? "" : " · Den is watching"), style: .meta, color: .secondary)
            if let live, !live.isEmpty { Text(clip(live, 80), style: .body) }
            Text(clip(page.title, 80), style: .heading)
            if let image {
                Image(image: image, sizePreset: .fill, cornerRadius: .medium)
            } else if let url = page.imageURL {
                Image(uri: url.absoluteString, sizePreset: .fill, cornerRadius: .medium)
            }
            if !page.body.isEmpty { Text(clip(page.body, image == nil && page.imageURL == nil ? 280 : 120), style: .body) }
            // Watch me gets its own row and the initial focus: one pinch starts it, no swipe across a crowded row.
            if let onWatch, live == nil {
                ButtonGroup { Button(label: "Watch me", style: .primary, onClick: tap(onWatch)).actionRole(.primary) }
            }
            ButtonGroup {
                Button(label: "Back", style: .secondary, iconName: .triangleLeftVerticalLine, onClick: tap(onBack))
                if let onWatch, live != nil { Button(label: "Stop", style: .outline, onClick: tap(onWatch)) }
                if onWatch != nil, live == nil {
                    Button(label: index + 1 == count ? "Finish" : "Next", style: .secondary, iconName: .triangleRightVerticalLine, onClick: tap(onNext))
                }
                if onWatch == nil || live != nil {
                    Button(label: index + 1 == count ? "Finish" : "Next", style: .primary, iconName: .triangleRightVerticalLine, onClick: tap(onNext)).actionRole(.primary)
                }
            }
        }
        .padding(24)
        .background(.card)
    }

    static func final(spentCents: Int, budgetCents: Int, refundCents: Int, onDone: @escaping CardAction) -> FlexBox {
        FlexBox(direction: .column, spacing: 12) {
            Text("Done", style: .heading)
            Text("Spent \(dollars(spentCents)) of \(dollars(budgetCents))", style: .body)
            Text("Refunded \(dollars(refundCents))", style: .body, color: .secondary)
            Button(label: "Done", style: .primary, iconName: .checkmark, onClick: tap(onDone))
        }
        .padding(24)
        .background(.card)
    }

    static func failure(_ message: String, onRetry: @escaping CardAction) -> FlexBox {
        FlexBox(direction: .column, spacing: 12) {
            Text("Something went wrong", style: .heading)
            Text(clip(message, 140), style: .body, color: .secondary)
            Button(label: "Try again", style: .primary, onClick: tap(onRetry))
        }
        .padding(24)
        .background(.card)
    }
}
