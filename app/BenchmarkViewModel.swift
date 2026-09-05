import Foundation

@MainActor
@Observable
final class BenchmarkViewModel {
    /// Default controller and backend endpoints.
    ///
    /// Both are only the *initial* values: they are editable in the app and
    /// persisted per device, so a lab address never needs to be committed. To
    /// preset them for a build, add `BenchmarkControllerURL` and
    /// `BenchmarkBackendBaseURL` to the target's Info.plist; otherwise the
    /// placeholder host below applies and can be overwritten in the UI.
    static let labControllerURLString = configuredDefault(
        infoKey: "BenchmarkControllerURL",
        fallback: "ws://benchmark-controller.local:8765"
    )
    static let labBackendBaseURLString = configuredDefault(
        infoKey: "BenchmarkBackendBaseURL",
        fallback: "http://benchmark-controller.local:8000"
    )
    static let labChurchID = "benchmark-lab"

    private static func configuredDefault(infoKey: String, fallback: String) -> String {
        guard let raw = Bundle.main.object(forInfoDictionaryKey: infoKey) as? String else {
            return fallback
        }
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? fallback : trimmed
    }

    private enum DefaultsKey {
        static let runMode = "BenchmarkViewModel.runMode"
        static let controllerURLString = "BenchmarkViewModel.controllerURLString"
        static let backendBaseURLString = "BenchmarkViewModel.backendBaseURLString"
        static let churchID = "BenchmarkViewModel.churchID"
        static let autoConnectToControllerOnLaunch = "BenchmarkViewModel.autoConnectToControllerOnLaunch"
        static let lastRunSpec = "BenchmarkViewModel.lastRunSpec"
    }

    let availablePipelines = BenchmarkPipelineID.allCases.map(\.profile)
    var runMode: BenchmarkRunMode = .controllerWait {
        didSet {
            guard !isRestoringPersistedSettings else { return }
            persistSettings()
        }
    }
    var controllerURLString = BenchmarkViewModel.labControllerURLString {
        didSet {
            guard !isRestoringPersistedSettings else { return }
            persistSettings()
        }
    }
    var backendBaseURLString = BenchmarkViewModel.labBackendBaseURLString {
        didSet {
            guard !isRestoringPersistedSettings else { return }
            persistSettings()
        }
    }
    var churchID = BenchmarkViewModel.labChurchID {
        didSet {
            guard !isRestoringPersistedSettings else { return }
            persistSettings()
        }
    }
    var autoConnectToControllerOnLaunch = true {
        didSet {
            guard !isRestoringPersistedSettings else { return }
            persistSettings()
            if autoConnectToControllerOnLaunch {
                ensureControllerConnection(resetLastError: false)
            }
        }
    }
    var selectedPipeline: BenchmarkPipelineID = .appleAECOnly {
        didSet {
            guard !isRestoringPersistedRunSpec else { return }
            activeRunSpec = Self.makeSampleRunSpec(for: selectedPipeline)
        }
    }
    var controllerStatus: ControllerConnectionStatus = .disconnected
    var streamStatus: BenchmarkStreamStatus = .idle
    var backendSessionID: Int?
    var runState: BenchmarkRunState = .idle
    var activeRunSpec: BenchmarkRunSpec? = .sample
    var queuedRunSpecs: [BenchmarkRunSpec] = []
    var telemetry = BenchmarkTelemetrySnapshot.placeholder
    var lastError: String?

    private let captureManager = BenchmarkAudioCaptureManager()
    private let controlClient = BenchmarkControlClient()
    private let streamClient = BenchmarkStreamSocketClient()
    private let defaults = UserDefaults.standard
    private var telemetrySendTask: Task<Void, Never>?
    private var pendingTelemetrySnapshot: BenchmarkTelemetrySnapshot?
    private var isRestoringPersistedRunSpec = false
    private var isRestoringPersistedSettings = false

