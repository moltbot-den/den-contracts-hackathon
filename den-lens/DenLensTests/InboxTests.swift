import CryptoKit
import XCTest
@testable import DenLens

/// Answers the Agent Secret Store approver endpoints and records requests.
final class AssStub: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var requests: [(method: String, path: String, auth: String?, body: Data)] = []
    nonisolated(unsafe) static var signExpiresAt = 0

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func stopLoading() {}

    override func startLoading() {
        var body = request.httpBody ?? Data()
        if body.isEmpty, let stream = request.httpBodyStream {
            stream.open()
            var buf = [UInt8](repeating: 0, count: 8192)
            while stream.hasBytesAvailable { let n = stream.read(&buf, maxLength: buf.count); if n <= 0 { break }; body.append(contentsOf: buf[..<n]) }
            stream.close()
        }
        let path = request.url!.path
        Self.requests.append((request.httpMethod ?? "GET", path, request.value(forHTTPHeaderField: "Authorization"), body))
        let json: String
        switch path {
        case "/v1/approver/inbox":
            json = #"{"requests":[{"request_id":"signing:r1","kind":"signing","title":"Send 12.50 USDC to 0xab…cd","lines":["Base mainnet"],"payload_digest":"\#(String(repeating: "cd", count: 32))","expires_at":"2030-01-01T00:00:00Z","sign_expires_at":\#(Self.signExpiresAt)}]}"#
        case "/v1/approver/devices": json = #"{"device_id":"test-device-0001","device_token":"ass_dev_secret"}"#
        default: json = "{}"
        }
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(json.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
}

@MainActor
final class InboxTests: XCTestCase {
    private func make() -> (ApproverInbox, SoftwareSigner) {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [AssStub.self]
        AssStub.requests = []
        AssStub.signExpiresAt = Int(Date().timeIntervalSince1970) + 300
        let signer = SoftwareSigner()
        let lens = LensAPI(baseURL: URL(string: "https://api.test")!, session: URLSession(configuration: config), apiKey: { "k" })
        let approvals = ApprovalService(signer: signer, api: lens, attestor: NoAttestation())
        let ass = AssAPI(baseURL: URL(string: "https://api.test/v1/approver/")!, session: URLSession(configuration: config), token: { "ass_dev_secret" })
        return (ApproverInbox(api: ass, approvals: approvals), signer)
    }

    func testInboxListsRequestsWithTheDeviceBearerToken() async throws {
        let (inbox, _) = make()
        let items = try await inbox.api.inbox()
        XCTAssertEqual(items.map(\.requestId), ["signing:r1"])
        XCTAssertEqual(items[0].title, "Send 12.50 USDC to 0xab…cd")
        XCTAssertEqual(AssStub.requests.first?.auth, "Bearer ass_dev_secret")
    }

    func testApproveSignsThePreimageOfTheServersRequest() async throws {
        let (inbox, signer) = make()
        let item = try await inbox.api.inbox()[0]
        await inbox.decide(item, approve: true)

        let post = try XCTUnwrap(AssStub.requests.last { $0.path == "/v1/approver/decisions" })
        let approval = try XCTUnwrap((JSONSerialization.jsonObject(with: post.body) as? [String: Any])?["approval"] as? [String: Any])
        XCTAssertEqual(approval["request_id"] as? String, "signing:r1")
        XCTAssertEqual(approval["decision"] as? String, "approve")
        XCTAssertEqual(approval["device_id"] as? String, "test-device-0001")
        XCTAssertEqual(approval["expires_at"] as? Int, item.signExpiresAt)

        let message = ApprovalMessage.preimage(requestID: "signing:r1", payloadDigest: item.payloadDigest, decision: "approve", expiresAt: item.signExpiresAt)
        var padded = (approval["signature"] as? String ?? "").replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        padded += String(repeating: "=", count: (4 - padded.count % 4) % 4)
        let raw = try XCTUnwrap(Data(base64Encoded: padded))
        let pub = try P256.Signing.PublicKey(x963Representation: signer.publicKeySEC1)
        XCTAssertTrue(pub.isValidSignature(try P256.Signing.ECDSASignature(rawRepresentation: raw), for: message))
    }

    func testDenyIsSignedAsDeny() async throws {
        let (inbox, signer) = make()
        let item = try await inbox.api.inbox()[0]
        await inbox.decide(item, approve: false)
        let post = try XCTUnwrap(AssStub.requests.last { $0.path == "/v1/approver/decisions" })
        let approval = try XCTUnwrap((JSONSerialization.jsonObject(with: post.body) as? [String: Any])?["approval"] as? [String: Any])
        XCTAssertEqual(approval["decision"] as? String, "deny")
        let denied = ApprovalMessage.preimage(requestID: "signing:r1", payloadDigest: item.payloadDigest, decision: "deny", expiresAt: item.signExpiresAt)
        let approved = ApprovalMessage.preimage(requestID: "signing:r1", payloadDigest: item.payloadDigest, decision: "approve", expiresAt: item.signExpiresAt)
        XCTAssertNotEqual(denied, approved)
        _ = signer
    }

    func testExpiredItemIsNotSigned() async throws {
        let (inbox, _) = make()
        AssStub.signExpiresAt = Int(Date().timeIntervalSince1970) - 10
        let item = try await inbox.api.inbox()[0]
        await inbox.decide(item, approve: true)
        XCTAssertFalse(AssStub.requests.contains { $0.path == "/v1/approver/decisions" })
        XCTAssertEqual(inbox.status, ApprovalError.expired.localizedDescription)
    }

    func testPairingSendsTheCodeAndKeyThenStoresTheDeviceToken() async throws {
        let (inbox, signer) = make()
        defer { Keychain.saveASSToken("") }
        await inbox.pair(code: " abcd234567 ")
        let post = try XCTUnwrap(AssStub.requests.first { $0.path == "/v1/approver/devices" })
        XCTAssertNil(post.auth, "pairing is the one unauthenticated call: the code stands in for a login")
        let sent = try XCTUnwrap(JSONSerialization.jsonObject(with: post.body) as? [String: Any])
        XCTAssertEqual(sent["pairing_code"] as? String, "ABCD234567")
        XCTAssertEqual(sent["device_id"] as? String, "test-device-0001")
        XCTAssertNotNil(sent["key_id"], "App Attest key id travels at the top level")
        XCTAssertEqual((sent["public_key"] as? [String: String])?["value"], base64URL(signer.publicKeySEC1))
        XCTAssertEqual(Keychain.loadASSToken(), "ass_dev_secret")
        XCTAssertTrue(inbox.paired)
    }

    func testPairingLinkFromTheDashboardQr() {
        XCTAssertEqual(ApproverInbox.pairingCode(from: URL(string: "denlens://pair?code=abcd234567")!), "ABCD234567")
        XCTAssertNil(ApproverInbox.pairingCode(from: URL(string: "denlens://pair?code=bad%20code")!))
        XCTAssertNil(ApproverInbox.pairingCode(from: URL(string: "denlens://pair?code=AB")!))
        XCTAssertNil(ApproverInbox.pairingCode(from: URL(string: "https://pair?code=ABCD234567")!))
        XCTAssertNil(ApproverInbox.pairingCode(from: URL(string: "denlens://callback?metaWearablesAction=x")!))
    }
}
