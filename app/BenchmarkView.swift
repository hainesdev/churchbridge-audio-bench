import SwiftUI

struct BenchmarkView: View {
    @Bindable var viewModel: BenchmarkViewModel

    var body: some View {
        NavigationStack {
            List {
                Section("Run") {
                    LabeledContent("Mode", value: viewModel.runMode.displayName)
                    Picker("Pipeline", selection: $viewModel.selectedPipeline) {
                        ForEach(viewModel.availablePipelines, id: \.id) { profile in
                            Text(profile.displayName).tag(profile.id)
                        }
                    }
                    LabeledContent("Controller", value: viewModel.controllerStatus.displayName)
                    LabeledContent("Run State", value: viewModel.runState.displayName)
                    LabeledContent("Family", value: viewModel.selectedPipelineProfile.family.displayName)
                    Text(viewModel.selectedPipelineProfile.summary)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("Controller") {
                    TextField("ws://controller-host:8765", text: $viewModel.controllerURLString)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.footnote.monospaced())

                    Button("Connect To Controller") {
                        viewModel.connectToController()
                    }
                    .disabled(viewModel.controllerStatus == .connecting || viewModel.controllerStatus == .connected)

                    Button("Disconnect Controller") {
                        viewModel.disconnectFromController()
                    }
                    .disabled(viewModel.controllerStatus == .disconnected)
                }

                Section("Backend") {
                    TextField("http://127.0.0.1:8000", text: $viewModel.backendBaseURLString)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.footnote.monospaced())

                    TextField("Church ID", text: $viewModel.churchID)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()

                    LabeledContent("Stream", value: viewModel.streamStatus.displayName)
                    LabeledContent("Backend Session", value: viewModel.backendSessionID.map(String.init) ?? "None")
                }

                Section("Current Spec") {
                    LabeledContent("Bench Session", value: viewModel.activeRunSpec?.benchmarkSessionID ?? "None")
                    LabeledContent("Run ID", value: viewModel.activeRunSpec?.runID ?? "None")
                    LabeledContent("Scenario", value: viewModel.activeRunSpec?.scenarioID ?? "Not loaded")
                    LabeledContent("Expected Transcript", value: viewModel.activeRunSpec?.expectedTranscript ?? "Not loaded")
                    LabeledContent("Run Duration", value: durationText(viewModel.activeRunSpec?.runDurationMilliseconds))
                    LabeledContent("Server Capture", value: serverCaptureText(for: viewModel.activeRunSpec))
                    LabeledContent("Capture Label", value: viewModel.activeRunSpec?.serverCaptureLabel ?? "None")
                }

                Section("Controls") {
                    Button("Start Sample Run") {
                        viewModel.startSampleRun()
                    }
                    .disabled(viewModel.runState == .preparing || viewModel.runState == .running)

                    Button("Load Compact Queue") {
                        viewModel.prepareCompactSessionQueue()
                    }
                    .disabled(viewModel.runState == .preparing || viewModel.runState == .running)

                    Button("Run Queued Session") {
                        viewModel.runQueuedSampleSession()
                    }
                    .disabled(viewModel.runState == .preparing || viewModel.runState == .running)

                    Button("Stop") {
                        viewModel.stopRun()
                    }
                    .disabled(viewModel.runState == .idle)
                }

                Section("Queue") {
                    LabeledContent("Queued Runs", value: "\(viewModel.queuedRunSpecs.count)")
                    if !viewModel.queuedRunSpecs.isEmpty {
                        ForEach(viewModel.queuedRunSpecs, id: \.id) { runSpec in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(runSpec.pipelineID.displayName)
                                Text(runSpec.runID)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }

                Section("Diagnostics") {
                    LabeledContent("Route", value: viewModel.telemetry.routeName)
                    LabeledContent("Input Rate", value: viewModel.telemetry.inputSampleRateText)
                    LabeledContent("Output Rate", value: viewModel.telemetry.outputSampleRateText)
                    LabeledContent("Active Pipeline", value: viewModel.telemetry.activePipelineID)
                    LabeledContent("Output Audio", value: viewModel.telemetry.emittedAudioSecondsText)
                    LabeledContent("Chunk Samples", value: "\(viewModel.telemetry.chunkSampleCount)")
                    LabeledContent("Chunk Bytes", value: "\(viewModel.telemetry.lastChunkEncodedBytes)")
                    LabeledContent("Speech", value: viewModel.telemetry.speechDetected ? "Detected" : "Idle")
                    LabeledContent("Last Error", value: viewModel.lastError ?? "None")
                }
            }
            .navigationTitle("Audio Bench")
        }
    }

    private func durationText(_ milliseconds: Int?) -> String {
        guard let milliseconds else { return "Not loaded" }
        return String(format: "%.1f s", Double(milliseconds) / 1_000)
    }

    private func serverCaptureText(for runSpec: BenchmarkRunSpec?) -> String {
        guard let runSpec else { return "Not loaded" }
        return runSpec.saveServerCapture ? "Requested" : "Disabled"
    }
}

#Preview {
    BenchmarkView(viewModel: BenchmarkViewModel())
}
