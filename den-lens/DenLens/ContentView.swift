import SwiftUI

struct ContentView: View {
    @Bindable var model: AppModel
    @State private var showSettings = false

    var body: some View {
        NavigationStack {
            List {
                glassesSection
                Section("Den Lens") { PhaseView(flow: model.flow) }
                if model.inbox.paired { InboxSection(inbox: model.inbox) }
                if model.glasses.usingMock, let preview = model.glasses.mockPreview {
                    Section("Simulated lens") { PreviewHost(view: preview).frame(height: 300) }
                }
            }
            .navigationTitle("Den Lens")
            .toolbar {
                Button("Settings", systemImage: "gearshape") { showSettings = true }
            }
            .sheet(isPresented: $showSettings) { SettingsView(model: model) }
            .alert("Glasses error", isPresented: Binding(
                get: { model.glasses.errorMessage != nil },
                set: { if !$0 { model.glasses.errorMessage = nil } }
            )) {
                Button("OK") { model.glasses.errorMessage = nil }
            } message: { Text(model.glasses.errorMessage ?? "") }
        }
    }

    private var glassesSection: some View {
        Section("Glasses") {
            LabeledContent("Status", value: model.glasses.status)
            LabeledContent("Registration", value: model.glasses.registration.description)
            if model.glasses.registration != .registered && !model.glasses.usingMock {
                Button("Connect with Meta AI") { Task { await model.glasses.register() } }
            }
            Toggle("Simulated glasses", isOn: Binding(get: { model.glasses.usingMock }, set: { model.setMock($0) }))
            if let e = model.mockError { Text(e).foregroundStyle(.red).font(.footnote) }
            if !model.hasKey { Label("Add your API key in Settings", systemImage: "key").foregroundStyle(.orange) }
        }
    }
}

/// Phone mirror of everything the lens shows, plus the same actions.
struct PhaseView: View {
    var flow: LensFlow

