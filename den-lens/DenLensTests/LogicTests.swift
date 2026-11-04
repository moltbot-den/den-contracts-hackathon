import XCTest
@testable import DenLens

final class GuideSplitterTests: XCTestCase {
    func testNumberedStepsBecomePagesWithIntro() {
        let md = """
        Replace the worn belt.

        1. **Unplug** the unit
        Wait 30 seconds.
        2. Remove the cover
           Two screws, `T20`.
        3. Fit the new belt
        """
        let pages = GuideSplitter.pages(from: md)
        XCTAssertEqual(pages.map(\.title), ["Overview", "Unplug the unit", "Remove the cover", "Fit the new belt"])
        XCTAssertEqual(pages[1].body, "Wait 30 seconds.")
        XCTAssertTrue(pages[2].body.contains("T20"))
        XCTAssertFalse(pages[2].body.contains("`"))
    }

    func testHeadingsSplitAndBareTitleIsDropped() {
        let md = "# Repair guide\n## Parts\nBelt, screws\n## Steps\nDo it\n"
        let pages = GuideSplitter.pages(from: md)
        XCTAssertEqual(pages.map(\.title), ["Parts", "Steps"])
    }

    func testImageExtractedOnlyIfHTTPS() {
        let md = "1. Look\n![diagram](https://img.example.com/a.png)\n2. Skip\n![x](http://insecure.example.com/b.png)\nbody"
        let pages = GuideSplitter.pages(from: md)
        XCTAssertEqual(pages[0].imageURL?.absoluteString, "https://img.example.com/a.png")
        XCTAssertNil(pages[1].imageURL)
        XCTAssertEqual(pages[1].body, "body")
    }

    func testPlainTextIsOnePage() {
        XCTAssertEqual(GuideSplitter.pages(from: "Just a paragraph.").map(\.body), ["Just a paragraph."])
        XCTAssertTrue(GuideSplitter.pages(from: "  \n").isEmpty)
    }
}

final class SSEParserTests: XCTestCase {
    func testParsesEventsAcrossChunkBoundaries() {
        var p = SSEParser()
        XCTAssertTrue(p.feed(Data("id: 7\nevent: job.state\nda".utf8)).isEmpty)
        let events = p.feed(Data("ta: {\"a\":1}\n\n: keepalive\n\nevent: end\ndata: bye\n\n".utf8))
        XCTAssertEqual(events, [
            SSEEvent(id: "7", event: "job.state", data: "{\"a\":1}"),
            SSEEvent(id: nil, event: "end", data: "bye"),
        ])
        XCTAssertEqual(p.lastEventID, "7")
    }

    func testMultilineDataCRLFAndDefaultEventName() {
        var p = SSEParser()
        let events = p.feed(Data("data: one\r\ndata: two\r\n\r\n".utf8))
        XCTAssertEqual(events, [SSEEvent(id: nil, event: "message", data: "one\ntwo")])
    }

    func testBlankLinesWithoutDataEmitNothing() {
        var p = SSEParser()
        XCTAssertTrue(p.feed(Data("\n\n: ping\n\n".utf8)).isEmpty)
    }
}

final class SendThrottleTests: XCTestCase {
    func testAtMostOneSendPerTwoSecondsLatestWins() {
        var t = SendThrottle(interval: 2)
        let t0 = Date(timeIntervalSince1970: 1000)
        XCTAssertEqual(t.request(at: t0), .sendNow)
        t.didSend(at: t0)
        XCTAssertEqual(t.request(at: t0.addingTimeInterval(0.5)), .deferUntil(t0.addingTimeInterval(2)))
        XCTAssertEqual(t.request(at: t0.addingTimeInterval(1.0)), .coalesce)
        t.didSend(at: t0.addingTimeInterval(2))
        XCTAssertEqual(t.request(at: t0.addingTimeInterval(2.5)), .deferUntil(t0.addingTimeInterval(4)))
        t.didSend(at: t0.addingTimeInterval(4))
        XCTAssertEqual(t.request(at: t0.addingTimeInterval(7)), .sendNow)
    }

