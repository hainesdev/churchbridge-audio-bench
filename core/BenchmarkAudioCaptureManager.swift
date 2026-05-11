@preconcurrency import AVFAudio
import Foundation

final class BenchmarkAudioCaptureManager: NSObject {
    var telemetryDidChange: ((BenchmarkTelemetrySnapshot) -> Void)?
    var errorHandler: ((String) -> Void)?
    var audioChunkHandler: ((BenchmarkAudioChunkEnvelope) -> Void)?

    private let session = AVAudioSession.sharedInstance()
    private let engine = AVAudioEngine()
    private let processingQueue = DispatchQueue(label: "ChurchBridgeAudioBench.audio-processing", qos: .userInitiated)
    private let processingQueueKey = DispatchSpecificKey<UInt8>()
    private var hardwareInputFormat: AVAudioFormat?
    private var targetFormat: AVAudioFormat?
    private var converter: AVAudioConverter?
    private var telemetry = BenchmarkTelemetrySnapshot.placeholder
    private var pendingSamples: [Float] = []
    private var isRunning = false
    private var currentMode: BenchmarkCaptureMode?
    private var currentRequestedMode: BenchmarkCaptureMode?
    private var currentStrategy: BenchmarkAudioProcessingStrategy = .liveDefault
    private var isReconfiguring = false
    private var lastReconfigureAt: Date?
    private var dcRejectPreviousInput: Float = 0
    private var dcRejectPreviousOutput: Float = 0
    private var lowFrequencyEstimate: Float = 0
    private var smoothedGain: Float = 1
    private var gateHoldFramesRemaining = 0

    override init() {
        super.init()
        processingQueue.setSpecific(key: processingQueueKey, value: 1)
        NotificationCenter.default.addObserver(self, selector: #selector(handleRouteChange), name: AVAudioSession.routeChangeNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(handleInterruption), name: AVAudioSession.interruptionNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(handleEngineConfigurationChange), name: .AVAudioEngineConfigurationChange, object: engine)
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
    }

    func start(runSpec: BenchmarkRunSpec) async throws {
        telemetry.activePipelineID = runSpec.pipelineID.rawValue
        telemetry.pipelineFamily = runSpec.pipelineID.profile.family.rawValue
        try await start(mode: runSpec.captureMode, strategy: runSpec.processingStrategy)
    }

    func start(mode: BenchmarkCaptureMode, strategy: BenchmarkAudioProcessingStrategy) async throws {
        guard !isRunning else { return }

        let granted = await AVAudioApplication.requestRecordPermission()
        telemetry.microphonePermissionGranted = granted
        publishTelemetry()
        guard granted else {
            throw NSError(domain: "ChurchBridgeAudioBench", code: 1, userInfo: [NSLocalizedDescriptionKey: "Microphone permission was denied."])
        }

        let effectiveMode = resolveEffectiveMode(requestedMode: mode)
        currentMode = effectiveMode
        currentRequestedMode = mode
        currentStrategy = strategy
        resetStageTelemetry()
        telemetry.captureStrategy = strategy.rawValue
        try configureSession(for: effectiveMode)
        try configureEngine(for: effectiveMode, requestedMode: mode)
        engine.prepare()
        try engine.start()
        telemetry.engineRunning = engine.isRunning
        isRunning = true
        refreshRouteTelemetry()
        publishTelemetry()
    }

    func stop() {
        guard isRunning else { return }

        engine.stop()
        teardownCaptureGraph()
        processingQueue.sync {
            pendingSamples.removeAll(keepingCapacity: false)
        }

        isRunning = false
        currentMode = nil
        currentRequestedMode = nil
        currentStrategy = .liveDefault
        lastReconfigureAt = nil
        telemetry.engineRunning = false
        telemetry.speechDetected = false
        telemetry.rmsLevel = 0
        telemetry.clipping = false
        telemetry.pendingSampleCount = 0
        resetSignalConditioningState()
        publishTelemetry()

        do {
            try session.setActive(false, options: .notifyOthersOnDeactivation)
        } catch {
            emitError("Audio session deactivate failed: \(error.localizedDescription)")
        }
    }

    private func resolveEffectiveMode(requestedMode: BenchmarkCaptureMode) -> BenchmarkCaptureMode {
        #if targetEnvironment(simulator)
        telemetry.capturePath = "Simulator fallback"
        telemetry.fallbackReason = "Simulator uses raw capture because Apple voice-processing behavior is device-only."
        return .rawDebug
        #else
        telemetry.fallbackReason = ""
        switch requestedMode {
        case .voiceProcessing:
            telemetry.capturePath = "Voice Processing"
        case .echoCancelled:
            telemetry.capturePath = "Echo-Cancelled Input"
        case .rawDebug:
            telemetry.capturePath = "Raw Debug"
        }
        return requestedMode
        #endif
    }

    private func configureSession(for mode: BenchmarkCaptureMode) throws {
        let sessionMode: AVAudioSession.Mode
        switch mode {
        case .voiceProcessing:
            sessionMode = .voiceChat
        case .echoCancelled:
            sessionMode = .default
        case .rawDebug:
            sessionMode = .measurement
        }

        try session.setCategory(.playAndRecord, mode: sessionMode, options: [.defaultToSpeaker, .allowBluetoothHFP])
        try session.setPreferredSampleRate(48_000)
        try session.setPreferredIOBufferDuration(0.02)
        try? session.setPreferredInputNumberOfChannels(1)

        if #available(iOS 18.2, *) {
            telemetry.echoCancelledInputAvailable = session.isEchoCancelledInputAvailable
            if mode == .echoCancelled, session.isEchoCancelledInputAvailable {
                try? session.setPrefersEchoCancelledInput(true)
            } else {
                try? session.setPrefersEchoCancelledInput(false)
            }
            telemetry.echoCancelledInputEnabled = session.isEchoCancelledInputEnabled
        } else {
            telemetry.echoCancelledInputAvailable = false
            telemetry.echoCancelledInputEnabled = false
        }

        try session.setActive(true, options: [])
    }

