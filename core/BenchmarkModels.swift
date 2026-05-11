import Foundation

enum BenchmarkCaptureMode: String, CaseIterable, Identifiable, Codable, Sendable {
    case voiceProcessing = "Voice Processing"
    case echoCancelled = "Echo-Cancelled Input"
    case rawDebug = "Raw Debug"

    var id: String { rawValue }
}

enum BenchmarkPipelineFamily: String, CaseIterable, Codable, Sendable {
    case baseline
    case conservative
    case experimental
    case diagnostic

    var displayName: String {
        rawValue.capitalized
    }
}

enum BenchmarkAudioProcessingStrategy: String, CaseIterable, Identifiable, Codable, Sendable {
    case appleVoicePassthrough = "Apple Voice Passthrough"
    case robustVoiceFilter = "Robust Voice Filter"
    case persistentConverter = "Persistent Converter"
    case ephemeralConverter = "Ephemeral Converter"

    var id: String { rawValue }

    static let liveDefault: BenchmarkAudioProcessingStrategy = .robustVoiceFilter

    var targetSampleRate: Int {
        switch self {
        case .appleVoicePassthrough:
            return 48_000
        case .robustVoiceFilter, .persistentConverter, .ephemeralConverter:
            return 16_000
        }
    }
}

struct BenchmarkPipelineProfile: Sendable, Identifiable {
    let id: BenchmarkPipelineID
    let family: BenchmarkPipelineFamily
    let captureMode: BenchmarkCaptureMode
    let processingStrategy: BenchmarkAudioProcessingStrategy
    let summary: String

    var displayName: String { id.displayName }
}

enum BenchmarkPipelineID: String, CaseIterable, Codable, Identifiable, Sendable {
    case appleAECOnly = "apple_aec_only"
    case appleAECPlusCurrentCleanup = "apple_aec_plus_current_cleanup"
    case rawDebug = "raw_debug"
    case appleAECPlusDeepFilterNet3 = "apple_aec_plus_deepfilternet3"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .appleAECOnly:
            return "Apple AEC Only"
        case .appleAECPlusCurrentCleanup:
            return "Apple AEC + Current Cleanup"
        case .rawDebug:
            return "Raw Debug"
        case .appleAECPlusDeepFilterNet3:
            return "Apple AEC + DeepFilterNet3"
        }
    }

    var profile: BenchmarkPipelineProfile {
        switch self {
        case .appleAECOnly:
            return BenchmarkPipelineProfile(
                id: self,
                family: .baseline,
                captureMode: .voiceProcessing,
                processingStrategy: .persistentConverter,
                summary: "Apple voice processing plus explicit client-side sample-rate conversion."
            )
        case .appleAECPlusCurrentCleanup:
            return BenchmarkPipelineProfile(
                id: self,
                family: .conservative,
                captureMode: .voiceProcessing,
                processingStrategy: .robustVoiceFilter,
                summary: "Apple voice processing plus the current speech-focused cleanup path."
            )
        case .rawDebug:
            return BenchmarkPipelineProfile(
                id: self,
                family: .diagnostic,
                captureMode: .rawDebug,
                processingStrategy: .ephemeralConverter,
                summary: "Minimal diagnostic path used to expose raw or fallback behavior."
            )
        case .appleAECPlusDeepFilterNet3:
            return BenchmarkPipelineProfile(
                id: self,
                family: .experimental,
                captureMode: .voiceProcessing,
                processingStrategy: .robustVoiceFilter,
                summary: "Experimental path reserved for DFN3 integration and client-finalized output."
            )
        }
    }
}

enum BenchmarkRunMode: String, Codable, CaseIterable, Identifiable, Sendable {
    case manual
    case controllerWait = "controller_wait"
    case autorunLastSpec = "autorun_last_spec"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .manual:
            return "Manual"
        case .controllerWait:
            return "Controller Wait"
        case .autorunLastSpec:
            return "Autorun Last Spec"
        }
    }
}

enum ControllerConnectionStatus: String, Codable, Sendable {
    case disconnected
    case connecting
    case connected
    case failed

    var displayName: String {
        rawValue.replacingOccurrences(of: "_", with: " ").capitalized
    }
}

enum BenchmarkStreamStatus: String, Codable, Sendable {
    case idle
    case connecting
    case connected
    case reconnecting
    case failed

    var displayName: String {
        rawValue.replacingOccurrences(of: "_", with: " ").capitalized
    }
}

enum BenchmarkRunState: String, Codable, Sendable {
    case idle
    case preparing
    case ready
    case running
    case finishing
    case completed
    case failed

    var displayName: String {
        rawValue.capitalized
    }
}

struct BenchmarkRunSpec: Codable, Sendable, Identifiable {
    let benchmarkSessionID: String
    let runID: String
    let scenarioID: String
    let pipelineID: BenchmarkPipelineID
    let expectedTranscript: String
    let sttSampleRate: Int
    let chunkDurationMilliseconds: Int
    let runDurationMilliseconds: Int
    let saveServerCapture: Bool
    let serverCaptureLabel: String?
    let controllerStartedAt: Date?

    var id: String { runID }

    enum CodingKeys: String, CodingKey {
        case benchmarkSessionID = "benchmark_session_id"
        case runID = "run_id"
        case scenarioID = "scenario_id"
        case pipelineID = "pipeline_id"
        case expectedTranscript = "expected_transcript"
        case sttSampleRate = "stt_sample_rate"
        case chunkDurationMilliseconds = "chunk_duration_ms"
        case runDurationMilliseconds = "run_duration_ms"
        case saveServerCapture = "save_server_capture"
        case serverCaptureLabel = "server_capture_label"
        case controllerStartedAt = "controller_started_at"
    }

    var captureMode: BenchmarkCaptureMode {
        pipelineID.profile.captureMode
    }

    var processingStrategy: BenchmarkAudioProcessingStrategy {
        pipelineID.profile.processingStrategy
    }

    static let sample = BenchmarkRunSpec(
        benchmarkSessionID: "sample-session",
        runID: "sample-run",
        scenarioID: "sample-scenario",
        pipelineID: .appleAECOnly,
        expectedTranscript: "For God so loved the world",
        sttSampleRate: 16_000,
        chunkDurationMilliseconds: 100,
        runDurationMilliseconds: 5_000,
        saveServerCapture: true,
        serverCaptureLabel: "sample-scenario-apple-aec-only",
        controllerStartedAt: nil
    )
}

struct BenchmarkRunResult: Codable, Sendable {
    let runID: String
    let pipelineID: BenchmarkPipelineID
    let status: String
    let firstPartialLatencyMilliseconds: Int?
    let firstFinalLatencyMilliseconds: Int?
    let wordErrorRate: Double?
    let characterErrorRate: Double?
    let finalTranscript: String
    let warnings: [String]
    let errors: [String]

    enum CodingKeys: String, CodingKey {
        case runID = "run_id"
        case pipelineID = "pipeline_id"
        case status
        case firstPartialLatencyMilliseconds = "first_partial_latency_ms"
        case firstFinalLatencyMilliseconds = "first_final_latency_ms"
        case wordErrorRate = "wer"
        case characterErrorRate = "cer"
        case finalTranscript = "final_transcript"
        case warnings
        case errors
    }
}

struct BenchmarkAudioChunkEnvelope: Sendable {
    let base64: String
    let sampleRate: Int
}
