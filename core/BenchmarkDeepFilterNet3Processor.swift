//
// Implements the DeepFilterNet3 streaming signal chain.
// Derived from DeepFilterNet (https://github.com/Rikorose/DeepFilterNet),
// Copyright (c) 2021 Hendrik Schröter, MIT License.
// See LICENSE-DEEPFILTERNET and THIRD-PARTY-NOTICES.md.
//

import Accelerate
import CoreML
import Foundation

@available(iOS 18.0, *)
final class BenchmarkDeepFilterNet3Processor {
    static let supportedSampleRate = 48_000

    private let config = BenchmarkDeepFilterNet3Config.default

    private var network: BenchmarkDeepFilterNet3Network?
    private var stft: BenchmarkDeepFilterNet3STFTProcessor?
    private var erbFilterbank: [Float] = []
    private var inverseERBFilterbank: [Float] = []
    private var meanNormState: [Float] = []
    private var unitNormState: [Float] = []
    private var initialMeanNormState: [Float] = []
    private var initialUnitNormState: [Float] = []
    private var analysisMemory: [Float] = []
    private var synthesisMemory: [Float] = []
    private var outputBuffer: [Float] = []
    private var tuning: BenchmarkResolvedDFN3Tuning = .subtle

    private(set) var loadWarning: String?

    var isReady: Bool {
        network != nil && stft != nil
    }

    func setTuning(_ tuning: BenchmarkResolvedDFN3Tuning) {
        self.tuning = tuning
    }

    func prepare() async throws {
        guard !isReady else { return }

        let assetsDirectory = try await ensureAssetsDirectory()
        let auxiliaryData = try Self.loadAuxiliaryData(from: assetsDirectory)
        let modelURL = assetsDirectory.appendingPathComponent(Self.modelDirectoryName, isDirectory: true)

        network = try BenchmarkDeepFilterNet3Network(modelURL: modelURL)
        stft = BenchmarkDeepFilterNet3STFTProcessor(
            fftSize: config.fftSize,
            hopSize: config.hopSize,
            window: auxiliaryData.window
        )
        erbFilterbank = auxiliaryData.erbFilterbank
        inverseERBFilterbank = auxiliaryData.inverseERBFilterbank
        initialMeanNormState = auxiliaryData.meanNormState
        initialUnitNormState = auxiliaryData.unitNormState
        reset()
        loadWarning = nil
    }

    func reset() {
        analysisMemory = [Float](repeating: 0, count: config.fftSize - config.hopSize)
        synthesisMemory = [Float](repeating: 0, count: config.fftSize - config.hopSize)
        meanNormState = initialMeanNormState
        unitNormState = initialUnitNormState
        outputBuffer.removeAll(keepingCapacity: true)
    }

