import Foundation

struct GuidePage: Equatable {
    var title: String
    var body: String
    /// First https image in the step, shown on the glasses (display images must be https).
    var imageURL: URL?
    /// Numbered steps are kept even with no body; a bare heading is just a section label.
    fileprivate var isStep = false

    init(title: String, body: String, imageURL: URL? = nil) {
        self.title = title
        self.body = body
        self.imageURL = imageURL
    }
}

enum GuideSplitter {
    /// Splits a markdown deliverable into one page per numbered step or heading.
    /// Text before the first step becomes an intro page. Empty pages are dropped.
    static func pages(from markdown: String) -> [GuidePage] {
        var pages: [GuidePage] = []
        var current: (title: String, lines: [String], isStep: Bool)?
        var intro: [String] = []

        func flush() {
            guard let c = current else { return }
            var page = makePage(title: c.title, lines: c.lines)
            page.isStep = c.isStep
            pages.append(page)
            current = nil
        }

        for raw in markdown.replacingOccurrences(of: "\r\n", with: "\n").split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(raw)
            let step = numberedStep(line)
            if let title = heading(line) ?? step {
                if current == nil, !intro.joined().trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    pages.append(makePage(title: "Overview", lines: intro))
                }
                flush()
                current = (title, [], step != nil)
            } else if current != nil {
                current?.lines.append(line)
            } else {
                intro.append(line)
            }
        }
        flush()
        if pages.isEmpty {
            let whole = makePage(title: "Result", lines: intro)
            if !whole.body.isEmpty { pages = [whole] }
        }
        // A bare heading (e.g. "# Guide" directly followed by "1. ...") adds nothing.
        return pages.filter { $0.isStep || !$0.body.isEmpty || $0.imageURL != nil }
    }

    private static func heading(_ line: String) -> String? {
        guard line.hasPrefix("#") else { return nil }
        let t = line.drop(while: { $0 == "#" })
        guard t.first == " " else { return nil }
        return clean(String(t))
    }

    private static func numberedStep(_ line: String) -> String? {
        guard let r = line.range(of: #"^\s{0,3}\d+[.)]\s+"#, options: .regularExpression) else { return nil }
        return clean(String(line[r.upperBound...]))
    }

    private static func makePage(title: String, lines: [String]) -> GuidePage {
        var image: URL?
        var kept: [String] = []
        for line in lines {
            if image == nil, let r = line.range(of: #"!\[[^\]]*\]\((https://[^)\s]+)\)"#, options: .regularExpression) {
                let m = line[r]
                if let open = m.lastIndex(of: "("), let close = m.lastIndex(of: ")") {
                    image = URL(string: String(m[m.index(after: open)..<close]))
                }
            }
            let stripped = line.replacingOccurrences(of: #"!\[[^\]]*\]\([^)]*\)"#, with: "", options: .regularExpression)
            kept.append(clean(stripped))
        }
        let body = kept.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        return GuidePage(title: title, body: body.replacingOccurrences(of: #"\n{3,}"#, with: "\n\n", options: .regularExpression), imageURL: image)
    }

    /// Strips inline markdown the glasses cannot render.
    private static func clean(_ s: String) -> String {
        s.replacingOccurrences(of: "**", with: "")
            .replacingOccurrences(of: "__", with: "")
            .replacingOccurrences(of: "`", with: "")
            .replacingOccurrences(of: #"\[([^\]]+)\]\([^)]*\)"#, with: "$1", options: .regularExpression)
            .trimmingCharacters(in: .whitespaces)
    }
}
