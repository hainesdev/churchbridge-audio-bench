import Foundation

struct BenchmarkTelemetrySnapshot: Codable, Sendable {
    var activePipelineID: String
    var pipelineFamily: String
    var routeName: String
    var routeInputs: [String]
    var routeOutputs: [String]
    var capturePath: String
    var fallbackReason: String
    var inputSampleRate: Double
    var targetSampleRate: Double
    var inputChannels: Int
    var inputFormatDescription: String
    var outputSampleRate: Double
    var speechDetected: Bool
    var rmsLevel: Float
    var noiseFloor: Float
    var clipping: Bool
    var lastSpeechAt: Date?
    var batchesSent: Int
    var lastBatchAt: Date?
    var voiceProcessingRequested: Bool
    var voiceProcessingEnabled: Bool
    var voiceProcessingAGCEnabled: Bool
    var echoCancelledInputAvailable: Bool
    var echoCancelledInputEnabled: Bool
    var microphonePermissionGranted: Bool
    var engineRunning: Bool
    var tapCallbackCount: Int
    var tapFrameCount: Int
    var lastTapAt: Date?
    var copyMonoSuccessCount: Int
    var copyMonoFailureCount: Int
    var processingInvocationCount: Int
    var conversionSuccessCount: Int
    var conversionFailureCount: Int
    var zeroFrameConversionCount: Int
    var convertedFrameCount: Int
    var lastConvertedAt: Date?
    var pendingSampleCount: Int
    var pendingSampleHighWaterMark: Int
    var captureRestartCount: Int
    var lastRestartAt: Date?
    var lastRestartReason: String
    var captureStrategy: String
    var chunkSampleCount: Int
    var totalEmittedSamples: Int
    var lastChunkSampleRate: Int
    var lastChunkEncodedBytes: Int
    var warnings: [String]

    var inputSampleRateText: String {
        sampleRateText(inputSampleRate)
    }

    var outputSampleRateText: String {
        sampleRateText(outputSampleRate)
    }

    var emittedAudioSecondsText: String {
        guard outputSampleRate > 0, totalEmittedSamples > 0 else { return "0.00 s" }
        let seconds = Double(totalEmittedSamples) / outputSampleRate
        return String(format: "%.2f s", seconds)
    }

    static let placeholder = BenchmarkTelemetrySnapshot(
        activePipelineID: BenchmarkPipelineID.appleAECOnly.rawValue,
        pipelineFamily: BenchmarkPipelineFamily.baseline.rawValue,
        routeName: "Not configured",
        routeInputs: [],
        routeOutputs: [],
        capturePath: "Not configured",
        fallbackReason: "",
        inputSampleRate: 0,
        targetSampleRate: 16_000,
        inputChannels: 0,
        inputFormatDescription: "",
        outputSampleRate: 16_000,
        speechDetected: false,
        rmsLevel: 0,
        noiseFloor: 0,
        clipping: false,
        lastSpeechAt: nil,
        batchesSent: 0,
        lastBatchAt: nil,
        voiceProcessingRequested: false,
        voiceProcessingEnabled: false,
        voiceProcessingAGCEnabled: false,
        echoCancelledInputAvailable: false,
        echoCancelledInputEnabled: false,
        microphonePermissionGranted: false,
        engineRunning: false,
        tapCallbackCount: 0,
        tapFrameCount: 0,
        lastTapAt: nil,
        copyMonoSuccessCount: 0,
        copyMonoFailureCount: 0,
        processingInvocationCount: 0,
        conversionSuccessCount: 0,
        conversionFailureCount: 0,
        zeroFrameConversionCount: 0,
        convertedFrameCount: 0,
        lastConvertedAt: nil,
        pendingSampleCount: 0,
        pendingSampleHighWaterMark: 0,
        captureRestartCount: 0,
        lastRestartAt: nil,
        lastRestartReason: "",
        captureStrategy: BenchmarkAudioProcessingStrategy.liveDefault.rawValue,
        chunkSampleCount: 1_600,
        totalEmittedSamples: 0,
        lastChunkSampleRate: 16_000,
        lastChunkEncodedBytes: 0,
        warnings: []
    )

    private func sampleRateText(_ value: Double) -> String {
        guard value > 0 else { return "Unknown" }
        return "\(Int(value.rounded())) Hz"
    }
}