    func process(_ samples: [Float], sampleRate: Int) -> [Float] {
        guard !samples.isEmpty else { return [] }
        guard isReady, let network, let stft else { return samples }
        guard sampleRate == Self.supportedSampleRate else {
            loadWarning = "DeepFilterNet3 requires 48 kHz input but received \(sampleRate) Hz; falling back to unenhanced audio for this route."
            return samples
        }

        do {
            let (specReal, specImag) = stft.forward(audio: samples, analysisMem: &analysisMemory)
            let numFrames = specReal.count / config.freqBins
            guard numFrames > 0 else { return [] }

            var erbFeatures = Self.computeERBFeatures(
                real: specReal,
                imag: specImag,
                erbFilterbank: erbFilterbank,
                freqBins: config.freqBins,
                erbBands: config.erbBands,
                numFrames: numFrames
            )
            Self.applyMeanNormalization(
                &erbFeatures,
                state: &meanNormState,
                alpha: config.normAlpha,
                erbBands: config.erbBands,
                numFrames: numFrames
            )

            var specFeatureReal = [Float](repeating: 0, count: numFrames * config.dfBins)
            var specFeatureImag = [Float](repeating: 0, count: numFrames * config.dfBins)
            for frame in 0..<numFrames {
                let specBase = frame * config.freqBins
                let featureBase = frame * config.dfBins
                for bin in 0..<config.dfBins {
                    specFeatureReal[featureBase + bin] = specReal[specBase + bin]
                    specFeatureImag[featureBase + bin] = specImag[specBase + bin]
                }
            }

            Self.applyUnitNormalization(
                real: &specFeatureReal,
                imag: &specFeatureImag,
                state: &unitNormState,
                alpha: config.normAlpha,
                dfBins: config.dfBins,
                numFrames: numFrames
            )

            let erbInput = try Self.makeERBInput(erbFeatures: erbFeatures, numFrames: numFrames, erbBands: config.erbBands)
            let specInput = try Self.makeSpecInput(
                specFeatureReal: specFeatureReal,
                specFeatureImag: specFeatureImag,
                numFrames: numFrames,
                dfBins: config.dfBins
            )
            let prediction = try network.predict(featErb: erbInput, featSpec: specInput)

            let erbMaskCount = numFrames * config.erbBands
            var erbMask = [Float](repeating: 0, count: erbMaskCount)
            Self.extractMultiArray(prediction.erbMask, into: &erbMask, count: erbMaskCount)

            let coefficientCount = config.dfOrder * numFrames * config.dfBins * 2
            var rawCoefficients = [Float](repeating: 0, count: coefficientCount)
            Self.extractMultiArray(prediction.dfCoefs, into: &rawCoefficients, count: coefficientCount)

            var reorderedCoefficients = [Float](repeating: 0, count: coefficientCount)
            for frame in 0..<numFrames {
                for bin in 0..<config.dfBins {
                    for tap in 0..<config.dfOrder {
                        let sourceIndex = ((tap * numFrames + frame) * config.dfBins + bin) * 2
                        let destinationIndex = ((frame * config.dfBins + bin) * config.dfOrder + tap) * 2
                        reorderedCoefficients[destinationIndex] = rawCoefficients[sourceIndex]
                        reorderedCoefficients[destinationIndex + 1] = rawCoefficients[sourceIndex + 1]
                    }
                }
            }

            var enhancedReal = specReal
            var enhancedImag = specImag
            Self.applyERBMask(
                specReal: &enhancedReal,
                specImag: &enhancedImag,
                erbMask: erbMask,
                inverseERBFilterbank: inverseERBFilterbank,
                erbBands: config.erbBands,
                freqBins: config.freqBins,
                numFrames: numFrames
            )

            let deepFiltered = Self.applyDeepFiltering(
                specReal: specReal,
                specImag: specImag,
                coefficients: reorderedCoefficients,
                dfBins: config.dfBins,
                dfOrder: config.dfOrder,
                dfLookahead: config.dfLookahead,
                numFrames: numFrames,
                freqBins: config.freqBins
            )
            for frame in 0..<numFrames {
                let fullBase = frame * config.freqBins
                let filteredBase = frame * config.dfBins
                for bin in 0..<config.dfBins {
                    enhancedReal[fullBase + bin] = deepFiltered.real[filteredBase + bin]
                    enhancedImag[fullBase + bin] = deepFiltered.imag[filteredBase + bin]
                }
            }

            let enhancedSamples = stft.inverse(
                real: enhancedReal,
                imag: enhancedImag,
                synthesisMem: &synthesisMemory
            )
            outputBuffer.append(contentsOf: enhancedSamples)
            let emittedCount = min(outputBuffer.count, samples.count)
            guard emittedCount > 0 else { return [] }

            let emitted = Array(outputBuffer.prefix(emittedCount))
            outputBuffer.removeFirst(emittedCount)
            loadWarning = nil
            return applyTuning(
                drySamples: Array(samples.prefix(emittedCount)),
                enhancedSamples: emitted
            )
        } catch {
            loadWarning = "DeepFilterNet3 inference failed: \(error.localizedDescription)"
            return samples
        }
    }