    private func configureEngine(for mode: BenchmarkCaptureMode, requestedMode: BenchmarkCaptureMode) throws {
        engine.stop()
        engine.reset()
        teardownCaptureGraph()

        let inputNode = engine.inputNode
        _ = engine.outputNode
        _ = engine.mainMixerNode

        telemetry.voiceProcessingRequested = requestedMode == .voiceProcessing

        if mode == .voiceProcessing {
            do {
                try inputNode.setVoiceProcessingEnabled(true)
                telemetry.voiceProcessingEnabled = inputNode.isVoiceProcessingEnabled
            } catch {
                telemetry.voiceProcessingEnabled = false
                telemetry.capturePath = "Raw fallback"
                telemetry.fallbackReason = "Voice processing could not be enabled on this route."
                emitError("Voice processing could not be enabled: \(error.localizedDescription)")
            }
        } else {
            if inputNode.isVoiceProcessingEnabled {
                try? inputNode.setVoiceProcessingEnabled(false)
            }
            telemetry.voiceProcessingEnabled = false
            if requestedMode == .echoCancelled && !telemetry.echoCancelledInputEnabled && telemetry.fallbackReason.isEmpty {
                telemetry.capturePath = "Raw fallback"
                telemetry.fallbackReason = "Echo-cancelled input is unavailable on this route."
            }
        }

        let hardwareFormat = inputNode.outputFormat(forBus: 0)
        let outputSampleRate = currentStrategy == .appleVoicePassthrough ? hardwareFormat.sampleRate : 16_000
        guard let targetFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: outputSampleRate, channels: 1, interleaved: false) else {
            throw NSError(domain: "ChurchBridgeAudioBench", code: 2, userInfo: [NSLocalizedDescriptionKey: "Unable to create output processing format."])
        }
        guard let monoFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: hardwareFormat.sampleRate, channels: 1, interleaved: false) else {
            throw NSError(domain: "ChurchBridgeAudioBench", code: 3, userInfo: [NSLocalizedDescriptionKey: "Unable to create mono processing format."])
        }

        hardwareInputFormat = hardwareFormat
        self.targetFormat = targetFormat
        switch currentStrategy {
        case .persistentConverter, .robustVoiceFilter:
            converter = makeConverter(from: monoFormat, to: targetFormat)
        case .appleVoicePassthrough, .ephemeralConverter:
            converter = nil
        }
        resetPendingSamples()
        resetSignalConditioningState()

        telemetry.inputSampleRate = hardwareFormat.sampleRate
        telemetry.inputChannels = Int(hardwareFormat.channelCount)
        telemetry.inputFormatDescription = describe(format: hardwareFormat)
        telemetry.targetSampleRate = targetFormat.sampleRate
        telemetry.outputSampleRate = outputSampleRate
        telemetry.chunkSampleCount = chunkSampleCount(for: outputSampleRate)
        telemetry.voiceProcessingAGCEnabled = inputNode.isVoiceProcessingAGCEnabled
        telemetry.batchesSent = 0
        telemetry.lastBatchAt = nil
        telemetry.lastSpeechAt = nil
        telemetry.rmsLevel = 0
        telemetry.noiseFloor = 0
        telemetry.clipping = false
        telemetry.speechDetected = false
        telemetry.pendingSampleCount = 0

        let tapBufferSize = max(AVAudioFrameCount((hardwareFormat.sampleRate * 0.02).rounded()), 256)
        inputNode.installTap(onBus: 0, bufferSize: tapBufferSize, format: hardwareFormat) { [weak self] buffer, _ in
            self?.captureBuffer(buffer)
        }
    }

    private func captureBuffer(_ buffer: AVAudioPCMBuffer) {
        telemetry.tapCallbackCount += 1
        telemetry.tapFrameCount += Int(buffer.frameLength)
        telemetry.lastTapAt = Date()
        guard let copiedSamples = copyMonoSamples(from: buffer) else {
            telemetry.copyMonoFailureCount += 1
            publishTelemetry()
            return
        }
        telemetry.copyMonoSuccessCount += 1
        processingQueue.async { [weak self] in
            self?.process(samples: copiedSamples)
        }
    }

    private func process(samples: [Float]) {
        telemetry.processingInvocationCount += 1

        let outputSamples: [Float]
        if currentStrategy == .appleVoicePassthrough {
            outputSamples = samples
        } else {
            guard
                let hardwareInputFormat,
                let sourceFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: hardwareInputFormat.sampleRate, channels: 1, interleaved: false),
                let sourceBuffer = AVAudioPCMBuffer(pcmFormat: sourceFormat, frameCapacity: AVAudioFrameCount(samples.count))
            else {
                return
            }

            sourceBuffer.frameLength = AVAudioFrameCount(samples.count)
            guard let destination = sourceBuffer.floatChannelData?[0] else { return }
            destination.update(from: samples, count: samples.count)

            guard let converted = convertBuffer(sourceBuffer, sourceFormat: sourceFormat) else { return }
            outputSamples = currentStrategy == .robustVoiceFilter ? conditionSpeechForStreaming(converted) : converted
        }

        guard !outputSamples.isEmpty else { return }
        updateLevels(from: outputSamples)
        pendingSamples.append(contentsOf: outputSamples)
        telemetry.pendingSampleCount = pendingSamples.count
        telemetry.pendingSampleHighWaterMark = max(telemetry.pendingSampleHighWaterMark, pendingSamples.count)

        let chunkSamples = chunkSampleCount(for: telemetry.outputSampleRate)
        while pendingSamples.count >= chunkSamples {
            let chunk = Array(pendingSamples.prefix(chunkSamples))
            pendingSamples.removeFirst(chunkSamples)
            telemetry.pendingSampleCount = pendingSamples.count
            let base64 = chunk.withUnsafeBufferPointer { pointer in
                Data(buffer: pointer).base64EncodedString()
            }
            telemetry.batchesSent += 1
            telemetry.lastBatchAt = Date()
            telemetry.totalEmittedSamples += chunk.count
            telemetry.lastChunkSampleRate = Int(telemetry.outputSampleRate.rounded())
            telemetry.lastChunkEncodedBytes = base64.utf8.count
            publishTelemetry()
            emitChunk(base64, sampleRate: Int(telemetry.outputSampleRate.rounded()))
        }
    }

    private func copyMonoSamples(from buffer: AVAudioPCMBuffer) -> [Float]? {
        let frameCount = Int(buffer.frameLength)
        guard frameCount > 0 else { return nil }

        let channelCount = Int(buffer.format.channelCount)
        var monoSamples = Array(repeating: Float.zero, count: frameCount)

        switch buffer.format.commonFormat {
        case .pcmFormatFloat32:
            guard let channelData = buffer.floatChannelData else { return nil }
            for channel in 0..<max(channelCount, 1) {
                let source = channelData[min(channel, channelCount - 1)]
                for frame in 0..<frameCount {
                    monoSamples[frame] += source[frame]
                }
            }
        case .pcmFormatInt16:
            guard let channelData = buffer.int16ChannelData else { return nil }
            for channel in 0..<max(channelCount, 1) {
                let source = channelData[min(channel, channelCount - 1)]
                for frame in 0..<frameCount {
                    monoSamples[frame] += Float(source[frame]) / Float(Int16.max)
                }
            }
        case .pcmFormatInt32:
            guard let channelData = buffer.int32ChannelData else { return nil }
            for channel in 0..<max(channelCount, 1) {
                let source = channelData[min(channel, channelCount - 1)]
                for frame in 0..<frameCount {
                    monoSamples[frame] += Float(source[frame]) / Float(Int32.max)
                }
            }
        case .pcmFormatFloat64:
            guard let audioBuffer = buffer.audioBufferList.pointee.mBuffers.mData else { return nil }
            let source = audioBuffer.assumingMemoryBound(to: Double.self)
            for frame in 0..<frameCount {
                monoSamples[frame] = Float(source[frame])
            }
        default:
            return nil
        }

        let divisor = Float(max(channelCount, 1))
        if divisor > 1 {
            for frame in 0..<frameCount {
                monoSamples[frame] /= divisor
            }
        }
        return monoSamples
    }

    private func convertBuffer(_ sourceBuffer: AVAudioPCMBuffer, sourceFormat: AVAudioFormat) -> [Float]? {
        guard let targetFormat else { return nil }
        let outputCapacity = AVAudioFrameCount((Double(sourceBuffer.frameLength) * targetFormat.sampleRate / sourceFormat.sampleRate).rounded(.up)) + 64
        guard outputCapacity > 0 else { return nil }

        let activeConverter: AVAudioConverter?
        switch currentStrategy {
        case .persistentConverter:
            activeConverter = converter
        case .ephemeralConverter:
            activeConverter = makeConverter(from: sourceFormat, to: targetFormat)
        case .robustVoiceFilter:
            activeConverter = converter
        case .appleVoicePassthrough:
            activeConverter = nil
        }

        guard let activeConverter else { return nil }
        var outputSamples: [Float] = []
        var conversionError: NSError?
        var inputProvided = false

        while true {
            guard let outputBuffer = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: outputCapacity) else { return nil }
            let status = activeConverter.convert(to: outputBuffer, error: &conversionError) { _, outStatus in
                if inputProvided {
                    outStatus.pointee = self.currentStrategy == .persistentConverter ? .noDataNow : .endOfStream
                    return nil
                }
                inputProvided = true
                outStatus.pointee = .haveData
                return sourceBuffer
            }

            if let conversionError {
                telemetry.conversionFailureCount += 1
                emitError("Audio convert failed: \(conversionError.localizedDescription)")
                publishTelemetry()
                return nil
            }

            let frameCount = Int(outputBuffer.frameLength)
            if frameCount > 0, let channelData = outputBuffer.floatChannelData?[0] {
                telemetry.conversionSuccessCount += 1
                telemetry.convertedFrameCount += frameCount
                telemetry.lastConvertedAt = Date()
                outputSamples.append(contentsOf: UnsafeBufferPointer(start: channelData, count: frameCount))
            }

            switch status {
            case .haveData:
                if frameCount == 0 {
                    telemetry.zeroFrameConversionCount += 1
                    publishTelemetry()
                    return outputSamples.isEmpty ? nil : outputSamples
                }
                continue
            case .inputRanDry, .endOfStream:
                if frameCount == 0 {
                    telemetry.zeroFrameConversionCount += 1
                }
                publishTelemetry()
                return outputSamples.isEmpty ? nil : outputSamples
            case .error:
                telemetry.conversionFailureCount += 1
                publishTelemetry()
                return nil
            @unknown default:
                publishTelemetry()
                return outputSamples.isEmpty ? nil : outputSamples
            }
        }
    }

    private func makeConverter(from sourceFormat: AVAudioFormat, to targetFormat: AVAudioFormat) -> AVAudioConverter? {
        guard let converter = AVAudioConverter(from: sourceFormat, to: targetFormat) else {
            return nil
        }
        converter.primeMethod = .none
        converter.downmix = false
        converter.sampleRateConverterQuality = AVAudioQuality.high.rawValue
        return converter
    }

    private func teardownCaptureGraph() {
        let inputNode = engine.inputNode
        inputNode.removeTap(onBus: 0)
        converter = nil
        hardwareInputFormat = nil
        targetFormat = nil
        resetSignalConditioningState()
    }

    private func conditionSpeechForStreaming(_ samples: [Float]) -> [Float] {
        guard !samples.isEmpty else { return samples }

        var filtered = Array(repeating: Float.zero, count: samples.count)
        var sumSquares: Float = 0
        var peak: Float = 0

        for index in samples.indices {
            let input = samples[index]

            // Remove DC drift and sub-speech rumble before we estimate speech energy.
            let dcRejected = input - dcRejectPreviousInput + 0.995 * dcRejectPreviousOutput
            dcRejectPreviousInput = input
            dcRejectPreviousOutput = dcRejected

            lowFrequencyEstimate += 0.015 * (dcRejected - lowFrequencyEstimate)
            let highPassed = dcRejected - lowFrequencyEstimate
            filtered[index] = highPassed

            let magnitude = abs(highPassed)
            sumSquares += highPassed * highPassed
            peak = max(peak, magnitude)
        }

        let frameCount = Float(filtered.count)
        let rms = sqrt(sumSquares / max(frameCount, 1))
        let speechOpenThreshold = max(telemetry.noiseFloor * 2.2, 0.008)
        let speechCloseThreshold = max(telemetry.noiseFloor * 1.4, 0.004)
        let likelySpeech = rms >= speechOpenThreshold || peak >= max(telemetry.noiseFloor * 4.0, 0.045)

        if likelySpeech {
            gateHoldFramesRemaining = max(gateHoldFramesRemaining, filtered.count / 2)
        } else {
            gateHoldFramesRemaining = max(gateHoldFramesRemaining - filtered.count, 0)
        }

        let gateIsOpen = likelySpeech || gateHoldFramesRemaining > 0 || rms >= speechCloseThreshold
        let desiredGain: Float
        if gateIsOpen {
            desiredGain = min(max(0.09 / max(rms, 0.015), 1.0), 3.0)
        } else {
            desiredGain = 0.55
        }
        smoothedGain = smoothedGain * 0.82 + desiredGain * 0.18
        let attenuation: Float = gateIsOpen ? 1.0 : 0.22
        let tanhNormalizer = tanh(Float(1.25))

        for index in filtered.indices {
            let amplified = filtered[index] * smoothedGain * attenuation
            filtered[index] = tanh(amplified * 1.25) / tanhNormalizer
        }

        return filtered
    }

    private func updateLevels(from samples: [Float]) {
        guard !samples.isEmpty else { return }

        let peak = samples.reduce(Float.zero) { max($0, abs($1)) }
        let rms = sqrt(samples.reduce(Float.zero) { $0 + ($1 * $1) } / Float(samples.count))
        let nextNoiseFloor: Float
        if telemetry.noiseFloor == 0 {
            nextNoiseFloor = rms
        } else if rms < telemetry.noiseFloor {
            nextNoiseFloor = telemetry.noiseFloor * 0.8 + rms * 0.2
        } else {
            nextNoiseFloor = telemetry.noiseFloor * 0.98 + rms * 0.02
        }

        let threshold = max(nextNoiseFloor * 2.5, 0.015)
        let speechDetected = rms >= threshold
        telemetry.rmsLevel = rms
        telemetry.noiseFloor = nextNoiseFloor
        telemetry.clipping = peak >= 0.98
        telemetry.speechDetected = speechDetected
        if speechDetected {
            telemetry.lastSpeechAt = Date()
        }
        publishTelemetry()
    }

    private func describe(format: AVAudioFormat) -> String {
        let commonFormat: String
        switch format.commonFormat {
        case .pcmFormatFloat32:
            commonFormat = "Float32"
        case .pcmFormatFloat64:
            commonFormat = "Float64"
        case .pcmFormatInt16:
            commonFormat = "Int16"
        case .pcmFormatInt32:
            commonFormat = "Int32"
        case .otherFormat:
            commonFormat = "Other"
        @unknown default:
            commonFormat = "Unknown"
        }
        let layout = format.isInterleaved ? "interleaved" : "non-interleaved"
        return "\(commonFormat) | \(Int(format.channelCount)) ch | \(layout)"
    }

    private func refreshRouteTelemetry() {
        let route = session.currentRoute
        telemetry.routeInputs = route.inputs.map { "\($0.portType.rawValue) | \($0.portName)" }
        telemetry.routeOutputs = route.outputs.map { "\($0.portType.rawValue) | \($0.portName)" }
        telemetry.routeName = telemetry.routeInputs.joined(separator: ", ")
        if telemetry.routeName.isEmpty {
            telemetry.routeName = "No active input route"
        }
        telemetry.inputSampleRate = session.sampleRate
    }

    private func publishTelemetry() {
        let snapshot = telemetry
        DispatchQueue.main.async { [weak self] in
            self?.telemetryDidChange?(snapshot)
        }
    }

    private func emitError(_ message: String) {
        DispatchQueue.main.async { [weak self] in
            self?.errorHandler?(message)
        }
    }

    private func emitChunk(_ base64: String, sampleRate: Int) {
        DispatchQueue.main.async { [weak self] in
            self?.audioChunkHandler?(BenchmarkAudioChunkEnvelope(base64: base64, sampleRate: sampleRate))
        }
    }

    @objc
    private func handleRouteChange(_ notification: Notification) {
        refreshRouteTelemetry()
        publishTelemetry()
        if isRunning {
            reconfigureCaptureGraph(reason: "audio route change")
        }
    }

    @objc
    private func handleInterruption(_ notification: Notification) {
        guard
            let info = notification.userInfo,
            let typeValue = info[AVAudioSessionInterruptionTypeKey] as? UInt,
            let type = AVAudioSession.InterruptionType(rawValue: typeValue)
        else {
            return
        }

        if type == .ended, isRunning {
            reconfigureCaptureGraph(reason: "audio interruption ended")
        }
    }

    @objc
    private func handleEngineConfigurationChange(_ notification: Notification) {
        if isRunning {
            refreshRouteTelemetry()
            publishTelemetry()
            reconfigureCaptureGraph(reason: "engine configuration change")
        }
    }

    private func reconfigureCaptureGraph(reason: String, completion: ((Bool) -> Void)? = nil) {
        guard isRunning, !isReconfiguring else { return }
        guard let mode = currentMode, let requestedMode = currentRequestedMode else { return }
        if let lastReconfigureAt, Date().timeIntervalSince(lastReconfigureAt) < 0.75 {
            completion?(false)
            return
        }

        telemetry.captureRestartCount += 1
        telemetry.lastRestartAt = Date()
        telemetry.lastRestartReason = reason
        lastReconfigureAt = telemetry.lastRestartAt
        isReconfiguring = true
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            defer { self.isReconfiguring = false }

            do {
                try self.configureSession(for: mode)
                try self.configureEngine(for: mode, requestedMode: requestedMode)
                self.engine.prepare()
                try self.engine.start()
                self.telemetry.engineRunning = self.engine.isRunning
                self.refreshRouteTelemetry()
                self.publishTelemetry()
                completion?(true)
            } catch {
                self.emitError("Audio capture reconfiguration failed after \(reason): \(error.localizedDescription)")
                completion?(false)
            }
        }
    }

    func forceDiagnosticRebuild(reason: String = "diagnostics probe") async -> Bool {
        guard isRunning, !isReconfiguring, currentMode != nil, currentRequestedMode != nil else { return false }
        return await withCheckedContinuation { continuation in
            reconfigureCaptureGraph(reason: reason) { success in
                continuation.resume(returning: success)
            }
        }
    }

    private func resetPendingSamples() {
        let clear = { self.pendingSamples.removeAll(keepingCapacity: false) }
        if DispatchQueue.getSpecific(key: processingQueueKey) != nil {
            clear()
        } else {
            processingQueue.sync(execute: clear)
        }
        telemetry.pendingSampleCount = 0
    }

    private func resetSignalConditioningState() {
        let clear = {
            self.dcRejectPreviousInput = 0
            self.dcRejectPreviousOutput = 0
            self.lowFrequencyEstimate = 0
            self.smoothedGain = 1
            self.gateHoldFramesRemaining = 0
        }
        if DispatchQueue.getSpecific(key: processingQueueKey) != nil {
            clear()
        } else {
            processingQueue.sync(execute: clear)
        }
    }

    private func resetStageTelemetry() {
        telemetry.tapCallbackCount = 0
        telemetry.tapFrameCount = 0
        telemetry.lastTapAt = nil
        telemetry.copyMonoSuccessCount = 0
        telemetry.copyMonoFailureCount = 0
        telemetry.processingInvocationCount = 0
        telemetry.conversionSuccessCount = 0
        telemetry.conversionFailureCount = 0
        telemetry.zeroFrameConversionCount = 0
        telemetry.convertedFrameCount = 0
        telemetry.lastConvertedAt = nil
        telemetry.pendingSampleCount = 0
        telemetry.pendingSampleHighWaterMark = 0
        telemetry.captureRestartCount = 0
        telemetry.lastRestartAt = nil
        telemetry.lastRestartReason = ""
        telemetry.captureStrategy = currentStrategy.rawValue
        telemetry.outputSampleRate = Double(currentStrategy.targetSampleRate)
        telemetry.chunkSampleCount = chunkSampleCount(for: telemetry.outputSampleRate)
        telemetry.totalEmittedSamples = 0
        telemetry.lastChunkSampleRate = Int(telemetry.outputSampleRate.rounded())
        telemetry.lastChunkEncodedBytes = 0
    }

    private func chunkSampleCount(for sampleRate: Double) -> Int {
        max(Int((sampleRate * 0.1).rounded()), 256)
    }
}