    init() {
        restorePersistedSettings()
        captureManager.telemetryDidChange = { [weak self] telemetry in
            self?.handleTelemetryUpdate(telemetry)
        }
        captureManager.errorHandler = { [weak self] message in
            self?.lastError = message
            self?.runState = .failed
        }
        captureManager.audioChunkHandler = { [weak self] envelope in
            self?.handleAudioChunk(envelope)
        }
        controlClient.statusDidChange = { [weak self] status in
            Task { @MainActor in
                self?.controllerStatus = status
            }
        }
        controlClient.runSpecReceived = { [weak self] runSpec in
            Task { @MainActor in
                self?.acceptController(runSpec: runSpec)
            }
        }
        controlClient.playbackStarted = { [weak self] message in
            Task { @MainActor in
                self?.beginControllerRun(for: message.runID)
            }
        }
        controlClient.ackReceived = { [weak self] _ in
            Task { @MainActor in
                if self?.runState == .completed {
                    self?.runState = .idle
                }
            }
        }
        controlClient.errorHandler = { [weak self] message in
            Task { @MainActor in
                self?.lastError = message
                self?.controllerStatus = .failed
            }
        }
        Task {
            await streamClient.setHandlers(
                statusHandler: { [weak self] status in
                    Task { @MainActor in
                        self?.streamStatus = status
                    }
                },
                messageHandler: { [weak self] message in
                    Task { @MainActor in
                        self?.lastError = message
                    }
                },
                sessionIDHandler: { [weak self] sessionID in
                    Task { @MainActor in
                        self?.backendSessionID = sessionID
                    }
                }
            )
        }
        if let persistedRunSpec = loadPersistedRunSpec() {
            isRestoringPersistedRunSpec = true
            selectedPipeline = persistedRunSpec.pipelineID
            activeRunSpec = persistedRunSpec
            isRestoringPersistedRunSpec = false
        } else {
            activeRunSpec = Self.makeSampleRunSpec(for: selectedPipeline)
        }
        if autoConnectToControllerOnLaunch {
            ensureControllerConnection(resetLastError: false)
        }
    }

    var selectedPipelineProfile: BenchmarkPipelineProfile {
        selectedPipeline.profile
    }

    func prepareCompactSessionQueue() {
        queuedRunSpecs = [
            Self.makeSampleRunSpec(for: .appleAECOnly),
            Self.makeSampleRunSpec(for: .appleAECPlusCurrentCleanup),
            Self.makeSampleRunSpec(for: .rawDebug),
            Self.makeSampleRunSpec(for: .deepFilterNet3Only),
            Self.makeSampleRunSpec(for: .appleAECPlusDeepFilterNet3),
        ]
    }

    func startSampleRun() {
        guard let activeRunSpec else { return }
        startLocalRun(using: activeRunSpec)
    }

    func runQueuedSampleSession() {
        if queuedRunSpecs.isEmpty {
            prepareCompactSessionQueue()
        }
        guard !queuedRunSpecs.isEmpty else {
            return
        }

        runState = .preparing
        Task { [weak self] in
            guard let self else { return }
            do {
                for runSpec in queuedRunSpecs {
                    self.activeRunSpec = runSpec
                    self.selectedPipeline = runSpec.pipelineID
                    try await self.connectStream(for: runSpec)
                    try await captureManager.start(runSpec: runSpec)
                    self.runState = .running
                    try await Task.sleep(for: .milliseconds(runSpec.runDurationMilliseconds))
                    captureManager.stop()
                    await self.streamClient.disconnect()
                    try await Task.sleep(for: .milliseconds(250))
                }
                self.runState = .completed
            } catch {
                await self.streamClient.disconnect()
                self.lastError = error.localizedDescription
                self.runState = .failed
            }
        }
    }

    func stopRun() {
        telemetrySendTask?.cancel()
        telemetrySendTask = nil
        pendingTelemetrySnapshot = nil
        captureManager.stop()
        Task {
            await streamClient.disconnect()
        }
        runState = .idle
    }

    func connectToController() {
        runMode = .controllerWait
        reconnectToController()
    }

    func disconnectFromController() {
        controlClient.disconnect()
        runMode = .manual
    }

    func ensureControllerConnection(resetLastError: Bool = true) {
        guard controllerStatus != .connected, controllerStatus != .connecting else { return }
        if resetLastError {
            lastError = nil
        }
        runMode = .controllerWait
        controlClient.connect(to: controllerURLString)
    }