    private func applyTuning(drySamples: [Float], enhancedSamples: [Float]) -> [Float] {
        let sampleCount = min(drySamples.count, enhancedSamples.count)
        guard sampleCount > 0 else { return [] }

        let dryMix = 1 - tuning.wetMix
        let wetMix = tuning.wetMix
        let postGain = Float(pow(10.0, Double(tuning.postGainDB) / 20.0))

        var output = [Float](repeating: 0, count: sampleCount)
        for index in 0..<sampleCount {
            output[index] = (drySamples[index] * dryMix) + (enhancedSamples[index] * wetMix)
        }

        let dryRMS = Self.rootMeanSquare(drySamples)
        let mixedRMS = Self.rootMeanSquare(output)
        if dryRMS > 0, mixedRMS > 0, tuning.loudnessCompensation > 0 {
            let targetGain = min(max(dryRMS / mixedRMS, 1), tuning.maxCompensationGain)
            let compensatedGain = 1 + ((targetGain - 1) * tuning.loudnessCompensation)
            for index in 0..<sampleCount {
                output[index] *= compensatedGain
            }
        }

        if postGain != 1 {
            for index in 0..<sampleCount {
                output[index] *= postGain
            }
        }

        let peak = Self.peakMagnitude(output)
        if peak > tuning.peakLimit, peak > 0 {
            let limiterGain = tuning.peakLimit / peak
            for index in 0..<sampleCount {
                output[index] *= limiterGain
            }
        }

        return output
    }

    private static func rootMeanSquare(_ samples: [Float]) -> Float {
        guard !samples.isEmpty else { return 0 }
        var sumSquares: Float = 0
        for sample in samples {
            sumSquares += sample * sample
        }
        return sqrt(sumSquares / Float(samples.count))
    }

    private static func peakMagnitude(_ samples: [Float]) -> Float {
        var peak: Float = 0
        for sample in samples {
            peak = max(peak, abs(sample))
        }
        return peak
    }

    private func ensureAssetsDirectory() async throws -> URL {
        if let bundledAssetsDirectory = Self.locateBundledAssetsDirectory() {
            return bundledAssetsDirectory
        }

        let cacheDirectory = try Self.applicationSupportAssetsDirectory()
        try FileManager.default.createDirectory(at: cacheDirectory, withIntermediateDirectories: true, attributes: nil)
        if Self.assetsExist(at: cacheDirectory) {
            return cacheDirectory
        }

        try await Self.downloadAssets(into: cacheDirectory)
        guard Self.assetsExist(at: cacheDirectory) else {
            throw NSError(
                domain: "ChurchBridgeAudioBench.DeepFilterNet3",
                code: 2200,
                userInfo: [NSLocalizedDescriptionKey: "DeepFilterNet3 assets are still incomplete after download."]
            )
        }
        return cacheDirectory
    }

    private static func locateBundledAssetsDirectory() -> URL? {
        let bundles = [Bundle.main, Bundle(for: BenchmarkDeepFilterNet3Processor.self)]
        for bundle in bundles {
            guard
                let modelURL = bundle.url(forResource: "DeepFilterNet3", withExtension: "mlmodelc"),
                let auxiliaryURL = bundle.url(forResource: "auxiliary", withExtension: "npz")
            else {
                continue
            }
            let directory = modelURL.deletingLastPathComponent()
            if assetsExist(at: directory) || FileManager.default.fileExists(atPath: auxiliaryURL.path) {
                return directory
            }
        }
        return nil
    }

    private static func applicationSupportAssetsDirectory() throws -> URL {
        let applicationSupportDirectory = try FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        return applicationSupportDirectory
            .appendingPathComponent("BenchmarkModels", isDirectory: true)
            .appendingPathComponent("DeepFilterNet3-CoreML", isDirectory: true)
    }

