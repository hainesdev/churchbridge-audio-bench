import Foundation

@MainActor
@Observable
final class BenchmarkViewModel {
    let availablePipelines = BenchmarkPipelineID.allCases.map(\.profile)
    var runMode: BenchmarkRunMode = .manual
    var controllerURLString = "ws://192.168.1.2:8765"
    var backendBaseURLString = "http://127.0.0.1:8000"
    var churchID = "benchmark-lab"
    var selectedPipeline: BenchmarkPipelineID = .appleAECOnly {
        didSet {
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
    private var telemetrySendTask: Task<Void, Never>?

    init() {
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
    }

    var selectedPipelineProfile: BenchmarkPipelineProfile {
        selectedPipeline.profile
    }

    func prepareCompactSessionQueue() {
        queuedRunSpecs = [
            Self.makeSampleRunSpec(for: .appleAECOnly),
            Self.makeSampleRunSpec(for: .appleAECPlusCurrentCleanup),
            Self.makeSampleRunSpec(for: .rawDebug),
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
        captureManager.stop()
        Task {
            await streamClient.disconnect()
        }
        runState = .idle
    }

    func connectToController() {
        runMode = .controllerWait
        controlClient.connect(to: controllerURLString)
    }

    func disconnectFromController() {
        controlClient.disconnect()
        runMode = .manual
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
            controllerStartedAt: nil
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
        guard controllerStatus == .connected, runState == .running, let activeRunSpec else { return }

        telemetrySendTask?.cancel()
        telemetrySendTask = Task { [controlClient] in
            try? await Task.sleep(for: .milliseconds(150))
            if Task.isCancelled {
                return
            }
            controlClient.sendTelemetry(runID: activeRunSpec.runID, snapshot: telemetry)
        }
    }

    private func acceptController(runSpec: BenchmarkRunSpec) {
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
        await streamClient.connect(
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
}