    func reconnectToController(resetLastError: Bool = true) {
        if resetLastError {
            lastError = nil
        }
        runMode = .controllerWait
        controlClient.connect(to: controllerURLString)
    }

    func handleAppDidBecomeActive() {
        guard autoConnectToControllerOnLaunch else { return }
        ensureControllerConnection(resetLastError: false)
    }

    func restoreLabDefaults() {
        controllerURLString = Self.labControllerURLString
        backendBaseURLString = Self.labBackendBaseURLString
        churchID = Self.labChurchID
        autoConnectToControllerOnLaunch = true
        reconnectToController()
    }

    private static func makeSampleRunSpec(for pipelineID: BenchmarkPipelineID) -> BenchmarkRunSpec {
        BenchmarkRunSpec(
            benchmarkSessionID: "sample-session",
            runID: "sample-\(pipelineID.rawValue)",
            scenarioID: "sample-scenario",
            pipelineID: pipelineID,
            expectedTranscript: "For God so loved the world",
            sttSampleRate: 16_000,
            chunkDurationMilliseconds: 100,
            runDurationMilliseconds: 5_000,
            saveServerCapture: true,
            serverCaptureLabel: "sample-scenario-\(pipelineID.rawValue)",
            controllerStartedAt: nil,
            sttConfig: .default,
            micProfile: nil,
            dfn3Tuning: nil
        )
    }

    private func startLocalRun(using runSpec: BenchmarkRunSpec) {
        runState = .preparing
        lastError = nil
        Task { [weak self] in
            guard let self else { return }
            do {
                try await self.connectStream(for: runSpec)
                try await captureManager.start(runSpec: runSpec)
                self.runState = .running
            } catch {
                await streamClient.disconnect()
                self.lastError = error.localizedDescription
                self.runState = .failed
            }
        }
    }

