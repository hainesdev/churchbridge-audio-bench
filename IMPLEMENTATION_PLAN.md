# ChurchBridge Audio Bench Implementation Plan

## 1. Summary

`ChurchBridgeAudioBench` will be a dedicated iOS 18+ benchmark app for comparing live speech-capture pipelines before STT. It will reuse stable work from the current Church Bridge iPhone app, but its design will optimize for:

- repeatability
- observability
- automation
- structured reporting
- fast iteration on pipeline variants

The benchmark will be driven by a local PC controller that:

- selects a scenario
- starts playback
- instructs the iPhone which pipeline to use
- collects transcript and telemetry results
- computes comparisons across runs

This app is not a production translation client. It is a measurement harness.

Its primary technical purpose is to evaluate how well the iPhone can condition and convert live acoustic input into the final STT-ready audio representation on the client side.

## 2. Goals

### Primary goals

- Benchmark real-world live acoustic capture performance on a physical iPhone.
- Compare Apple voice-processing-only capture against more advanced enhancement paths.
- Make the client produce the final STT-ready audio form, including conditioning, resampling, and chunk shaping.
- Measure both transcript quality and runtime behavior.
- Automate scenario execution from a remote controller.
- Produce durable JSON reports for later analysis.

### Secondary goals

- Reuse as much of the existing audio and transport work as possible.
- Keep production app experimentation isolated from benchmark-specific complexity.
- Make it easy to add new pipeline variants over time.
- Make it cheap to compare several candidate approaches in one deployment cycle.

## 3. Non-Goals

- Replacing the existing Church Bridge translation app.
- Building a polished end-user experience.
- Supporting every possible STT backend from day one.
- Solving lab-grade acoustic calibration in the first milestone.
- Treating simulator behavior as authoritative for audio quality.

## 3.1 Development assumptions

- Audio pipeline work will be iterative and occasionally messy.
- Some promising variants will fail only in real acoustic playback conditions.
- We should assume we will need multiple competing implementations alive at once.
- We should optimize for learning-per-deployment, not just code cleanliness on the first pass.

## 4. Why A Separate Project Directory

The current app mixes production-facing concerns with diagnostic and experimental audio behavior. A dedicated benchmark project directory will give us:

- a clean place for benchmark-only docs and scaffolding
- an explicit boundary around reusable vs benchmark-specific code
- freedom to add test-control, telemetry, and reporting features without polluting the production app
- a clearer path to a second Xcode target in the same repo

The recommended shape is:

```text
ChurchBridgeAudioBench/
  README.md
  IMPLEMENTATION_PLAN.md
  docs/
  controller/
  app/
  reports/
```

The app, shared code, and controller can start in this directory even before we add a new Xcode target.

## 5. Benchmark Methodology

## 5.1 Physical setup

- The PC will act as the playback source and orchestration controller.
- The iPhone will run the benchmark app.
- The devices will be near each other in the same room.
- Playback should use a consistent speaker, volume, and physical placement for repeatability.

Suggested run metadata fields:

- room label
- speaker device label
- playback volume
- phone model
- iOS version
- distance estimate
- orientation
- route type

## 5.2 Primary benchmark flow

1. The PC chooses a source audio sample with a known transcript.
2. The PC sends a `RunSpec` to the iPhone.
3. The iPhone applies the requested capture and enhancement pipeline.
4. The iPhone responds `ready`.
5. The PC starts playback and records `t0`.
6. The iPhone captures acoustic playback, processes it, and streams it to STT.
7. The iPhone emits timing and health telemetry during the run.
8. The PC collects STT output and compares it to the expected transcript.
9. The iPhone sends a `RunResult`.
10. The PC stores raw outputs and a summarized comparison report.

## 5.3 Initial pipeline variants

Phase 1 variants:

- `apple_aec_only`
- `apple_aec_plus_current_cleanup`
- `raw_debug`

Phase 2 variants:

- `apple_aec_plus_deepfilternet3`
- `apple_aec_plus_deepfilternet3_plus_vad`
- `apple_aec_plus_deepfilternet3_plus_agc_tuned`

Planning rule:

- Do not collapse variants too early.
- Keep baseline, conservative, and experimental paths side by side until field data makes the choice obvious.
- Prefer a run matrix that compares several variants in one session over one-variant-per-build iteration.

## 6. Existing Work To Reuse

The current app already contains the most valuable parts of the capture stack.

Primary reuse candidates:

- [AudioCaptureManager.swift](/C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/ChurchBridgeTranslation/AudioCaptureManager.swift)
- [Models.swift](/C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/ChurchBridgeTranslation/Models.swift)
- [StreamSocketClient.swift](/C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/ChurchBridgeTranslation/StreamSocketClient.swift)
- [SettingsStore.swift](/C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/ChurchBridgeTranslation/SettingsStore.swift)
- [IOS_AUDIO_NOTES.md](/C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/IOS_AUDIO_NOTES.md)
- [IOS_DEVELOPMENT_NOTES.md](/C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/IOS_DEVELOPMENT_NOTES.md)

What is already useful:

- `AVAudioSession` setup with `.playAndRecord`
- `.voiceChat` mode selection
- `setVoiceProcessingEnabled(true)` on the engine input path
- tap-based capture flow
- mono float32 conversion
- local resampling
- chunk batching
- interruption and route-change recovery
- diagnostics fields and event timing

Recommended reuse philosophy:

- copy proven code paths first
- rewrite them into benchmark-owned variants second
- keep the original working path available while a new path is under test
- prefer several explicit approaches over one constantly-mutating pipeline during early benchmarking

What should not be carried over:

- translation-specific UI
- Bible/data features
- production display feed logic
- any production-specific copy or settings not needed for bench runs

## 7. Recommended Architecture

## 7.1 High-level structure

Recommended code split:

```text
ChurchBridgeAudioBench/
  app/
    ChurchBridgeAudioBenchApp
    Views
    ViewModels
  core/
    AudioBenchCore
  controller/
    pc-controller
  docs/
  reports/
```

Recommended modules:

- `AudioBenchCore`
  - capture engine
  - pipeline configuration
  - enhancement wrappers
  - telemetry
  - report models
- `ChurchBridgeAudioBench`
  - app target
  - minimal UI
  - local state
  - control-plane integration
- `pc-controller`
  - orchestration server/client
  - playback runner
  - transcript scoring
  - report generation

## 7.2 iPhone app data flow

```text
AVAudioSession(.playAndRecord, .voiceChat)
  -> AVAudioEngine input node with voice processing
  -> capture mixer node
  -> tap callback
  -> processing queue
  -> selected pipeline
  -> final STT-ready output format
  -> STT stream client
  -> local telemetry + remote result export
```

## 7.3 PC controller responsibilities

- discover or connect to the iPhone benchmark app
- send `RunSpec`
- await `ready`
- play benchmark audio
- drive compact multi-variant run matrices in one session
- collect timestamps and transcript outputs
- receive per-run telemetry from the iPhone
- compute quality and latency metrics
- persist run artifacts

## 7.4 Why a control plane matters

UI-driven testing alone will not be enough. The benchmark app needs an explicit network control plane so it can:

- auto-start runs
- auto-apply scenarios
- emit structured results
- support unattended repeated runs

This is much more reliable than trying to drive the whole system only through taps and screenshots.

## 8. Audio Pipeline Plan

## 8.1 Capture strategy

The baseline path should preserve Apple’s native voice pipeline:

- `AVAudioSessionCategory.playAndRecord`
- `AVAudioSession.Mode.voiceChat`
- `inputNode.setVoiceProcessingEnabled(true)`

This uses Apple’s native hardware/OS echo cancellation and voice-focused processing before any custom model work.

Even the baseline variants should still convert to the final STT-ready client output on-device. The comparison is about how much conditioning happens before that final conversion, not about whether the server finishes the job.

## 8.2 Processing order

Recommended order for advanced variants:

1. Apple AEC and voice processing
2. mono float32 normalization
3. DeepFilterNet3 speech enhancement at 48 kHz
4. optional post-enhancement gate or limiter
5. resample to STT input rate, likely 16 kHz
6. chunk and stream to STT backend

Important rule:

DeepFilterNet3 should run before we downsample to 16 kHz.

