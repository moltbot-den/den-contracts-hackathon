import Foundation
import UIKit
import ImageIO
import UniformTypeIdentifiers

enum LensAPIError: LocalizedError, Equatable {
    case noKey
    case unauthorized
    case insufficientCredits
    case rateLimited
    case modelUnavailable
    case unreadableImage
    case http(Int, String)
    case badResponse

    var errorDescription: String? {
        switch self {
        case .noKey: "Add your agent API key in Settings."
        case .unauthorized: "The API key was rejected."
        case .insufficientCredits: "Not enough credits for this budget."
        case .rateLimited: "Too many asks (30 per hour). Try again later."
        case .modelUnavailable: "The model is unavailable right now. Try again shortly."
        case .unreadableImage: "Den could not read that photo. Try again."
        case .http(let code, let msg): "Server error \(code): \(msg)"
        case .badResponse: "Unexpected server response."
        }
    }
}

struct LensAPI: Sendable {
    var baseURL = URL(string: "https://api.moltbotden.com")!
    var session: URLSession = .shared
    var apiKey: @Sendable () -> String? = { Keychain.loadAPIKey() }

    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
    static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()

    func request(_ path: String, method: String = "GET", body: (any Encodable)? = nil, accept: String = "application/json") throws -> URLRequest {
        guard let key = apiKey(), !key.isEmpty else { throw LensAPIError.noKey }
        var r = URLRequest(url: baseURL.appending(path: path))
        r.httpMethod = method
        r.setValue(key, forHTTPHeaderField: "X-API-Key")
        r.setValue(accept, forHTTPHeaderField: "Accept")
        if let body {
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            r.httpBody = try Self.encoder.encode(AnyEncodable(body))
        }
        return r
    }

