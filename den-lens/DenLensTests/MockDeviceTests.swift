import MWDATCore
import MWDATMockDevice
import UIKit
import XCTest
@testable import DenLens

/// Session -> display -> photo capture path against Mock Device Kit (no glasses needed).
@MainActor
final class MockDeviceTests: XCTestCase {
    private var photoFile: URL!
    private var glasses: GlassesController!

    override func setUp() async throws {
        try? Wearables.configure()
        photoFile = FileManager.default.temporaryDirectory.appendingPathComponent("denlens-test-\(UUID().uuidString).jpg")
        let img = UIGraphicsImageRenderer(size: CGSize(width: 2000, height: 1500)).image { ctx in
            UIColor.red.setFill()
            ctx.fill(CGRect(x: 0, y: 0, width: 2000, height: 1500))
        }
        try img.jpegData(compressionQuality: 0.8)!.write(to: photoFile)
        glasses = GlassesController()
        try glasses.enableMock(photoFile: photoFile)
    }

    override func tearDown() async throws {
        glasses.disconnect()
        glasses.disableMock()
        try? FileManager.default.removeItem(at: photoFile)
    }

    func testSessionConnectsAndCapturesOneFrame() async throws {
        try await glasses.connect()
        XCTAssertTrue(glasses.isReady)

        let raw = try await glasses.capturePhoto()
        let small = try XCTUnwrap(FramePrep.jpeg(from: raw, maxSide: 1024))
        let size = try XCTUnwrap(UIImage(data: small)).size
        XCTAssertLessThanOrEqual(max(size.width, size.height), 1024)

        // A second capture works: Photo is started and stopped per capture.
        _ = try await glasses.capturePhoto()
    }

    func testDisplaySendsAreThrottledToOnePerTwoSeconds() async throws {
        try await glasses.connect()
        let t0 = Date()
        let contract = Contract(id: "c", state: "running")
        for i in 0..<5 {
            glasses.show(Cards.status(contract, budgetCents: 2000, note: "n\(i)"), throttled: true, now: t0.addingTimeInterval(Double(i) * 0.1))
        }
        await glasses.flushSends()
        XCTAssertEqual(glasses.sentCount, 1, "first goes out, the rest coalesce behind the 2 s window")
    }

    func testApprovalCardReachesTheDisplay() async throws {
        try await glasses.connect()
        glasses.show(Cards.approval(budgetCents: 2000, flags: ["Real money (mainnet)"], signed: true, note: "", onApprove: {}, onDecline: {}))
        await glasses.flushSends()
        XCTAssertEqual(glasses.sentCount, 1)
    }

    /// The capture button on the glasses starts an ask hands-free; nothing else on the glasses should.
    func testCaptureButtonStartsAnAsk() async throws {
        var asks = 0
        glasses.onAsk = { asks += 1 }
        try await glasses.connect()
        let mock = try XCTUnwrap(glasses.mockGlassesForTests)
        for _ in 0..<100 where !glasses.inputsActive { try await Task.sleep(for: .milliseconds(50)) }
        XCTAssertTrue(glasses.inputsActive, "Inputs never became active")
        mock.services.input.select()
        mock.services.input.capture(pressType: .shortPress)
        for _ in 0..<40 where asks == 0 { try await Task.sleep(for: .milliseconds(50)) }
        XCTAssertEqual(asks, 1)
    }

    /// Live mode: a frame Den judges done moves the guide on by itself, sending only this step and the next.
    func testLiveModeAdvancesTheGuideWhenDenSeesTheStepDone() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubServer.self]
        StubServer.requests = []
        let api = LensAPI(baseURL: URL(string: "https://api.test")!, session: URLSession(configuration: config), apiKey: { "k" })
        let flow = LensFlow(glasses: glasses, api: api)
        try await glasses.connect()
        flow.showGuide((1...3).map { GuidePage(title: "Step \($0)", body: "do \($0)") }, 0)

        flow.toggleLive()
        for _ in 0..<100 { if case .guide(_, 1) = flow.phase { break }; try await Task.sleep(for: .milliseconds(50)) }
        flow.stopLive()

        guard case .guide(_, let index) = flow.phase else { return XCTFail("left the guide") }
        XCTAssertEqual(index, 1)
        XCTAssertNil(flow.live)
        let sent = try XCTUnwrap(StubServer.requests.first { $0.path == "/lens/watch" })
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: sent.body) as? [String: Any])
        XCTAssertEqual(body["steps"] as? [String], ["Step 1: do 1", "Step 2: do 2"])
        XCTAssertEqual(body["index"] as? Int, 0)
        XCTAssertNotNil(body["image_jpeg_b64"])
    }

    /// A crewmate's photo request shows on the idle lens; the photo is captured and sent only on the wearer's Send.
    func testPhotoRequestSendsAPhotoOnlyWhenTheWearerSays() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubServer.self]
        StubServer.requests = []
        let flow = LensFlow(glasses: glasses, api: LensAPI(baseURL: URL(string: "https://api.test")!, session: URLSession(configuration: config), apiKey: { "k" }))
        try await glasses.connect()

        await flow.checkPhotoRequests()
        guard case .photoRequest(let r) = flow.phase else { return XCTFail("no request shown") }
        XCTAssertEqual(r.requester, "my-openclaw")
        XCTAssertFalse(StubServer.requests.contains { $0.method == "POST" }, "nothing sent before the wearer answers")

        flow.answerPhoto(send: true)
        for _ in 0..<100 { if case .idle = flow.phase { break }; try await Task.sleep(for: .milliseconds(50)) }
        let sent = try XCTUnwrap(StubServer.requests.first { $0.method == "POST" && $0.path == "/lens/photo-requests/pr_1" })
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: sent.body) as? [String: Any])
        XCTAssertNotNil(body["image_jpeg_b64"] as? String)
    }

    /// Steps from an answer get a Muse Image diagram on the lens, drawn once per step.
    func testGuideStepGetsADiagramOnce() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubServer.self]
        StubServer.requests = []
        let flow = LensFlow(glasses: glasses, api: LensAPI(baseURL: URL(string: "https://api.test")!, session: URLSession(configuration: config), apiKey: { "k" }))
        let pages = [GuidePage(title: "Step 1", body: "Remove the lid bolts")]

        flow.showGuide(pages, 0)
        for _ in 0..<100 where flow.diagram(at: 0) == nil { try await Task.sleep(for: .milliseconds(50)) }
        XCTAssertNotNil(flow.diagram(at: 0))
        flow.showGuide(pages, 0)
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertEqual(StubServer.requests.filter { $0.path == "/lens/diagram" }.count, 1)
    }

    func testInboxRequestCardReachesTheDisplay() async throws {
        try await glasses.connect()
        glasses.show(Cards.inboxRequest(title: "Send 12.50 USDC", lines: ["Base mainnet"], pending: 2, onApprove: {}, onDeny: {}))
        await glasses.flushSends()
        XCTAssertEqual(glasses.sentCount, 1)
    }
}