    private static func assetsExist(at directory: URL) -> Bool {
        let modelDirectory = directory.appendingPathComponent(modelDirectoryName, isDirectory: true)
        let auxiliaryURL = directory.appendingPathComponent(auxiliaryFileName)
        let fileManager = FileManager.default

        return fileManager.fileExists(atPath: auxiliaryURL.path)
            && requiredModelFiles.allSatisfy { relativePath in
                fileManager.fileExists(atPath: directory.appendingPathComponent(relativePath).path)
            }
            && fileManager.fileExists(atPath: modelDirectory.path)
    }

    private static func downloadAssets(into directory: URL) async throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: nil)

        for relativePath in requiredDownloadFiles {
            let destinationURL = directory.appendingPathComponent(relativePath)
            if FileManager.default.fileExists(atPath: destinationURL.path) {
                continue
            }

            let parentDirectory = destinationURL.deletingLastPathComponent()
            try FileManager.default.createDirectory(at: parentDirectory, withIntermediateDirectories: true, attributes: nil)

            let remoteURL = URL(string: "https://huggingface.co/aufklarer/DeepFilterNet3-CoreML/resolve/main/\(relativePath)?download=true")!
            let (data, response) = try await URLSession.shared.data(from: remoteURL)
            guard
                let httpResponse = response as? HTTPURLResponse,
                (200...299).contains(httpResponse.statusCode)
            else {
                throw NSError(
                    domain: "ChurchBridgeAudioBench.DeepFilterNet3",
                    code: 2201,
                    userInfo: [NSLocalizedDescriptionKey: "DeepFilterNet3 asset download failed for \(relativePath)."]
                )
            }
            try data.write(to: destinationURL, options: .atomic)
        }
    }

    private static func loadAuxiliaryData(from directory: URL) throws -> BenchmarkDeepFilterNet3AuxiliaryData {
        let modelDirectory = directory.appendingPathComponent(modelDirectoryName, isDirectory: true)
        let auxiliaryURL = directory.appendingPathComponent(auxiliaryFileName)

        guard FileManager.default.fileExists(atPath: modelDirectory.path) else {
            throw NSError(
                domain: "ChurchBridgeAudioBench.DeepFilterNet3",
                code: 2202,
                userInfo: [NSLocalizedDescriptionKey: "DeepFilterNet3.mlmodelc was not found in \(directory.path)."]
            )
        }
        guard FileManager.default.fileExists(atPath: auxiliaryURL.path) else {
            throw NSError(
                domain: "ChurchBridgeAudioBench.DeepFilterNet3",
                code: 2203,
                userInfo: [NSLocalizedDescriptionKey: "auxiliary.npz was not found in \(directory.path)."]
            )
        }

        let arrays = try BenchmarkDeepFilterNet3NpzReader.read(url: auxiliaryURL)
        guard
            let erbFilterbank = arrays["erb_fb"],
            let inverseERBFilterbank = arrays["erb_inv_fb"],
            let window = arrays["window"],
            let meanNormState = arrays["mean_norm_state"],
            let unitNormState = arrays["unit_norm_state"]
        else {
            throw NSError(
                domain: "ChurchBridgeAudioBench.DeepFilterNet3",
                code: 2204,
                userInfo: [NSLocalizedDescriptionKey: "auxiliary.npz was missing one or more required arrays."]
            )
        }

        return BenchmarkDeepFilterNet3AuxiliaryData(
            erbFilterbank: erbFilterbank,
            inverseERBFilterbank: inverseERBFilterbank,
            window: window,
            meanNormState: meanNormState,
            unitNormState: unitNormState
        )
    }

    private static func makeERBInput(erbFeatures: [Float], numFrames: Int, erbBands: Int) throws -> MLMultiArray {
        let array = try MLMultiArray(
            shape: [1, 1, NSNumber(value: numFrames), NSNumber(value: erbBands)],
            dataType: .float32
        )
        let pointer = array.dataPointer.assumingMemoryBound(to: Float.self)
        erbFeatures.withUnsafeBufferPointer { source in
            pointer.update(from: source.baseAddress!, count: erbFeatures.count)
        }
        return array
    }

    private static func makeSpecInput(
        specFeatureReal: [Float],
        specFeatureImag: [Float],
        numFrames: Int,
        dfBins: Int
    ) throws -> MLMultiArray {
        let array = try MLMultiArray(
            shape: [1, 2, NSNumber(value: numFrames), NSNumber(value: dfBins)],
            dataType: .float32
        )
        let pointer = array.dataPointer.assumingMemoryBound(to: Float.self)
        let channelStride = numFrames * dfBins
        specFeatureReal.withUnsafeBufferPointer { source in
            pointer.update(from: source.baseAddress!, count: channelStride)
        }
        specFeatureImag.withUnsafeBufferPointer { source in
            (pointer + channelStride).update(from: source.baseAddress!, count: channelStride)
        }
        return array
    }

    private static func extractMultiArray(_ array: MLMultiArray, into output: inout [Float], count: Int) {
        switch array.dataType {
        case .float16:
            let pointer = array.dataPointer.assumingMemoryBound(to: Float16.self)
            for index in 0..<count {
                output[index] = Float(pointer[index])
            }
        default:
            let pointer = array.dataPointer.assumingMemoryBound(to: Float.self)
            output.withUnsafeMutableBufferPointer { destination in
                destination.baseAddress!.update(from: pointer, count: count)
            }
        }
    }

    private static func computeERBFeatures(
        real: [Float],
        imag: [Float],
        erbFilterbank: [Float],
        freqBins: Int,
        erbBands: Int,
        numFrames: Int
    ) -> [Float] {
        var power = [Float](repeating: 0, count: numFrames * freqBins)
        for index in 0..<power.count {
            power[index] = (real[index] * real[index]) + (imag[index] * imag[index])
        }

        var erbFeatures = [Float](repeating: 0, count: numFrames * erbBands)
        vDSP_mmul(
            power,
            1,
            erbFilterbank,
            1,
            &erbFeatures,
            1,
            vDSP_Length(numFrames),
            vDSP_Length(erbBands),
            vDSP_Length(freqBins)
        )

        var epsilon: Float = 1e-10
        vDSP_vsadd(erbFeatures, 1, &epsilon, &erbFeatures, 1, vDSP_Length(erbFeatures.count))
        var count32 = Int32(erbFeatures.count)
        vvlog10f(&erbFeatures, erbFeatures, &count32)
        var decibelScale: Float = 10
        vDSP_vsmul(erbFeatures, 1, &decibelScale, &erbFeatures, 1, vDSP_Length(erbFeatures.count))
        return erbFeatures
    }

    private static func applyMeanNormalization(
        _ erbFeatures: inout [Float],
        state: inout [Float],
        alpha: Float,
        erbBands: Int,
        numFrames: Int
    ) {
        let oneMinusAlpha = 1 - alpha
        for frame in 0..<numFrames {
            let baseIndex = frame * erbBands
            for band in 0..<erbBands {
                let value = erbFeatures[baseIndex + band]
                state[band] = (value * oneMinusAlpha) + (state[band] * alpha)
                erbFeatures[baseIndex + band] = (value - state[band]) / 40
            }
        }
    }

    private static func applyUnitNormalization(
        real: inout [Float],
        imag: inout [Float],
        state: inout [Float],
        alpha: Float,
        dfBins: Int,
        numFrames: Int
    ) {
        let oneMinusAlpha = 1 - alpha
        for frame in 0..<numFrames {
            let baseIndex = frame * dfBins
            for bin in 0..<dfBins {
                let realValue = real[baseIndex + bin]
                let imaginaryValue = imag[baseIndex + bin]
                let magnitude = sqrt((realValue * realValue) + (imaginaryValue * imaginaryValue))
                state[bin] = (magnitude * oneMinusAlpha) + (state[bin] * alpha)
                let normalization = sqrt(max(state[bin], 1e-10))
                real[baseIndex + bin] = realValue / normalization
                imag[baseIndex + bin] = imaginaryValue / normalization
            }
        }
    }

    private static func applyDeepFiltering(
        specReal: [Float],
        specImag: [Float],
        coefficients: [Float],
        dfBins: Int,
        dfOrder: Int,
        dfLookahead: Int,
        numFrames: Int,
        freqBins: Int
    ) -> (real: [Float], imag: [Float]) {
        let padBefore = dfOrder - 1 - dfLookahead
        var outputReal = [Float](repeating: 0, count: numFrames * dfBins)
        var outputImag = [Float](repeating: 0, count: numFrames * dfBins)

        for frame in 0..<numFrames {
            for bin in 0..<dfBins {
                var sumReal: Float = 0
                var sumImaginary: Float = 0
                for tap in 0..<dfOrder {
                    let sourceFrame = max(0, min(numFrames - 1, frame + tap - padBefore))
                    let sourceIndex = (sourceFrame * freqBins) + bin
                    let coefficientIndex = ((frame * dfBins + bin) * dfOrder + tap) * 2

                    let coefficientReal = coefficients[coefficientIndex]
                    let coefficientImaginary = coefficients[coefficientIndex + 1]
                    let inputReal = specReal[sourceIndex]
                    let inputImaginary = specImag[sourceIndex]

                    sumReal += (inputReal * coefficientReal) - (inputImaginary * coefficientImaginary)
                    sumImaginary += (inputImaginary * coefficientReal) + (inputReal * coefficientImaginary)
                }

                let destinationIndex = (frame * dfBins) + bin
                outputReal[destinationIndex] = sumReal
                outputImag[destinationIndex] = sumImaginary
            }
        }

        return (outputReal, outputImag)
    }

    private static func applyERBMask(
        specReal: inout [Float],
        specImag: inout [Float],
        erbMask: [Float],
        inverseERBFilterbank: [Float],
        erbBands: Int,
        freqBins: Int,
        numFrames: Int
    ) {
        var fullMask = [Float](repeating: 0, count: numFrames * freqBins)
        vDSP_mmul(
            erbMask,
            1,
            inverseERBFilterbank,
            1,
            &fullMask,
            1,
            vDSP_Length(numFrames),
            vDSP_Length(freqBins),
            vDSP_Length(erbBands)
        )
        vDSP_vmul(specReal, 1, fullMask, 1, &specReal, 1, vDSP_Length(specReal.count))
        vDSP_vmul(specImag, 1, fullMask, 1, &specImag, 1, vDSP_Length(specImag.count))
    }

    private static let modelDirectoryName = "DeepFilterNet3.mlmodelc"
    private static let auxiliaryFileName = "auxiliary.npz"
    private static let requiredModelFiles = [
        "DeepFilterNet3.mlmodelc/analytics/coremldata.bin",
        "DeepFilterNet3.mlmodelc/coremldata.bin",
        "DeepFilterNet3.mlmodelc/model.mil",
        "DeepFilterNet3.mlmodelc/weights/weight.bin",
    ]
    private static let requiredDownloadFiles = requiredModelFiles + [auxiliaryFileName]
}

