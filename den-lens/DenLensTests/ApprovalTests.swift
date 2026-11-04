import CryptoKit
import XCTest
@testable import DenLens

/// Software P-256 key standing in for the Secure Enclave, which the simulator does not have.
/// Used by tests only; the app never falls back to it.
struct SoftwareSigner: ApprovalSigner {
    let key = P256.Signing.PrivateKey()
    let deviceID = "test-device-0001"
    var publicKeySEC1: Data { key.publicKey.x963Representation }
    func sign(_ message: Data, reason: String) async throws -> Data { try key.signature(for: message).rawRepresentation }
}

struct NoAttestation: DeviceAttestor {
    func attest(publicKey: Data, deviceID: String) async -> (type: String, data: String, keyID: String) { ("none", "", "") }
}

/// Answers the approval endpoints and records what the app sent.
final class StubServer: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var requests: [(method: String, path: String, body: Data)] = []
    nonisolated(unsafe) static var registered = false
    nonisolated(unsafe) static var expiresAt = 0

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func stopLoading() {}

    override func startLoading() {
        let path = request.url!.path
        let method = request.httpMethod ?? "GET"
        var body = request.httpBody ?? Data()
        if body.isEmpty, let stream = request.httpBodyStream {
            stream.open()
            var buf = [UInt8](repeating: 0, count: 8192)
            while stream.hasBytesAvailable { let n = stream.read(&buf, maxLength: buf.count); if n <= 0 { break }; body.append(contentsOf: buf[..<n]) }
            stream.close()
        }
        Self.requests.append((method, path, body))
        let json: String
        switch (method, path) {
        case ("GET", "/me/approver-devices"):
            json = Self.registered ? #"{"devices":[{"device_id":"test-device-0001"}]}"# : #"{"devices":[]}"#
        case ("POST", "/me/approver-devices"):
            Self.registered = true
            json = #"{"device_id":"test-device-0001"}"#
        case ("GET", "/contracts/c1/approval-request"):
            json = #"{"request_id":"c1:7","payload_digest":"\#(String(repeating: "ab", count: 32))","expires_at":\#(Self.expiresAt),"signature_required":true,"summary":{"flags":["mainnet_spend"],"budget_cents":2000,"committed_cents":1800,"contract_id":"c1","event_seq":7,"pause_reason":"awaiting_plan_approval"},"devices":["test-device-0001"]}"#
        case ("GET", "/lens/photo-requests"):
            json = #"{"requests":[{"id":"pr_1","requester":"my-openclaw","reason":"Which pump model?","created_at":"x","expires_at":"y"}]}"#
        case ("POST", "/lens/photo-requests/pr_1"):
            json = #"{"request_id":"pr_1","state":"sent"}"#
        case ("POST", "/lens/diagram"):
            let jpeg = UIGraphicsImageRenderer(size: CGSize(width: 60, height: 60)).image { _ in }.jpegData(compressionQuality: 0.5)!
            json = #"{"image_jpeg_b64":"\#(jpeg.base64EncodedString())"}"#
        case ("POST", "/lens/watch"):
            json = #"{"done":true,"note":""}"#
        case ("POST", "/contracts/c1/approve"):
            json = #"{"id":"c1","state":"hiring"}"#
        default:
            json = "{}"
        }
        let resp = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(json.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
}

final class ApprovalTests: XCTestCase {
    private let digest = String(repeating: "ab", count: 32)

    private func service() -> ApprovalService {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubServer.self]
        StubServer.requests = []
        StubServer.registered = false
        StubServer.expiresAt = Int(Date().timeIntervalSince1970) + 300
        let api = LensAPI(baseURL: URL(string: "https://api.test")!, session: URLSession(configuration: config), apiKey: { "k" })
        return ApprovalService(signer: SoftwareSigner(), api: api, attestor: NoAttestation())
    }

    /// Pins the wire format the API's verifier (services/approvals.py) and the ADR define.
    func testPreimageAndDigestMatchTheProtocolVector() {
        let preimage = ApprovalMessage.preimage(requestID: "ct1:7", payloadDigest: digest, decision: "approve", expiresAt: 1_800_000_300)
        XCTAssertEqual(String(decoding: preimage, as: UTF8.self), "ct1:7|\(digest)|approve|1800000300")
        XCTAssertEqual(SHA256.hash(data: preimage).map { String(format: "%02x", $0) }.joined(),
                       "7986c466c20fc72fe7e4e7e290ab6b469771ba8724e2286a814c03c53dbfa1ce")  // same value the API test pins
    }

    func testSignatureIsRaw64BytesThatVerifyAgainstTheSEC1Key() async throws {
        let signer = SoftwareSigner()
        let message = ApprovalMessage.preimage(requestID: "c1:7", payloadDigest: digest, decision: "approve", expiresAt: 1)
        let raw = try await signer.sign(message, reason: "test")
        XCTAssertEqual(raw.count, 64)
        let pub = try P256.Signing.PublicKey(x963Representation: signer.publicKeySEC1)
        XCTAssertEqual(signer.publicKeySEC1.count, 65)
        XCTAssertTrue(pub.isValidSignature(try P256.Signing.ECDSASignature(rawRepresentation: raw), for: message))
    }

    func testBase64URLHasNoPaddingOrUnsafeCharacters() {
        XCTAssertEqual(base64URL(Data([0xfb, 0xff, 0xfe, 0x01])), "-__-AQ")
    }

    func testRegisterSendsTheKeyOnceAndMarksEnrolled() async throws {
        let svc = service()
        try await svc.ensureRegistered()
        try await svc.ensureRegistered()  // already listed: no second POST
        let posts = StubServer.requests.filter { $0.method == "POST" }
        XCTAssertEqual(posts.count, 1)
        let sent = try XCTUnwrap(JSONSerialization.jsonObject(with: posts[0].body) as? [String: Any])
        XCTAssertEqual(sent["device_id"] as? String, "test-device-0001")
        let key = try XCTUnwrap(sent["public_key"] as? [String: String])
        XCTAssertEqual(key["format"], "sec1")
        XCTAssertEqual(key["value"], base64URL(svc.signer.publicKeySEC1))
        XCTAssertTrue(ApprovalState.enrolled)
        ApprovalState.enrolled = false
    }

    func testApproveSignsExactlyWhatTheServerIssued() async throws {
        let svc = service()
        let request = try await svc.request(contractID: "c1")
        XCTAssertEqual(request.flagLabels, ["Real money (mainnet)"])
        let contract = try await svc.approve(contractID: "c1", request: request)
        XCTAssertEqual(contract.state, "hiring")

        let post = try XCTUnwrap(StubServer.requests.last { $0.path == "/contracts/c1/approve" })
        let approval = try XCTUnwrap((JSONSerialization.jsonObject(with: post.body) as? [String: Any])?["approval"] as? [String: Any])
        XCTAssertEqual(approval["version"] as? String, "approval/1.0")
        XCTAssertEqual(approval["form"] as? String, "p256")
        XCTAssertEqual(approval["decision"] as? String, "approve")
        XCTAssertEqual(approval["request_id"] as? String, "c1:7")
        XCTAssertEqual(approval["expires_at"] as? Int, request.expiresAt)

        // The signature must verify over the preimage built from the same fields.
        let message = ApprovalMessage.preimage(requestID: "c1:7", payloadDigest: digest, decision: "approve", expiresAt: request.expiresAt)
        let sigB64 = try XCTUnwrap(approval["signature"] as? String)
        var padded = sigB64.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        padded += String(repeating: "=", count: (4 - padded.count % 4) % 4)
        let raw = try XCTUnwrap(Data(base64Encoded: padded))
        let pub = try P256.Signing.PublicKey(x963Representation: svc.signer.publicKeySEC1)
        XCTAssertTrue(pub.isValidSignature(try P256.Signing.ECDSASignature(rawRepresentation: raw), for: message))
    }

    func testExpiredRequestIsNotSigned() async throws {
        let svc = service()
        let request = try await svc.request(contractID: "c1")
        do {
            _ = try await svc.approve(contractID: "c1", request: request, now: Date(timeIntervalSince1970: Double(request.expiresAt)))
            XCTFail("expected expiry")
        } catch {
            XCTAssertEqual(error as? ApprovalError, .expired)
        }
        XCTAssertFalse(StubServer.requests.contains { $0.path == "/contracts/c1/approve" })
    }

    func testContractAwaitingApprovalIsDetected() throws {
        let c = try LensAPI.decoder.decode(Contract.self, from: Data(#"{"id":"c","state":"paused","pause_reason":"awaiting_plan_approval"}"#.utf8))
        XCTAssertTrue(c.awaitingApproval)
        XCTAssertFalse(Contract(id: "c", state: "paused", pauseReason: "manual").awaitingApproval)
    }
}
