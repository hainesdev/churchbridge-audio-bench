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
    case deepFilterNet3Streaming = "DeepFilterNet3 Streaming"

    var id: String { rawValue }

    static let liveDefault: BenchmarkAudioProcessingStrategy = .robustVoiceFilter

    var targetSampleRate: Int {
        switch self {
        case .appleVoicePassthrough:
            return 48_000
        case .robustVoiceFilter, .persistentConverter, .ephemeralConverter, .deepFilterNet3Streaming:
            return 16_000
        }
    }
}

enum BenchmarkMicProfile: String, CaseIterable, Identifiable, Codable, Sendable {
    case auto = "auto"
    case frontCardioid = "front_cardioid"
    case backCardioid = "back_cardioid"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .auto:
            return "Auto Built-In Mic"
        case .frontCardioid:
            return "Front Cardioid"
        case .backCardioid:
            return "Back Cardioid"
        }
    }
}

enum BenchmarkDFN3TuningProfile: String, CaseIterable, Identifiable, Codable, Sendable {
    case subtle = "subtle"
    case balanced = "balanced"
    case full = "full"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .subtle:
            return "Subtle"
        case .balanced:
            return "Balanced"
        case .full:
            return "Full"
        }
    }
}

struct BenchmarkResolvedDFN3Tuning: Sendable {
    let profile: BenchmarkDFN3TuningProfile
    let wetMix: Float
    let loudnessCompensation: Float
    let maxCompensationGain: Float
    let postGainDB: Float
    let peakLimit: Float
    /// Lower bound on the enhanced magnitude of a bin, as a fraction of that
    /// bin's analysis magnitude, applied before the wet/dry mix. 0 disables it.
    /// Unlike `wetMix`, which floors every bin by the same amount, this only
    /// lifts bins the model tried to gate, so it does not cost suppression in
    /// the bins the model got right. 0 in every profile until it is swept.
    let spectralFloor: Float

    var displayName: String { profile.displayName }

    static let subtle = BenchmarkResolvedDFN3Tuning(
        profile: .subtle,
        wetMix: 0.35,
        loudnessCompensation: 0.85,
        maxCompensationGain: 2.5,
        postGainDB: 0,
        peakLimit: 0.98,
        spectralFloor: 0
    )

    static let balanced = BenchmarkResolvedDFN3Tuning(
        profile: .balanced,
        wetMix: 0.55,
        loudnessCompensation: 0.7,
        maxCompensationGain: 2.25,
        postGainDB: 0.5,
        peakLimit: 0.98,
        spectralFloor: 0
    )

    static let full = BenchmarkResolvedDFN3Tuning(
        profile: .full,
        wetMix: 1.0,
        loudnessCompensation: 0,
        maxCompensationGain: 1.0,
        postGainDB: 0,
        peakLimit: 0.98,
        spectralFloor: 0
    )
}

struct BenchmarkDFN3TuningConfig: Codable, Sendable {
    let profile: BenchmarkDFN3TuningProfile?
    let wetMix: Float?
    let loudnessCompensation: Float?
    let maxCompensationGain: Float?
    let postGainDB: Float?
    let peakLimit: Float?
    let spectralFloor: Float?

    enum CodingKeys: String, CodingKey {
        case profile
        case wetMix = "wet_mix"
        case loudnessCompensation = "loudness_compensation"
        case maxCompensationGain = "max_compensation_gain"
        case postGainDB = "post_gain_db"
        case peakLimit = "peak_limit"
        case spectralFloor = "spectral_floor"
    }

    var resolved: BenchmarkResolvedDFN3Tuning {
        let base: BenchmarkResolvedDFN3Tuning
        switch profile ?? .subtle {
        case .subtle:
            base = .subtle
        case .balanced:
            base = .balanced
        case .full:
            base = .full
        }

        return BenchmarkResolvedDFN3Tuning(
            profile: profile ?? base.profile,
            wetMix: min(max(wetMix ?? base.wetMix, 0), 1),
            loudnessCompensation: min(max(loudnessCompensation ?? base.loudnessCompensation, 0), 1),
            maxCompensationGain: max(maxCompensationGain ?? base.maxCompensationGain, 1),
            postGainDB: postGainDB ?? base.postGainDB,
            peakLimit: min(max(peakLimit ?? base.peakLimit, 0.5), 1),
            spectralFloor: min(max(spectralFloor ?? base.spectralFloor, 0), 1)
        )
    }