@available(iOS 18.0, *)
private struct BenchmarkDeepFilterNet3AuxiliaryData {
    let erbFilterbank: [Float]
    let inverseERBFilterbank: [Float]
    let window: [Float]
    let meanNormState: [Float]
    let unitNormState: [Float]
}

@available(iOS 18.0, *)
private final class BenchmarkDeepFilterNet3STFTProcessor {
    private let fftSize: Int
    private let hopSize: Int
    private let freqBins: Int
    private let window: [Float]
    private let inverseScale: Float
    private let forwardSetup: OpaquePointer
    private let inverseSetup: OpaquePointer

    init(fftSize: Int, hopSize: Int, window: [Float]) {
        self.fftSize = fftSize
        self.hopSize = hopSize
        self.window = window
        freqBins = (fftSize / 2) + 1
        inverseScale = 1 / Float(fftSize)

        guard let forwardSetup = vDSP_DFT_zop_CreateSetup(nil, vDSP_Length(fftSize), .FORWARD) else {
            fatalError("Failed to create DeepFilterNet3 forward DFT setup.")
        }
        guard let inverseSetup = vDSP_DFT_zop_CreateSetup(nil, vDSP_Length(fftSize), .INVERSE) else {
            fatalError("Failed to create DeepFilterNet3 inverse DFT setup.")
        }

        self.forwardSetup = forwardSetup
        self.inverseSetup = inverseSetup
    }