    var body: some View {
        switch flow.phase {
        case .idle:
            HStack {
                Button("Ask Den", systemImage: "eyeglasses") { flow.start() }.buttonStyle(.borderedProminent)
                Button("Describe", systemImage: "text.viewfinder") { flow.describe() }.buttonStyle(.bordered)
            }
        case .photoRequest(let r):
            VStack(alignment: .leading, spacing: 8) {
                Text("\(r.requester) wants a photo").font(.headline)
                Text(r.reason)
                HStack {
                    Button("Send photo", systemImage: "camera") { flow.answerPhoto(send: true) }.buttonStyle(.borderedProminent)
                    Button("Decline", role: .cancel) { flow.answerPhoto(send: false) }
                }
            }
        case .connecting:
            busy("Connecting to glasses")
        case .listening(let partial):
            busy(partial.isEmpty ? "Listening" : partial)
            Button("Cancel", role: .cancel) { flow.cancel() }
        case .capturing:
            busy("Looking")
        case .understanding(let utterance):
            busy("Searching: \(utterance)")
        case .answered(let answer, let replies):
            VStack(alignment: .leading, spacing: 8) {
                Text(answer.answer).font(.headline)
                ForEach(Array(answer.steps.enumerated()), id: \.offset) { i, step in
                    Text("\(i + 1). \(step)").font(.subheadline)
                }
                if let crew = answer.crew {
                    Label(crew.sentTo.isEmpty ? "None of your agents are online" : "Sent to \(crew.sentTo.joined(separator: ", "))",
                          systemImage: "person.3").font(.footnote).foregroundStyle(.secondary)
                }
                ForEach(replies) { r in
                    VStack(alignment: .leading) {
                        Text(r.fromAgent).font(.caption).foregroundStyle(.secondary)
                        Text(r.body)
                    }
                }
                ForEach(answer.sources, id: \.url) { s in
                    if let url = URL(string: s.url) { Link(s.title, destination: url).font(.footnote) }
                }
                HStack {
                    if !answer.steps.isEmpty {
                        Button("Steps", systemImage: "list.number") { flow.showSteps() }.buttonStyle(.bordered)
                    }
                    if answer.hire != nil {
                        Button("Hire help", systemImage: "checkmark.circle") { flow.offerHire() }.buttonStyle(.borderedProminent)
                    }
                    Button("Done") { flow.cancel() }
                }
            }
        case .confirm(let intent):
            VStack(alignment: .leading, spacing: 8) {
                Text(intent.goal).font(.headline)
                Text("Budget \(dollars(intent.budgetCents))").foregroundStyle(.secondary)
                if let crop = FramePrep.decodeCrop(b64: intent.cropJpegB64, maxSide: 360) {
                    Image(uiImage: crop).resizable().scaledToFit().clipShape(.rect(cornerRadius: 12))
                }
                HStack {
                    Button(Cards.hireLabel, systemImage: "checkmark.circle") { flow.confirm() }.buttonStyle(.borderedProminent)
                    Button("Cancel", role: .cancel) { flow.cancel() }
                }
            }
        case .hiring:
            busy("Hiring")
        case .approve(let request, _, let note):
            VStack(alignment: .leading, spacing: 8) {
                Text("Approve the plan").font(.headline)
                Text("Up to \(dollars(request.summary.budgetCents))").foregroundStyle(.secondary)
                ForEach(request.flagLabels, id: \.self) { Label($0, systemImage: "exclamationmark.triangle").foregroundStyle(.orange) }
                if !note.isEmpty { Text(note).font(.footnote).foregroundStyle(.secondary) }
                HStack {
                    Button(request.signatureRequired ? "Approve with Face ID" : "Approve", systemImage: "faceid") { flow.approveNow() }.buttonStyle(.borderedProminent)
                    Button("Decline", role: .destructive) { flow.declineNow() }
                }
            }
        case .signing:
            busy("Waiting for Face ID")
        case .running(let contract, let note):
            VStack(alignment: .leading, spacing: 6) {
                Text("\(dollars(contract.spentCents)) of \(dollars(flow.budgetCents))").font(.headline)
                ForEach(contract.jobs) { job in
                    Text("\(job.capability) · \(job.assignedAgent ?? "open") · \(job.state)").font(.subheadline)
                }
                if !note.isEmpty { Text(note).font(.footnote).foregroundStyle(.secondary) }
            }
        case .guide(let pages, let index):
            VStack(alignment: .leading, spacing: 8) {
                Text("Step \(index + 1) of \(pages.count)" + (flow.live == nil ? "" : " · Den is watching")).font(.caption).foregroundStyle(.secondary)
                if let note = flow.live, !note.isEmpty { Text(note).foregroundStyle(.orange) }
                Text(pages[index].title).font(.headline)
                if let image = flow.diagram(at: index) {
                    Image(uiImage: image).resizable().scaledToFit().frame(maxHeight: 220)
                } else if let url = pages[index].imageURL {
                    AsyncImage(url: url) { $0.resizable().scaledToFit() } placeholder: { ProgressView() }.frame(maxHeight: 220)
                }
                Text(pages[index].body)
                HStack {
                    Button("Back", systemImage: "chevron.left") { flow.step(-1) }.disabled(index == 0)
                    Button(flow.live == nil ? "Watch me" : "Stop", systemImage: flow.live == nil ? "eye" : "eye.slash") { flow.toggleLive() }
                    Button(index + 1 == pages.count ? "Finish" : "Next", systemImage: "chevron.right") { flow.step(1) }.buttonStyle(.borderedProminent)
                }
            }
        case .finished(let spent, let refund):
            VStack(alignment: .leading, spacing: 8) {
                Text("Done").font(.headline)
                Text("Spent \(dollars(spent)) of \(dollars(flow.budgetCents)). Refunded \(dollars(refund)).")
                Button("New request") { flow.reset() }
            }
        case .failed(let message):
            VStack(alignment: .leading, spacing: 8) {
                Text(message).foregroundStyle(.red)
                Button("Try again") { flow.start() }
            }
        }
    }

    private func busy(_ text: String) -> some View {
        HStack { ProgressView(); Text(text) }
    }
}

/// Pending Agent Secret Store requests. Each button asks for Face ID to sign.
struct InboxSection: View {
    var inbox: ApproverInbox

    var body: some View {
        Section("Approvals") {
            if inbox.items.isEmpty { Text(inbox.status.isEmpty ? "No pending approvals." : inbox.status).foregroundStyle(.secondary) }
            ForEach(inbox.items) { item in
                VStack(alignment: .leading, spacing: 6) {
                    Text(item.title).font(.headline)
                    ForEach(item.lines, id: \.self) { Text($0).font(.subheadline).foregroundStyle(.secondary) }
                    HStack {
                        Button("Approve", systemImage: "faceid") { Task { await inbox.decide(item, approve: true) } }.buttonStyle(.borderedProminent)
                        Button("Deny", role: .destructive) { Task { await inbox.decide(item, approve: false) } }
                    }.disabled(inbox.busy)
                }
            }
        }
        .task { await inbox.refresh() }
    }
}

