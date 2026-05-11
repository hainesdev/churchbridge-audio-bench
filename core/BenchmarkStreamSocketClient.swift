import Foundation

actor BenchmarkStreamSocketClient {
    struct StreamConfiguration: Sendable {
        let baseURL: URL
        let churchID: String
        let sampleRate: Int
        let sourceScriptureVersion: String
        let displayScriptureVersion: String
    }

    struct BenchmarkCaptureDescriptor: Encodable, Sendable {
        let enabled: Bool
        let sessionId: String
        let runId: String
        let scenarioId: String
        let pipelineId: String
        let captureLabel: String
    }

    struct AudioSendResult: Sendable {
        let success: Bool
        let encodedBytes: Int
        let decodedBytes: Int
    }

    private var task: URLSessionWebSocketTask?
    private var state: BenchmarkStreamStatus = .idle
    private var retryTask: Task<Void, Never>?
    private var configuration: StreamConfiguration?
    private var runSpec: BenchmarkRunSpec?
    private var messageHandler: (@Sendable (String) -> Void)?
    private var statusHandler: (@Sendable (BenchmarkStreamStatus) -> Void)?
    private var sessionIDHandler: (@Sendable (Int?) -> Void)?
    private let encoder = JSONEncoder()

    func setHandlers(
        statusHandler: @escaping @Sendable (BenchmarkStreamStatus) -> Void,
        messageHandler: @escaping @Sendable (String) -> Void,
        sessionIDHandler: @escaping @Sendable (Int?) -> Void
    ) {
        self.statusHandler = statusHandler
        self.messageHandler = messageHandler
        self.sessionIDHandler = sessionIDHandler
    }

    func connect(configuration: StreamConfiguration, runSpec: BenchmarkRunSpec) async {
        self.configuration = configuration
        self.runSpec = runSpec
        retryTask?.cancel()
        await openSocket(isReconnect: false)
    }

    func disconnect() async {
        retryTask?.cancel()
        retryTask = nil
        sessionIDHandler?(nil)
        if let task {
            try? await sendEncodable(StreamStopPayload(), over: task)
            task.cancel(with: .normalClosure, reason: nil)
        }
        task = nil
        transition(to: .idle)
    }

    func sendAudio(base64Float32: String) async -> AudioSendResult {
        let decodedBytes = Data(base64Encoded: base64Float32)?.count ?? 0
        let encodedBytes = base64Float32.utf8.count
        guard let task else {
            return AudioSendResult(success: false, encodedBytes: encodedBytes, decodedBytes: decodedBytes)
        }
        do {
            try await sendEncodable(StreamAudioPayload(audio: base64Float32), over: task)
            return AudioSendResult(success: true, encodedBytes: encodedBytes, decodedBytes: decodedBytes)
        } catch {
            await handleTransportFailure(
                message: "Audio send failed: \(error.localizedDescription)",
                failedTask: task
            )
            return AudioSendResult(success: false, encodedBytes: encodedBytes, decodedBytes: decodedBytes)
        }
    }

    private func openSocket(isReconnect: Bool) async {
        guard let configuration, let runSpec else { return }
        let requestURL = configuration.baseURL
            .appending(path: "api")
            .appending(path: "stream")
            .appending(path: "v1")
            .appending(queryItems: [URLQueryItem(name: "church_id", value: configuration.churchID)])

        sessionIDHandler?(nil)
        transition(to: isReconnect ? .reconnecting : .connecting)
        let task = URLSession.shared.webSocketTask(with: requestURL)
        self.task = task
        task.resume()

        do {
            let payload = StreamStartPayload(
                sampleRate: configuration.sampleRate,
                topic: "",
                sourceScriptureVersion: configuration.sourceScriptureVersion,
                displayScriptureVersion: configuration.displayScriptureVersion,
                benchmarkCapture: BenchmarkCaptureDescriptor(
                    enabled: runSpec.saveServerCapture,
                    sessionId: runSpec.benchmarkSessionID,
                    runId: runSpec.runID,
                    scenarioId: runSpec.scenarioID,
                    pipelineId: runSpec.pipelineID.rawValue,
                    captureLabel: runSpec.serverCaptureLabel ?? "\(runSpec.scenarioID)-\(runSpec.pipelineID.rawValue)"
                )
            )
            try await sendEncodable(payload, over: task)
            listen(on: task)
        } catch {
            await handleTransportFailure(
                message: "Stream connection failed: \(error.localizedDescription)",
                failedTask: task
            )
        }
    }

    private func listen(on task: URLSessionWebSocketTask) {
        Task {
            do {
                while self.task === task {
                    let message = try await task.receive()
                    switch message {
                    case let .string(string):
                        await handleInboundString(string)
                    case let .data(data):
                        if let string = String(data: data, encoding: .utf8) {
                            await handleInboundString(string)
                        }
                    @unknown default:
                        break
                    }
                }
            } catch {
                let shouldReconnect = self.state != .idle
                if shouldReconnect {
                    await handleTransportFailure(
                        message: "Stream socket dropped: \(error.localizedDescription)",
                        failedTask: task
                    )
                }
            }
        }
    }

    private func handleInboundString(_ string: String) async {
        guard let data = string.data(using: .utf8) else { return }
        if let started = try? JSONDecoder().decode(StreamSessionStartedEvent.self, from: data),
           started.type == "session_started" {
            transition(to: .connected)
            sessionIDHandler?(started.sessionId)
            return
        }
        if let error = try? JSONDecoder().decode(ErrorEvent.self, from: data),
           error.type == "error" {
            messageHandler?(error.message)
        }
    }

    private func scheduleReconnect() async {
        guard configuration != nil, runSpec != nil else { return }
        retryTask?.cancel()
        retryTask = Task {
            transition(to: .reconnecting)
            try? await Task.sleep(for: .seconds(2))
            await openSocket(isReconnect: true)
        }
    }

    private func transition(to nextState: BenchmarkStreamStatus) {
        state = nextState
        statusHandler?(nextState)
    }

    private func handleTransportFailure(
        message: String,
        failedTask: URLSessionWebSocketTask
    ) async {
        guard self.task === failedTask else { return }
        messageHandler?(message)
        sessionIDHandler?(nil)
        failedTask.cancel(with: .goingAway, reason: nil)
        self.task = nil
        transition(to: .failed)
        await scheduleReconnect()
    }

    private func sendEncodable<T: Encodable>(_ payload: T, over task: URLSessionWebSocketTask) async throws {
        let data = try encoder.encode(payload)
        guard let string = String(data: data, encoding: .utf8) else { return }
        try await task.send(.string(string))
    }
}

private struct StreamStartPayload: Encodable {
    let type = "session.start"
    let sampleRate: Int
    let topic: String
    let sourceScriptureVersion: String
    let displayScriptureVersion: String
    let benchmarkCapture: BenchmarkStreamSocketClient.BenchmarkCaptureDescriptor
}

private struct StreamStopPayload: Encodable {
    let type = "session.stop"
}

private struct StreamAudioPayload: Encodable {
    let type = "audio"
    let audio: String
}

private struct StreamSessionStartedEvent: Decodable {
    let type: String
    let sessionId: Int?
    let captureActive: Bool?
}

private struct ErrorEvent: Decodable {
    let type: String
    let message: String
}