    private func send<T: Decodable>(_ req: URLRequest, as: T.Type) async throws -> T {
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw LensAPIError.badResponse }
        switch http.statusCode {
        case 200..<300: break
        case 403 where String(decoding: data, as: UTF8.self).localizedCaseInsensitiveContains("approv"):
            throw LensAPIError.http(403, String(decoding: data.prefix(200), as: UTF8.self))
        case 401, 403: throw LensAPIError.unauthorized
        case 402: throw LensAPIError.insufficientCredits
        case 400, 422: throw LensAPIError.unreadableImage
        case 429: throw LensAPIError.rateLimited
        case 502, 503: throw LensAPIError.modelUnavailable
        default: throw LensAPIError.http(http.statusCode, String(decoding: data.prefix(200), as: UTF8.self))
        }
        do { return try Self.decoder.decode(T.self, from: data) } catch { throw LensAPIError.badResponse }
    }

    func intent(utterance: String, imageJpegB64: String) async throws -> LensIntent {
        var r = try request("lens/intent", method: "POST", body: IntentRequest(utterance: utterance, imageJpegB64: imageJpegB64))
        r.timeoutInterval = 60
        return try await send(r, as: LensIntent.self)
    }

    func ask(utterance: String, imageJpegB64: String) async throws -> LensAnswer {
        var r = try request("lens/ask", method: "POST", body: IntentRequest(utterance: utterance, imageJpegB64: imageJpegB64))
        r.timeoutInterval = 90  // web search plus up to three model turns
        return try await send(r, as: LensAnswer.self)
    }

    /// Live mode: is the current guide step done in this frame?
    func watch(imageJpegB64: String, steps: [String], index: Int) async throws -> WatchResult {
        var r = try request("lens/watch", method: "POST", body: WatchRequest(imageJpegB64: imageJpegB64, steps: steps, index: index))
        r.timeoutInterval = 30
        return try await send(r, as: WatchResult.self)
    }

    /// Muse Image draws one guide step (at most 600x600, $0.01).
    func diagram(step: String, context: String) async throws -> Diagram {
        var r = try request("lens/diagram", method: "POST", body: DiagramRequest(step: step, context: context))
        r.timeoutInterval = 60
        return try await send(r, as: Diagram.self)
    }

    func photoRequests() async throws -> [PhotoRequest] {
        try await send(request("lens/photo-requests"), as: PhotoRequests.self).requests
    }

    /// Sends the photo, or declines when `imageJpegB64` is nil.
    func answerPhoto(id: String, imageJpegB64: String?) async throws -> PhotoAnswered {
        var r = try request("lens/photo-requests/\(id)", method: "POST", body: PhotoAnswerBody(imageJpegB64: imageJpegB64))
        r.timeoutInterval = 30
        return try await send(r, as: PhotoAnswered.self)
    }

    func crewReplies(threadID: String) async throws -> [CrewReply] {
        try await send(request("lens/threads/\(threadID)"), as: CrewReplies.self).replies
    }

    /// A link that opens Den as a web app on Meta Ray-Ban Display glasses. A new one revokes the old.
    func glassesLink() async throws -> GlassesLink {
        try await send(request("lens/glasses-link", method: "POST"), as: GlassesLink.self)
    }

    func crewInvite() async throws -> CrewInvite {
        try await send(request("lens/crew/invite", method: "POST"), as: CrewInvite.self)
    }

    func createContract(_ body: ContractRequest) async throws -> Contract {
        try await send(request("contracts", method: "POST", body: body), as: Contract.self)
    }

    func contract(id: String) async throws -> Contract {
        try await send(request("contracts/\(id)"), as: Contract.self)
    }

    func cancelContract(id: String) async throws -> Contract {
        try await send(request("contracts/\(id)/cancel", method: "POST"), as: Contract.self)
    }

    func get<T: Decodable>(_ path: String) async throws -> T {
        try await send(request(path), as: T.self)
    }

    func post<T: Decodable>(_ path: String, _ body: (any Encodable)? = nil) async throws -> T {
        try await send(request(path, method: "POST", body: body), as: T.self)
    }

    /// Streams SSE events, resuming with `Last-Event-ID` after a drop, until the server sends `end`
    /// or the task is cancelled. Throws after `maxRetries` consecutive failures.
    func events(contractID: String, maxRetries: Int = 5) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                var parser = SSEParser()
                var failures = 0
                while !Task.isCancelled {
                    do {
                        var req = try request("contracts/\(contractID)/stream", accept: "text/event-stream")
                        req.timeoutInterval = 3600
                        if let last = parser.lastEventID { req.setValue(last, forHTTPHeaderField: "Last-Event-ID") }
                        let (bytes, resp) = try await session.bytes(for: req)
                        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { throw LensAPIError.badResponse }
                        failures = 0
                        var chunk = Data()
                        for try await byte in bytes {
                            chunk.append(byte)
                            if byte == 0x0A {
                                for e in parser.feed(chunk) {
                                    continuation.yield(e)
                                    if e.event == "end" { continuation.finish(); return }
                                }
                                chunk.removeAll(keepingCapacity: true)
                            }
                        }
                        throw LensAPIError.badResponse // stream closed without `end`: reconnect
                    } catch is CancellationError {
                        break
                    } catch {
                        failures += 1
                        if failures > maxRetries { continuation.finish(throwing: error); return }
                        try? await Task.sleep(for: .seconds(min(2 * failures, 10)))
                    }
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

struct AnyEncodable: Encodable {
    let value: any Encodable
    init(_ v: any Encodable) { value = v }
    func encode(to encoder: Encoder) throws { try value.encode(to: encoder) }
}

/// Frame handling. Everything stays in memory; nothing is written to disk.
enum FramePrep {
    /// Downscales to `maxSide` and re-encodes as JPEG, honouring EXIF orientation.
    static func jpeg(from data: Data, maxSide: Int, quality: CGFloat = 0.7) -> Data? {
        guard let src = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        let opts: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: maxSide,
        ]
        guard let cg = CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary) else { return nil }
        return UIImage(cgImage: cg).jpegData(compressionQuality: quality)
    }

    static func decodeCrop(b64: String?, maxSide: Int = 560) -> UIImage? {
        guard let b64, let raw = Data(base64Encoded: b64), let small = jpeg(from: raw, maxSide: maxSide) else { return nil }
        return UIImage(data: small)
    }
}
