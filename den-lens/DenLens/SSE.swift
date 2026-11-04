import Foundation

struct SSEEvent: Equatable {
    var id: String?
    var event: String
    var data: String
}

/// Incremental Server-Sent Events parser. Feed raw bytes as they arrive.
/// URLSession's `AsyncBytes.lines` drops blank lines, which are the SSE event delimiter, so we split ourselves.
struct SSEParser {
    private var buffer = Data()
    private var id: String?
    private var event = ""
    private var dataLines: [String] = []
    /// Last event id seen, for `Last-Event-ID` on reconnect.
    private(set) var lastEventID: String?

    mutating func feed(_ bytes: Data) -> [SSEEvent] {
        buffer.append(bytes)
        var out: [SSEEvent] = []
        // ponytail: LF and CRLF only; a bare-CR line ending is not supported.
        while let nl = buffer.firstIndex(of: 0x0A) {
            var line = String(decoding: buffer[buffer.startIndex..<nl], as: UTF8.self)
            if line.hasSuffix("\r") { line.removeLast() }
            buffer.removeSubrange(buffer.startIndex...nl)
            if let e = consume(line: line) { out.append(e) }
        }
        return out
    }

    private mutating func consume(line: String) -> SSEEvent? {
        if line.isEmpty {
            defer { id = nil; event = ""; dataLines = [] }
            guard !dataLines.isEmpty || !event.isEmpty else { return nil }
            return SSEEvent(id: id, event: event.isEmpty ? "message" : event, data: dataLines.joined(separator: "\n"))
        }
        if line.hasPrefix(":") { return nil }
        let name: Substring, value: Substring
        if let colon = line.firstIndex(of: ":") {
            name = line[line.startIndex..<colon]
            var v = line[line.index(after: colon)...]
            if v.first == " " { v = v.dropFirst() }
            value = v
        } else {
            name = line[...]
            value = ""
        }
        switch name {
        case "id":
            id = String(value)
            lastEventID = String(value)
        case "event": event = String(value)
        case "data": dataLines.append(String(value))
        default: break
        }
        return nil
    }
}
