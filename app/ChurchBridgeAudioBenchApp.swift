import SwiftUI

@main
struct ChurchBridgeAudioBenchApp: App {
    @State private var viewModel = BenchmarkViewModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            BenchmarkView(viewModel: viewModel)
                .onChange(of: scenePhase, initial: true) { _, nextPhase in
                    if nextPhase == .active {
                        viewModel.handleAppDidBecomeActive()
                    }
                }
        }
    }
}