    deinit {
        vDSP_DFT_DestroySetup(forwardSetup)
        vDSP_DFT_DestroySetup(inverseSetup)
    }

    func forward(audio: [Float], analysisMem: inout [Float]) -> (real: [Float], imag: [Float]) {
        let overlapSize = fftSize - hopSize
        let buffer = analysisMem + audio
        let numFrames = max(0, ((buffer.count - fftSize) / hopSize) + 1)
        guard numFrames > 0 else {
            analysisMem = Array(buffer.suffix(overlapSize))
            return ([], [])
        }

        var real = [Float](repeating: 0, count: numFrames * freqBins)
        var imag = [Float](repeating: 0, count: numFrames * freqBins)
        var windowedFrame = [Float](repeating: 0, count: fftSize)
        var zeroImag = [Float](repeating: 0, count: fftSize)
        var outputReal = [Float](repeating: 0, count: fftSize)
        var outputImag = [Float](repeating: 0, count: fftSize)

        for frame in 0..<numFrames {
            let start = frame * hopSize
            buffer.withUnsafeBufferPointer { pointer in
                vDSP_vmul(pointer.baseAddress! + start, 1, window, 1, &windowedFrame, 1, vDSP_Length(fftSize))
            }
            vDSP_vclr(&zeroImag, 1, vDSP_Length(fftSize))
            vDSP_DFT_Execute(forwardSetup, windowedFrame, zeroImag, &outputReal, &outputImag)

            let destinationBase = frame * freqBins
            for bin in 0..<freqBins {
                real[destinationBase + bin] = outputReal[bin]
                imag[destinationBase + bin] = outputImag[bin]
            }
        }

        let consumed = numFrames * hopSize
        analysisMem = Array(buffer.suffix(buffer.count - consumed))
        if analysisMem.count > overlapSize {
            analysisMem = Array(analysisMem.suffix(overlapSize))
        } else if analysisMem.count < overlapSize {
            analysisMem = [Float](repeating: 0, count: overlapSize - analysisMem.count) + analysisMem
        }
        return (real, imag)
    }

