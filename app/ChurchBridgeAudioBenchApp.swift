import SwiftUI

@main
struct ChurchBridgeAudioBenchApp: App {
    @State private var viewModel = BenchmarkViewModel()

    var body: some Scene {
        WindowGroup {
            BenchmarkView(viewModel: viewModel)
        }
    }
}
