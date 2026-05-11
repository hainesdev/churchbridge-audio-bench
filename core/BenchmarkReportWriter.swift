import Foundation

struct BenchmarkReportWriter {
    func encodeRunResult(_ result: BenchmarkRunResult) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        return try encoder.encode(result)
    }
}