struct SettingsView: View {
    var model: AppModel
    @State private var key = ""
    @State private var readAloud = LensFlow.readAloud
    @State private var pairingCode = ""
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("Agent API key") {
                    SecureField(model.hasKey ? "Saved in Keychain (enter to replace)" : "X-API-Key", text: $key)
                        .textContentType(.password)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                }
                Section("Approver") {
                    Text(model.approverStatus).font(.footnote)
                    if model.canEnrollApprover {
                        Button("Register this phone as approver", systemImage: "faceid") { Task { await model.enrollApprover() } }
                    }
                }
                Section("Agent Secret Store approvals") {
                    Text(model.inbox.paired ? "Paired. Secret and signing requests arrive here and as time-sensitive notifications." : "In the Agent Secret Store dashboard create a pairing code, then enter it here.").font(.footnote)
                    if !model.inbox.paired && model.approvals != nil {
                        TextField("Pairing code", text: $pairingCode).textInputAutocapitalization(.characters).autocorrectionDisabled()
                        Button("Pair this phone", systemImage: "faceid") { Task { await model.inbox.pair(code: pairingCode); pairingCode = "" } }.disabled(pairingCode.count < 6)
                    }
                    if !model.inbox.status.isEmpty { Text(model.inbox.status).font(.footnote).foregroundStyle(.secondary) }
                }
                Section { Text("Stored only in this phone's Keychain. Requests go to api.moltbotden.com.").font(.footnote) }
                Section { Toggle("Read answers aloud", isOn: $readAloud) }
                CrewSection(api: model.flow.api)
                GlassesAppSection(api: model.flow.api)
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        if !key.isEmpty { model.saveKey(key) }
                        LensFlow.readAloud = readAloud
                        dismiss()
                    }
                }
            }
        }
    }
}

/// "My agents": an invite any agent on Moltbot Den (OpenClaw, Claude Code, MCP) can act on to join the crew.
struct CrewSection: View {
    let api: LensAPI
    @State private var invite: CrewInvite?
    @State private var error: String?
    @State private var loading = false

    var body: some View {
        Section {
            if let invite {
                Text("Crew \(invite.crew): \(invite.members.count) member\(invite.members.count == 1 ? "" : "s")").font(.subheadline)
                Text(invite.message).font(.footnote).textSelection(.enabled)
                HStack {
                    Button("Copy", systemImage: "doc.on.doc") { UIPasteboard.general.string = invite.message }
                    ShareLink(item: invite.message)
                }
            }
            Button(invite == nil ? "Add my agents" : "New invite", systemImage: "person.badge.plus") {
                loading = true
                Task {
                    do { invite = try await api.crewInvite(); error = nil } catch { self.error = error.localizedDescription }
                    loading = false
                }
            }.disabled(loading)
            if let error { Text(error).font(.footnote).foregroundStyle(.red) }
        } header: { Text("My agents") } footer: {
            Text("Send the invite to your agent. Once it joins, Den can hand it tasks and its replies show on your glasses.")
        }
    }
}

/// "Hey Meta, ask Den": a link for the Meta AI app (App Connections) that opens Den on Ray-Ban Display glasses.
struct GlassesAppSection: View {
    let api: LensAPI
    @State private var link: GlassesLink?
    @State private var error: String?
    @State private var loading = false

    var body: some View {
        Section {
            if let link {
                Text(link.url.absoluteString).font(.footnote).textSelection(.enabled)
                HStack {
                    Button("Copy", systemImage: "doc.on.doc") { UIPasteboard.general.url = link.url }
                    ShareLink(item: link.url)
                }
            }
            Button(link == nil ? "Add Den to glasses" : "New link", systemImage: "eyeglasses") {
                loading = true
                Task {
                    do { link = try await api.glassesLink(); error = nil } catch { self.error = error.localizedDescription }
                    loading = false
                }
            }.disabled(loading)
            if let error { Text(error).font(.footnote).foregroundStyle(.red) }
        } header: { Text("Den on Ray-Ban Display") } footer: {
            Text("Add the link in the Meta AI app under App Connections. It works for 30 days; a new link turns the old one off.")
        }
    }
}

struct PreviewHost: UIViewRepresentable {
    let view: UIView
    func makeUIView(context: Context) -> UIView { view }
    func updateUIView(_ uiView: UIView, context: Context) {}
}
