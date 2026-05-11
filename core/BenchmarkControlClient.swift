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

    func connect(to urlString: String) {
        guard let url = URL(string: urlString) else {
            errorHandler?("Controller URL is invalid.")
            statusDidChange?(.failed)
            return
        }

        disconnect()
        statusDidChange?(.connecting)

        let session = URLSession(configuration: .default, delegate: self, delegateQueue: OperationQueue())
        let task = session.webSocketTask(with: url)
        self.session = session
        self.webSocketTask = task
        task.resume()
        receiveNextMessage()
    }

    func disconnect() {
        webSocketTask?.cancel(with: .goingAway, reason: nil)
        webSocketTask = nil
        session?.invalidateAndCancel()
        session = nil
        if isConnected {
            isConnected = false
            DispatchQueue.main.async {
                self.statusDidChange?(.disconnected)
            }
        }
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

    private func receiveNextMessage() {
        guard let webSocketTask else { return }

        Task {
            do {
                let message = try await webSocketTask.receive()
                try await handle(message: message)
                receiveNextMessage()
            } catch {
                await MainActor.run {
                    self.isConnected = false
                    self.statusDidChange?(.failed)
                    self.errorHandler?("Controller receive failed: \(error.localizedDescription)")
                }
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
        isConnected = true
        DispatchQueue.main.async {
            self.statusDidChange?(.connected)
        }
    }

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask, didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?) {
        isConnected = false
        DispatchQueue.main.async {
            self.statusDidChange?(.disconnected)
        }
    }
}
