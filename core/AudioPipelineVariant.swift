import Foundation

struct PipelineContext: Sendable {
    let runSpec: BenchmarkRunSpec
    let outputSampleRate: Int
}

protocol AudioPipelineVariant: Sendable {
    var id: BenchmarkPipelineID { get }
    func prepare(context: PipelineContext) async throws
    func process(_ samples: [Float], sampleRate: Int) throws -> [Float]
    func reset()
}

struct PassthroughAudioPipeline: AudioPipelineVariant {
    let id: BenchmarkPipelineID

    init(id: BenchmarkPipelineID = .appleAECOnly) {
        self.id = id
    }

    func prepare(context: PipelineContext) async throws {}

    func process(_ samples: [Float], sampleRate: Int) throws -> [Float] {
        samples
    }

    func reset() {}
}
