import CoreML
import Foundation

@available(iOS 18.0, *)
struct BenchmarkDeepFilterNet3Config {
    let fftSize: Int
    let hopSize: Int
    let erbBands: Int
    let dfBins: Int
    let dfOrder: Int
    let dfLookahead: Int
    let sampleRate: Int
    let normTau: Float

    var freqBins: Int { (fftSize / 2) + 1 }

    var normAlpha: Float {
        exp(-Float(hopSize) / Float(sampleRate) / normTau)
    }

    static let `default` = BenchmarkDeepFilterNet3Config(
        fftSize: 960,
        hopSize: 480,
        erbBands: 32,
        dfBins: 96,
        dfOrder: 5,
        dfLookahead: 2,
        sampleRate: 48_000,
        normTau: 1.0
    )
}

@available(iOS 18.0, *)
final class BenchmarkDeepFilterNet3Network {
    private let model: MLModel

    init(modelURL: URL, computeUnits: MLComputeUnits = .all) throws {
        let configuration = MLModelConfiguration()
        configuration.computeUnits = computeUnits
        model = try MLModel(contentsOf: modelURL, configuration: configuration)
    }

    func predict(featErb: MLMultiArray, featSpec: MLMultiArray) throws -> (erbMask: MLMultiArray, dfCoefs: MLMultiArray) {
        let input = try MLDictionaryFeatureProvider(
            dictionary: [
                "feat_erb": MLFeatureValue(multiArray: featErb),
                "feat_spec": MLFeatureValue(multiArray: featSpec),
            ]
        )

        let output = try model.prediction(from: input)
        guard
            let erbMask = output.featureValue(for: "erb_mask")?.multiArrayValue,
            let dfCoefs = output.featureValue(for: "df_coefs")?.multiArrayValue
        else {
            throw NSError(
                domain: "ChurchBridgeAudioBench.DeepFilterNet3",
                code: 2100,
                userInfo: [NSLocalizedDescriptionKey: "DeepFilterNet3 prediction output was missing one or more tensors."]
            )
        }

        return (erbMask, dfCoefs)
    }
}