    static let subtle = BenchmarkDFN3TuningConfig(
        profile: .subtle,
        wetMix: nil,
        loudnessCompensation: nil,
        maxCompensationGain: nil,
        postGainDB: nil,
        peakLimit: nil,
        spectralFloor: nil
    )
}

struct BenchmarkSTTConfig: Codable, Sendable {
    let model: String
    let language: String
    let languageCodes: [String]
    let location: String
    let recognizer: String
    let interimResults: Bool
    let utteranceEndMilliseconds: Int
    let vadEvents: Bool
    let smartFormat: Bool
    let punctuate: Bool
    let confidenceHoldThreshold: Double
    let lowConfidenceHoldSeconds: Double
    let diarizationEnabled: Bool
    let diarizationMinSpeakers: Int
    let diarizationMaxSpeakers: Int

    enum CodingKeys: String, CodingKey {
        case model
        case language
        case languageCodes
        case location
        case recognizer
        case interimResults
        case utteranceEndMilliseconds = "utteranceEndMs"
        case vadEvents
        case smartFormat
        case punctuate
        case confidenceHoldThreshold
        case lowConfidenceHoldSeconds = "lowConfidenceHoldSecs"
        case diarizationEnabled
        case diarizationMinSpeakers
        case diarizationMaxSpeakers
    }

    static let `default` = BenchmarkSTTConfig(
        model: "chirp_3",
        language: "es-US",
        languageCodes: ["es-US", "en-US"],
        location: "us",
        recognizer: "_",
        interimResults: true,
        utteranceEndMilliseconds: 2_000,
        vadEvents: true,
        smartFormat: true,
        punctuate: true,
        confidenceHoldThreshold: 0.72,
        lowConfidenceHoldSeconds: 2.5,
        diarizationEnabled: false,
        diarizationMinSpeakers: 2,
        diarizationMaxSpeakers: 2
    )
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
    case deepFilterNet3Only = "deepfilternet3_only"
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
        case .deepFilterNet3Only:
            return "DeepFilterNet3 Only"
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
        case .deepFilterNet3Only:
            return BenchmarkPipelineProfile(
                id: self,
                family: .experimental,
                captureMode: .rawDebug,
                processingStrategy: .deepFilterNet3Streaming,
                summary: "Raw benchmark capture plus a streaming DeepFilterNet3 enhancement stage before final STT resampling, with Apple voice processing disabled."
            )
        case .appleAECPlusDeepFilterNet3:
            return BenchmarkPipelineProfile(
                id: self,
                family: .experimental,
                captureMode: .voiceProcessing,
                processingStrategy: .deepFilterNet3Streaming,
                summary: "Apple voice processing plus a streaming DeepFilterNet3 enhancement stage before final STT resampling."
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
    let sttConfig: BenchmarkSTTConfig?
    let micProfile: BenchmarkMicProfile?
    let dfn3Tuning: BenchmarkDFN3TuningConfig?

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
        case sttConfig = "stt_config"
        case micProfile = "mic_profile"
        case dfn3Tuning = "dfn3_tuning"
    }

    var captureMode: BenchmarkCaptureMode {
        pipelineID.profile.captureMode
    }

    var processingStrategy: BenchmarkAudioProcessingStrategy {
        pipelineID.profile.processingStrategy
    }

    var effectiveMicProfile: BenchmarkMicProfile {
        micProfile ?? .auto
    }

    var effectiveSTTConfig: BenchmarkSTTConfig {
        sttConfig ?? .default
    }

    var effectiveDFN3Tuning: BenchmarkResolvedDFN3Tuning {
        dfn3Tuning?.resolved ?? .subtle
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
        controllerStartedAt: nil,
        sttConfig: .default,
        micProfile: nil,
        dfn3Tuning: nil
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