    func inverse(real: [Float], imag: [Float], synthesisMem: inout [Float]) -> [Float] {
        let numFrames = real.count / freqBins
        guard numFrames > 0 else { return [] }

        var output = [Float](repeating: 0, count: numFrames * hopSize)
        var fullReal = [Float](repeating: 0, count: fftSize)
        var fullImag = [Float](repeating: 0, count: fftSize)
        var inverseReal = [Float](repeating: 0, count: fftSize)
        var inverseImag = [Float](repeating: 0, count: fftSize)

        for frame in 0..<numFrames {
            let sourceBase = frame * freqBins
            for bin in 0..<freqBins {
                fullReal[bin] = real[sourceBase + bin]
                fullImag[bin] = imag[sourceBase + bin]
            }
            for bin in 1..<(fftSize / 2) {
                fullReal[fftSize - bin] = fullReal[bin]
                fullImag[fftSize - bin] = -fullImag[bin]
            }

            vDSP_DFT_Execute(inverseSetup, fullReal, fullImag, &inverseReal, &inverseImag)
            var scale = inverseScale
            vDSP_vsmul(inverseReal, 1, &scale, &inverseReal, 1, vDSP_Length(fftSize))

            var windowed = [Float](repeating: 0, count: fftSize)
            vDSP_vmul(inverseReal, 1, window, 1, &windowed, 1, vDSP_Length(fftSize))
            for index in 0..<min(windowed.count, synthesisMem.count) {
                windowed[index] += synthesisMem[index]
            }

            let destinationBase = frame * hopSize
            for index in 0..<hopSize {
                let destinationIndex = destinationBase + index
                if destinationIndex < output.count {
                    output[destinationIndex] = windowed[index]
                }
            }

            synthesisMem = Array(windowed[hopSize..<fftSize])
        }

        return output
    }
}