    private func handleTelemetryUpdate(_ telemetry: BenchmarkTelemetrySnapshot) {
        self.telemetry = telemetry
        guard controllerStatus == .connected, runState == .running, activeRunSpec != nil else { return }

        pendingTelemetrySnapshot = telemetry
        guard telemetrySendTask == nil else { return }

        telemetrySendTask = Task { [weak self, controlClient] in
            guard let self else { return }
            defer {
                self.telemetrySendTask = nil
            }

            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(250))
                guard !Task.isCancelled else { return }
                guard self.controllerStatus == .connected, self.runState == .running else { return }
                guard let activeRunID = self.activeRunSpec?.runID else { return }
                guard let latestSnapshot = self.pendingTelemetrySnapshot else { return }
                self.pendingTelemetrySnapshot = nil
                controlClient.sendTelemetry(runID: activeRunID, snapshot: latestSnapshot)
                if self.pendingTelemetrySnapshot == nil {
                    return
                }
            }
        }
    }

    private func acceptController(runSpec: BenchmarkRunSpec) {
        persistLastRunSpec(runSpec)
        selectedPipeline = runSpec.pipelineID
        activeRunSpec = runSpec
        lastError = nil
        runMode = .controllerWait
        runState = .ready
        controlClient.sendReady(for: runSpec)
    }

    private func beginControllerRun(for runID: String) {
        guard let activeRunSpec else {
            controlClient.sendRunRejected(runID: runID, reason: "No active run spec is loaded on the device.")
            return
        }
        guard activeRunSpec.runID == runID else {
            controlClient.sendRunRejected(runID: runID, reason: "Playback was started for an unexpected run identifier.")
            return
        }

        lastError = nil
        runState = .preparing
        Task { [weak self] in
            guard let self else { return }
            do {
                try await self.connectStream(for: activeRunSpec)
                try await captureManager.start(runSpec: activeRunSpec)
                self.runState = .running
                try await Task.sleep(for: .milliseconds(activeRunSpec.runDurationMilliseconds))
                self.captureManager.stop()
                await self.streamClient.disconnect()
                let result = self.makeRunResult(for: activeRunSpec, status: "completed")
                self.runState = .completed
                self.controlClient.sendRunResult(result)
            } catch {
                self.captureManager.stop()
                await self.streamClient.disconnect()
                self.lastError = error.localizedDescription
                self.runState = .failed
                let result = self.makeRunResult(for: activeRunSpec, status: "failed", extraErrors: [error.localizedDescription])
                self.controlClient.sendRunResult(result)
            }
        }
    }

    private func makeRunResult(for runSpec: BenchmarkRunSpec, status: String, extraErrors: [String] = []) -> BenchmarkRunResult {
        var warnings: [String] = []
        if !telemetry.fallbackReason.isEmpty {
            warnings.append(telemetry.fallbackReason)
        }
        if runSpec.saveServerCapture {
            warnings.append("Server-side capture was requested for this run; backend persistence still needs to honor the benchmark capture label.")
        }

        var errors = extraErrors
        if let lastError, !lastError.isEmpty {
            errors.append(lastError)
        }

        return BenchmarkRunResult(
            runID: runSpec.runID,
            pipelineID: runSpec.pipelineID,
            status: status,
            firstPartialLatencyMilliseconds: nil,
            firstFinalLatencyMilliseconds: nil,
            wordErrorRate: nil,
            characterErrorRate: nil,
            finalTranscript: "",
            warnings: warnings,
            errors: errors
        )
    }

    private func connectStream(for runSpec: BenchmarkRunSpec) async throws {
        guard let baseURL = URL(string: backendBaseURLString) else {
            throw NSError(domain: "ChurchBridgeAudioBench", code: 30, userInfo: [NSLocalizedDescriptionKey: "Backend base URL is invalid."])
        }
        try await streamClient.connect(
            configuration: .init(
                baseURL: baseURL,
                churchID: churchID,
                sampleRate: runSpec.sttSampleRate,
                sourceScriptureVersion: "rvr1960",
                displayScriptureVersion: "kjv"
            ),
            runSpec: runSpec
        )
    }

    private func handleAudioChunk(_ envelope: BenchmarkAudioChunkEnvelope) {
        Task { [streamClient] in
            _ = await streamClient.sendAudio(base64Float32: envelope.base64)
        }
    }

    private func restorePersistedSettings() {
        isRestoringPersistedSettings = true
        defer { isRestoringPersistedSettings = false }
        if let rawRunMode = defaults.string(forKey: DefaultsKey.runMode),
           let persistedRunMode = BenchmarkRunMode(rawValue: rawRunMode) {
            runMode = persistedRunMode
        }
        controllerURLString = restoredString(forKey: DefaultsKey.controllerURLString, fallback: Self.labControllerURLString)
        backendBaseURLString = restoredString(forKey: DefaultsKey.backendBaseURLString, fallback: Self.labBackendBaseURLString)
        churchID = restoredString(forKey: DefaultsKey.churchID, fallback: Self.labChurchID)
        if defaults.object(forKey: DefaultsKey.autoConnectToControllerOnLaunch) != nil {
            autoConnectToControllerOnLaunch = defaults.bool(forKey: DefaultsKey.autoConnectToControllerOnLaunch)
        } else {
            autoConnectToControllerOnLaunch = true
        }
    }

    private func loadPersistedRunSpec() -> BenchmarkRunSpec? {
        guard let data = defaults.data(forKey: DefaultsKey.lastRunSpec) else {
            return nil
        }
        return try? JSONDecoder().decode(BenchmarkRunSpec.self, from: data)
    }

    private func persistLastRunSpec(_ runSpec: BenchmarkRunSpec) {
        guard let data = try? JSONEncoder().encode(runSpec) else { return }
        defaults.set(data, forKey: DefaultsKey.lastRunSpec)
    }

    private func persistSettings() {
        defaults.set(runMode.rawValue, forKey: DefaultsKey.runMode)
        defaults.set(controllerURLString, forKey: DefaultsKey.controllerURLString)
        defaults.set(backendBaseURLString, forKey: DefaultsKey.backendBaseURLString)
        defaults.set(churchID, forKey: DefaultsKey.churchID)
        defaults.set(autoConnectToControllerOnLaunch, forKey: DefaultsKey.autoConnectToControllerOnLaunch)
    }

    private func restoredString(forKey key: String, fallback: String) -> String {
        let restored = defaults.string(forKey: key)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return restored.isEmpty ? fallback : restored
    }
}
