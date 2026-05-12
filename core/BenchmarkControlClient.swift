import Foundation
#if os(iOS)
import UIKit
#endif

struct ControllerHelloMessage: Codable {
    let type: String
    let protocolVersion: Int

    enum CodingKeys: String, CodingKey {
        case type
        case protocolVersion = "protocol_version"
    }
}

struct ControllerPlaybackStartedMessage: Codable {
    let type: String
    let runID: String
    let startedAt: Date?

    enum CodingKeys: String, CodingKey {
        case type
        case runID = "run_id"
        case startedAt = "started_at"
    }
}

struct ControllerAckMessage: Codable {
    let type: String
    let runID: String?
    let detail: String?

    enum CodingKeys: String, CodingKey {
        case type
        case runID = "run_id"
        case detail
    }
}

struct BenchmarkDeviceHelloMessage: Codable {
    let type = "device_hello"
    let protocolVersion: Int
    let deviceName: String
    let systemVersion: String
    let appVersion: String

    enum CodingKeys: String, CodingKey {
        case type
        case protocolVersion = "protocol_version"
        case deviceName = "device_name"
        case systemVersion = "system_version"
        case appVersion = "app_version"
    }
}

struct BenchmarkReadyMessage: Codable {
    let type = "ready"
    let runID: String
    let pipelineID: BenchmarkPipelineID
    let saveServerCapture: Bool
    let serverCaptureLabel: String?

    enum CodingKeys: String, CodingKey {
        case type
        case runID = "run_id"
        case pipelineID = "pipeline_id"
        case saveServerCapture = "save_server_capture"
        case serverCaptureLabel = "server_capture_label"
    }
}

struct BenchmarkRunRejectedMessage: Codable {
    let type = "run_rejected"
    let runID: String?
    let reason: String

    enum CodingKeys: String, CodingKey {
        case type
        case runID = "run_id"
        case reason
    }
}

struct BenchmarkTelemetryMessage: Codable {
    let type = "telemetry"
    let runID: String
    let snapshot: BenchmarkTelemetrySnapshot

    enum CodingKeys: String, CodingKey {
        case type
        case runID = "run_id"
        case snapshot
    }
}