@available(iOS 18.0, *)
private enum BenchmarkDeepFilterNet3NpzReader {
    static func read(url: URL) throws -> [String: [Float]] {
        let data = try Data(contentsOf: url)
        var arrays: [String: [Float]] = [:]
        var offset = 0

        while offset + 30 <= data.count {
            guard
                data[offset] == 0x50,
                data[offset + 1] == 0x4B,
                data[offset + 2] == 0x03,
                data[offset + 3] == 0x04
            else {
                break
            }

            var compressedSize = Int(readUInt32(data, at: offset + 18))
            var uncompressedSize = Int(readUInt32(data, at: offset + 22))
            let nameLength = Int(readUInt16(data, at: offset + 26))
            let extraLength = Int(readUInt16(data, at: offset + 28))
            let nameStart = offset + 30
            guard nameStart + nameLength <= data.count else { break }

            let nameData = data.subdata(in: nameStart..<(nameStart + nameLength))
            var name = String(data: nameData, encoding: .utf8) ?? ""
            if name.hasSuffix(".npy") {
                name = String(name.dropLast(4))
            }

            if compressedSize == 0xFFFFFFFF || uncompressedSize == 0xFFFFFFFF {
                let extraStart = nameStart + nameLength
                if extraLength >= 4, readUInt16(data, at: extraStart) == 0x0001 {
                    var extraOffset = extraStart + 4
                    if uncompressedSize == 0xFFFFFFFF {
                        uncompressedSize = Int(readUInt64(data, at: extraOffset))
                        extraOffset += 8
                    }
                    if compressedSize == 0xFFFFFFFF {
                        compressedSize = Int(readUInt64(data, at: extraOffset))
                    }
                }
            }

            let dataStart = nameStart + nameLength + extraLength
            let dataSize = max(compressedSize, uncompressedSize)
            guard dataStart + dataSize <= data.count else { break }

            if let values = parseNpy(data, npyOffset: dataStart, npySize: uncompressedSize) {
                arrays[name] = values
            }
            offset = dataStart + dataSize
        }

        return arrays
    }

    private static func parseNpy(_ data: Data, npyOffset: Int, npySize: Int) -> [Float]? {
        guard
            npySize >= 10,
            data[npyOffset] == 0x93,
            data[npyOffset + 1] == 0x4E
        else {
            return nil
        }

        let majorVersion = data[npyOffset + 6]
        let headerLength = majorVersion == 1
            ? Int(readUInt16(data, at: npyOffset + 8))
            : Int(readUInt32(data, at: npyOffset + 8))
        let headerSize = majorVersion == 1 ? 10 : 12
        let floatStart = npyOffset + headerSize + headerLength
        let byteCount = npySize - headerSize - headerLength
        guard byteCount > 0 else { return nil }

        let floatCount = byteCount / 4
        var values = [Float](repeating: 0, count: floatCount)
        _ = values.withUnsafeMutableBytes { buffer in
            data.copyBytes(to: buffer, from: floatStart..<(floatStart + (floatCount * 4)))
        }
        return values
    }

    private static func readUInt16(_ data: Data, at offset: Int) -> UInt16 {
        data.withUnsafeBytes { bytes in
            bytes.loadUnaligned(fromByteOffset: offset - data.startIndex, as: UInt16.self)
        }
    }

    private static func readUInt32(_ data: Data, at offset: Int) -> UInt32 {
        data.withUnsafeBytes { bytes in
            bytes.loadUnaligned(fromByteOffset: offset - data.startIndex, as: UInt32.self)
        }
    }

    private static func readUInt64(_ data: Data, at offset: Int) -> UInt64 {
        data.withUnsafeBytes { bytes in
            bytes.loadUnaligned(fromByteOffset: offset - data.startIndex, as: UInt64.self)
        }
    }
}