Important benchmark rule:

The client should emit the final STT-ready payload shape for all benchmarked variants. The server should act as the transcription endpoint, not as the place where final audio shaping is decided.

## 8.3 DeepFilterNet3 integration

Because the target is iOS 18+, the preferred implementation is to use `speech-swift` directly rather than only borrow ideas from it.

Primary repo:

- [soniqo/speech-swift](https://github.com/soniqo/speech-swift)

Most relevant references:

- [`SpeechEnhancement.swift`](https://github.com/soniqo/speech-swift/blob/main/Sources/SpeechEnhancement/SpeechEnhancement.swift)
- [`DeepFilterNet3Model.swift`](https://github.com/soniqo/speech-swift/blob/main/Sources/SpeechEnhancement/DeepFilterNet3Model.swift)
- [`WeightLoading.swift`](https://github.com/soniqo/speech-swift/blob/main/Sources/SpeechEnhancement/WeightLoading.swift)
- [`speech-enhancement.md`](https://github.com/soniqo/speech-swift/blob/main/docs/inference/speech-enhancement.md)

Streaming-oriented reference:

- [Ghostkwebb/MetalVoice](https://github.com/Ghostkwebb/MetalVoice)

Useful files:

- [`DeepFilterNet3_Streaming.swift`](https://github.com/Ghostkwebb/MetalVoice/blob/main/Sources/Core/AudioProcessing/DeepFilterNet3_Streaming.swift)
- [`DeepFilterNetDSP.swift`](https://github.com/Ghostkwebb/MetalVoice/blob/main/Sources/Core/AudioProcessing/DeepFilterNetDSP.swift)

## 8.4 Pipeline abstraction

Define a single protocol for all variants:

```swift
protocol AudioPipelineVariant {
    var id: String { get }
    func prepare(context: PipelineContext) async throws
    func process(_ samples: [Float], sampleRate: Int) throws -> [Float]
    func reset()
}
```

Concrete implementations:

- `AppleAECOnlyPipeline`
- `AppleAECPlusCurrentCleanupPipeline`
- `AppleAECPlusDeepFilterNet3Pipeline`
- `RawDebugPipeline`

This keeps the benchmark harness consistent while allowing different internals.

Development rule:

- pipeline variants should be easy to add, keep, and compare
- avoid refactors that force all experimentation through one mutable implementation
- favor explicit named strategies tied to reports and run specs

## 9. Measurement Plan

## 9.1 Core metrics

For every run, measure:

- first partial latency
- first stable segment latency
- first final latency
- total transcript completion time
- word error rate
- character error rate
- dropped word count
- extra word count
- chunk send failure count
- tap callback count
- processed frame count
- conversion failures
- denoiser average processing time
- denoiser p95 processing time
- clip count
- route changes during run

Meta-goal for these metrics:

- make it possible to reject weak approaches quickly
- make it obvious which small set of variants deserves the next physical-device run

## 9.2 Suggested transcript quality metrics

At minimum:

- WER
- CER
- exact final transcript
- normalized transcript

Optional later:

- punctuation F1
- number normalization accuracy
- scripture reference extraction accuracy

## 9.3 Raw telemetry to store

Store raw telemetry in JSON, not just summary strings.

Recommended fields:

- run id
- pipeline variant
- timestamps
- route info
- input sample rate
- output sample rate
- chunk size
- number of chunks emitted
- STT messages received
- app warnings
- app errors
- CPU and memory snapshots if available

## 9.4 Local observability in the app

The app should show:

- active pipeline
- route
- sample rates
- speech activity
- RMS and clipping
- chunk throughput
- denoiser timing
- last error
- controller connection status

This makes debugging field runs much easier.

## 10. Remote Testing And Automation Plan

## 10.1 Primary approach

Use an in-app control plane for benchmark execution and use external UI automation only for install/launch/smoke flows.

Why:

- network-driven benchmark runs are more repeatable
- they avoid flaky UI-only orchestration
- they allow unattended repeated runs

## 10.2 Device automation options

Recommended real-device automation references:

- [facebook/idb](https://github.com/facebook/idb)
  - strong fit for install, launch, log capture, screenshots, and physical-device orchestration
- [mobile-dev-inc/Maestro](https://github.com/mobile-dev-inc/Maestro)
  - useful for basic smoke flows and UI verification

Suggested usage split:

- `idb` for physical-device automation and log collection
- `Maestro` for simple repeatable UI checks
- control WebSocket for the actual benchmark lifecycle

## 10.3 Automatic remote testing scenarios

The benchmark app should support:

- start on launch
- auto-connect to a controller host
- run a received test spec automatically
- export result JSON without manual interaction

Possible modes:

- `manual`
- `controller_wait`
- `autorun_last_spec`

Additional optimization goal:

- support queued or batched benchmark specs so one launch/deploy cycle can exercise several variants with minimal manual overhead

## 11. STT Integration Plan

The first version should keep the existing Church Bridge stream contract whenever practical, because that reduces moving parts.

Existing server contract notes are documented in:

- [IOS_AUDIO_NOTES.md](/C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/IOS_AUDIO_NOTES.md)

Initial recommendation:

- keep base64 float32 chunk transport
- keep 16 kHz mono output to the backend
- benchmark the client-side conditioning/conversion pipeline before changing network shape
- reuse the current backend implementation in [churchbridge-ai](C:/Users/Dan/Desktop/Projects/churchbridge-ai) for the first benchmark loops instead of building a second ingest service

Operational rule:

- treat the backend primarily as an STT endpoint and comparison surface
- keep the final audio conditioning and conversion responsibility on the iPhone client

Later, if needed, add benchmark backends behind a `TranscriptionBackend` abstraction.

Backend reuse references:

- [stream.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/routes/stream.py)
- [display.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/routes/display.py)
- [session_manager.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/services/session_manager.py)
- [session_recorder.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/services/session_recorder.py)
- [TESTING_AND_BENCHMARKS.md](C:/Users/Dan/Desktop/Projects/churchbridge-ai/TESTING_AND_BENCHMARKS.md)

## 12. Report Format

Every run should generate:

- one machine-readable JSON report
- one human-readable summary

Recommended artifact set:

```text
reports/
  YYYY-MM-DD/
    run-<id>.json
    run-<id>.md
    run-<id>-transcript.txt
    summary.json
    summary.md
```

`run-<id>.json` should contain:

- run spec
- device metadata
- app telemetry
- controller timings
- transcript outputs
- scoring results

## 13. Milestone Plan

## Milestone 0: Directory and planning

Deliverables:

- benchmark project directory
- written architecture and implementation plan

Success criteria:

- project scope is explicit
- reuse plan is explicit
- benchmark methodology is explicit

## Milestone 1: Dedicated benchmark app target

Deliverables:

- new iOS target `ChurchBridgeAudioBench`
- minimal bench UI
- extracted reusable audio core

Success criteria:

- app launches on simulator and device
- baseline capture works
- client produces STT-ready output locally
- telemetry is visible locally

## Milestone 2: Controller protocol

Deliverables:

- control-plane connection to the PC
- `RunSpec` and `RunResult` models
- remote scenario execution

Success criteria:

- PC can trigger a run remotely
- app returns a structured result

## Milestone 3: Baseline acoustic benchmark

Deliverables:

- `apple_aec_only`
- `apple_aec_plus_current_cleanup`
- `raw_debug`

Success criteria:

- repeated runs produce saved reports
- latency and quality metrics compare correctly
- at least a small multi-variant comparison can be completed in one field session without rebuilding between variants

## Milestone 4: DeepFilterNet3 integration

Deliverables:

- `speech-swift` integration
- `apple_aec_plus_deepfilternet3`
- enhancement timing telemetry

Success criteria:

- DFN3 variant runs end-to-end on physical device
- results can be compared against baseline variants

## Milestone 5: Automatic remote testing

Deliverables:

- device automation support
- scripted run matrix execution
- report aggregation

Success criteria:

- repeated unattended multi-run comparisons complete from a single PC command

## 14. Concrete File And Code Plan

## 14.1 New benchmark-side files

Suggested initial files:

```text
ChurchBridgeAudioBench/
  README.md
  IMPLEMENTATION_PLAN.md
  docs/
    controller-protocol.md
    metric-definitions.md
  app/
    ChurchBridgeAudioBenchApp.swift
    BenchmarkView.swift
    BenchmarkViewModel.swift
  core/
    AudioPipelineVariant.swift
    BenchmarkAudioCaptureManager.swift
    BenchmarkModels.swift
    BenchmarkTelemetry.swift
    BenchmarkReportWriter.swift
  controller/
    README.md
    protocol.md
```

## 14.2 Current app extraction candidates

Refactor or copy initially:

- `AudioCaptureManager` -> `BenchmarkAudioCaptureManager`
- diagnostics structs -> `BenchmarkModels`
- transport helpers -> `BenchmarkSocketClient`

Initial recommendation:

- copy first
- extract shared code second

That reduces risk while the benchmark app is still changing rapidly.

## 15. Risks And Mitigations

## Risk 1: Acoustic variability between runs

Mitigation:

- define placement protocol
- record environment metadata
- repeat each scenario multiple times

## Risk 2: UI automation is too flaky for unattended runs

Mitigation:

- keep benchmark execution network-driven
- use UI automation only for launch/smoke/install

## Risk 3: DeepFilterNet3 streaming behavior is not low-latency enough

Mitigation:

- benchmark harness first
- start with Apple AEC-only and current cleanup baselines
- measure DFN3 before overcommitting
- use `MetalVoice` as a streaming reference if needed

## Risk 6: Iteration is slowed down by redeploy/build overhead

Mitigation:

- package multiple candidate variants into the same app build
- keep controller-driven run switching lightweight
- prefer compact benchmark matrices over one-off manual runs
- use real-world working reference code as a starting point instead of inventing every path from scratch

## Risk 4: Production app code becomes entangled with benchmark code

Mitigation:

- separate directory
- separate target
- copy-first extraction strategy

## Risk 5: Controller and app clocks are not aligned enough

Mitigation:

- anchor run timing to explicit controller events
- store controller and app timestamps separately
- compare relative deltas, not only absolute wall clock values

## 16. Open Questions

- Should the PC controller also receive mirrored raw/processed audio for offline inspection?
- Should the app save local WAV artifacts for some runs?
- Which STT backend should be the default for the first comparison matrix?
- How much of `speech-swift` should be vendored vs imported directly through SPM?
- Do we want one benchmark target in the existing Xcode project or a separate Xcode project under this directory?

## 17. Recommended Immediate Next Steps

1. Add a second iOS target named `ChurchBridgeAudioBench` to the existing Xcode project.
2. Copy the current audio capture stack into benchmark-specific files.
3. Build a minimal benchmark screen with live diagnostics and a fake local run spec.
4. Add a simple PC controller with a control WebSocket and report output.
5. Get `apple_aec_only` and `apple_aec_plus_current_cleanup` running side by side before integrating DFN3.
6. Make sure one build can switch between multiple variants remotely.
7. Add `speech-swift` only after the harness itself is stable.

## 18. Relevant External References

- Upstream model and paper:
  - [Rikorose/DeepFilterNet](https://github.com/Rikorose/DeepFilterNet)
- Best Core ML DFN3 implementation:
  - [soniqo/speech-swift](https://github.com/soniqo/speech-swift)
- Streaming-oriented Core ML DFN3 reference:
  - [Ghostkwebb/MetalVoice](https://github.com/Ghostkwebb/MetalVoice)
- iOS simulator and physical-device automation:
  - [facebook/idb](https://github.com/facebook/idb)
- UI flow automation:
  - [mobile-dev-inc/Maestro](https://github.com/mobile-dev-inc/Maestro)
- Existing Church Bridge audio and deployment notes:
  - [IOS_AUDIO_NOTES.md](/C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/IOS_AUDIO_NOTES.md)
  - [IOS_DEVELOPMENT_NOTES.md](/C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/IOS_DEVELOPMENT_NOTES.md)

## 19. Execution Companion

For the concrete phase-by-phase execution sequence, controller batching strategy, and deployment-efficient run matrix, see:

- [detailed-execution-plan.md](./docs/detailed-execution-plan.md)