final class BenchmarkControlClient: NSObject {
    var statusDidChange: ((ControllerConnectionStatus) -> Void)?
    var runSpecReceived: ((BenchmarkRunSpec) -> Void)?
    var playbackStarted: ((ControllerPlaybackStartedMessage) -> Void)?
    var ackReceived: ((ControllerAckMessage) -> Void)?
    var errorHandler: ((String) -> Void)?

    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }()

    private let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }()

    private var session: URLSession?
    private var webSocketTask: URLSessionWebSocketTask?
    private var isConnected = false
    private var desiredURLString: String?
    private var reconnectTask: Task<Void, Never>?
    private var reconnectAttemptCount = 0
    private var manualDisconnect = false

    func connect(to urlString: String) {
        desiredURLString = urlString
        manualDisconnect = false
        reconnectTask?.cancel()
        reconnectTask = nil
        openConnection(to: urlString)
    }

    func disconnect() {
        manualDisconnect = true
        desiredURLString = nil
        reconnectAttemptCount = 0
        reconnectTask?.cancel()
        reconnectTask = nil
        teardownCurrentConnection(notifyDisconnected: true)
    }

    private func openConnection(to urlString: String) {
        guard let url = URL(string: urlString) else {
            errorHandler?("Controller URL is invalid.")
            statusDidChange?(.failed)
            return
        }

        teardownCurrentConnection(notifyDisconnected: false)
        statusDidChange?(.connecting)

        let session = URLSession(configuration: .default, delegate: self, delegateQueue: OperationQueue())
        let task = session.webSocketTask(with: url)
        self.session = session
        self.webSocketTask = task
        task.resume()
        receiveNextMessage(for: task)
    }

    func sendReady(for runSpec: BenchmarkRunSpec) {
        let message = BenchmarkReadyMessage(
            runID: runSpec.runID,
            pipelineID: runSpec.pipelineID,
            saveServerCapture: runSpec.saveServerCapture,
            serverCaptureLabel: runSpec.serverCaptureLabel
        )
        send(message)
    }

    func sendRunRejected(runID: String?, reason: String) {
        send(BenchmarkRunRejectedMessage(runID: runID, reason: reason))
    }

    func sendTelemetry(runID: String, snapshot: BenchmarkTelemetrySnapshot) {
        guard isConnected else { return }
        send(BenchmarkTelemetryMessage(runID: runID, snapshot: snapshot))
    }

    func sendRunResult(_ result: BenchmarkRunResult) {
        send(result)
    }

    private func sendDeviceHello() {
        #if os(iOS)
        let deviceName = UIDevice.current.name
        let systemVersion = "\(UIDevice.current.systemName) \(UIDevice.current.systemVersion)"
        #else
        let deviceName = ProcessInfo.processInfo.hostName
        let systemVersion = ProcessInfo.processInfo.operatingSystemVersionString
        #endif
        let appVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.1.0"
        send(
            BenchmarkDeviceHelloMessage(
                protocolVersion: 1,
                deviceName: deviceName,
                systemVersion: systemVersion,
                appVersion: appVersion
            )
        )
    }

    private func send<T: Encodable>(_ payload: T) {
        guard let webSocketTask else { return }

        Task {
            do {
                let data = try encoder.encode(payload)
                guard let string = String(data: data, encoding: .utf8) else {
                    throw NSError(domain: "ChurchBridgeAudioBench", code: 20, userInfo: [NSLocalizedDescriptionKey: "Unable to encode controller payload as UTF-8."])
                }
                try await webSocketTask.send(.string(string))
            } catch {
                await MainActor.run {
                    self.errorHandler?("Controller send failed: \(error.localizedDescription)")
                    self.statusDidChange?(.failed)
                }
            }
        }
    }

    private func receiveNextMessage(for task: URLSessionWebSocketTask) {
        guard webSocketTask === task else { return }

        Task {
            do {
                let message = try await task.receive()
                try await handle(message: message)
                receiveNextMessage(for: task)
            } catch {
                await MainActor.run {
                    guard self.webSocketTask === task, !self.manualDisconnect else { return }
                    self.isConnected = false
                    self.statusDidChange?(.failed)
                    self.errorHandler?("Controller receive failed: \(error.localizedDescription)")
                    self.scheduleReconnect()
                }
            }
        }
    }

    private func teardownCurrentConnection(notifyDisconnected: Bool) {
        webSocketTask?.cancel(with: .goingAway, reason: nil)
        webSocketTask = nil
        session?.invalidateAndCancel()
        session = nil
        if isConnected || notifyDisconnected {
            isConnected = false
            DispatchQueue.main.async {
                self.statusDidChange?(.disconnected)
            }
        }
    }

    private func scheduleReconnect() {
        guard !manualDisconnect, let desiredURLString else { return }
        reconnectTask?.cancel()
        let nextAttempt = reconnectAttemptCount + 1
        reconnectAttemptCount = nextAttempt
        let delaySeconds = min(pow(2.0, Double(max(nextAttempt - 1, 0))), 5.0)
        reconnectTask = Task { [weak self, desiredURLString] in
            try? await Task.sleep(for: .seconds(delaySeconds))
            guard let strongSelf = self, !Task.isCancelled else { return }
            await MainActor.run {
                guard !strongSelf.manualDisconnect, strongSelf.desiredURLString == desiredURLString else { return }
                strongSelf.openConnection(to: desiredURLString)
            }
        }
    }

    private func handle(message: URLSessionWebSocketTask.Message) async throws {
        let data: Data
        switch message {
        case let .data(payload):
            data = payload
        case let .string(payload):
            guard let encoded = payload.data(using: .utf8) else {
                throw NSError(domain: "ChurchBridgeAudioBench", code: 21, userInfo: [NSLocalizedDescriptionKey: "Controller sent non-UTF8 text."])
            }
            data = encoded
        @unknown default:
            throw NSError(domain: "ChurchBridgeAudioBench", code: 22, userInfo: [NSLocalizedDescriptionKey: "Controller sent an unknown WebSocket message."])
        }

        let envelope = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let type = envelope?["type"] as? String else {
            throw NSError(domain: "ChurchBridgeAudioBench", code: 23, userInfo: [NSLocalizedDescriptionKey: "Controller payload is missing a type field."])
        }

        switch type {
        case "hello":
            _ = try decoder.decode(ControllerHelloMessage.self, from: data)
            await MainActor.run {
                self.sendDeviceHello()
            }
        case "run_spec":
            let runSpec = try decoder.decode(BenchmarkRunSpec.self, from: data)
            await MainActor.run {
                self.runSpecReceived?(runSpec)
            }
        case "playback_started":
            let started = try decoder.decode(ControllerPlaybackStartedMessage.self, from: data)
            await MainActor.run {
                self.playbackStarted?(started)
            }
        case "ack":
            let ack = try decoder.decode(ControllerAckMessage.self, from: data)
            await MainActor.run {
                self.ackReceived?(ack)
            }
        default:
            break
        }
    }
}

extension BenchmarkControlClient: URLSessionWebSocketDelegate {
    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask, didOpenWithProtocol protocol: String?) {
        guard webSocketTask === self.webSocketTask else { return }
        isConnected = true
        reconnectAttemptCount = 0
        DispatchQueue.main.async {
            self.statusDidChange?(.connected)
        }
    }

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask, didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?) {
        guard webSocketTask === self.webSocketTask else { return }
        isConnected = false
        DispatchQueue.main.async {
            self.statusDidChange?(.disconnected)
        }
        if !manualDisconnect {
            DispatchQueue.main.async {
                self.scheduleReconnect()
            }
        }
    }
}