    func testUserDrivenSendStillResetsTheWindow() {
        var t = SendThrottle(interval: 2)
        let t0 = Date(timeIntervalSince1970: 0)
        t.didSend(at: t0)
        XCTAssertEqual(t.request(at: t0.addingTimeInterval(1)), .deferUntil(t0.addingTimeInterval(2)))
    }
}

final class ModelTests: XCTestCase {
    func testContractDecodesSnakeCaseAndToleratesMissingFields() throws {
        let json = #"{"id":"c1","state":"running","jobs":[{"id":"j1","capability":"research","state":"done","assigned_agent":"den-research","paid_cents":40},{"id":"j2"}],"spent_cents":40}"#
        let c = try LensAPI.decoder.decode(Contract.self, from: Data(json.utf8))
        XCTAssertEqual(c.jobs.first?.assignedAgent, "den-research")
        XCTAssertEqual(c.jobs[1].state, "pending")
        XCTAssertEqual(c.refundCreditsCents, 0)
        XCTAssertFalse(c.isTerminal)
    }

    func testContractRequestWireFormat() throws {
        let body = ContractRequest(goal: "g", budgetCents: 2000, acceptanceHints: ["a"], evidence: ["gs://b/crop.jpg"])
        let obj = try JSONSerialization.jsonObject(with: LensAPI.encoder.encode(body)) as! [String: Any]
        XCTAssertEqual(obj["surface"] as? String, "lens")
        XCTAssertEqual(obj["budget_cents"] as? Int, 2000)
        XCTAssertEqual(obj["require_approval"] as? Bool, false)
        XCTAssertEqual(obj["public"] as? Bool, true)
        XCTAssertEqual(obj["evidence"] as? [String], ["gs://b/crop.jpg"])
    }

    /// The answer card renders from this; a hire offer must decode to the same draft the confirm card uses.
    func testAskAnswerDecodesCrewAndHireAndDefaultsTheRest() throws {
        let json = #"""
        {"answer": "Impeller 073129", "steps": ["Shut off power"], "sources": [{"title": "poolweb.com", "url": "https://poolweb.com/x"}],
         "crew": {"crew": "will-crew", "thread_id": "th_1", "sent_to": ["my-openclaw"]},
         "hire": {"goal": "Buy it", "budget_cents": 1000, "evidence": ["contracts/evidence/w/lens-1.jpg"]}}
        """#
        let a = try LensAPI.decoder.decode(LensAnswer.self, from: Data(json.utf8))
        XCTAssertEqual(a.crew, CrewHandoff(crew: "will-crew", threadId: "th_1", sentTo: ["my-openclaw"]))
        XCTAssertEqual(a.hire?.budgetCents, 1000)
        XCTAssertEqual(a.hire?.evidence, ["contracts/evidence/w/lens-1.jpg"])
        let bare = try LensAPI.decoder.decode(LensAnswer.self, from: Data(#"{"answer": "Hi", "crew": null, "hire": null}"#.utf8))
        XCTAssertEqual(bare, LensAnswer(answer: "Hi"))
    }

    func testIntentDecodesEvidenceAndDefaultsItToEmpty() throws {
        let full = #"{"goal":"g","budget_cents":2000,"acceptance_hints":[],"target":{"label":"belt","box":null},"crop_jpeg_b64":null,"evidence":["gs://b/c.jpg"]}"#
        XCTAssertEqual(try LensAPI.decoder.decode(LensIntent.self, from: Data(full.utf8)).evidence, ["gs://b/c.jpg"])
        let bare = #"{"goal":"g","budget_cents":1}"#
        XCTAssertEqual(try LensAPI.decoder.decode(LensIntent.self, from: Data(bare.utf8)).evidence, [])
    }

    func testRequestCarriesKeyHeaderAndNeverWithoutKey() throws {
        var api = LensAPI(apiKey: { nil })
        XCTAssertThrowsError(try api.request("contracts/x")) { XCTAssertEqual($0 as? LensAPIError, .noKey) }
        api = LensAPI(apiKey: { "k123" })
        let r = try api.request("contracts/x")
        XCTAssertEqual(r.value(forHTTPHeaderField: "X-API-Key"), "k123")
        XCTAssertEqual(r.url?.absoluteString, "https://api.moltbotden.com/contracts/x")
    }
}
