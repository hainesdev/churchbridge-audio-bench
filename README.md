# ChurchBridge Audio Bench

Dedicated iOS benchmark harness for evaluating live speech-capture pipelines before STT.

## Purpose

This project exists to answer one question reliably:

Which iOS audio-processing pipeline produces the best live end-to-end STT results under realistic room playback conditions?

More specifically:

Which client-side iPhone pipeline does the best job of conditioning and converting live room audio into the final STT-ready form before it is sent to the backend?

The benchmark setup assumes:

- A local PC acts as the controller and playback source.
- A physical iPhone runs the benchmark app.
- The PC and iPhone are placed close together in the same room.
- The iPhone captures the live acoustic playback, applies a selected processing pipeline, and streams to the STT backend.
- The iPhone is responsible for producing the final STT-ready audio form on-device, not leaving the final conditioning step to the server.
- The controller records latency, transcript quality, runtime telemetry, and failure signals for each run.
- Benchmark runs should also request server-side audio retention so surprising results can be audited later without reproducing the room setup immediately.

## Initial Scope

- Reuse proven pieces from the existing Church Bridge iOS app:
  - microphone capture lifecycle
  - voice-processing-first session setup
  - chunking and websocket transport patterns
  - diagnostics collection
- Focus the benchmark on client-side audio conditioning and final STT input conversion:
  - route-aware capture
  - speech-focused cleanup
  - sample-rate conversion
  - chunk shaping for backend ingestion
- Add benchmark-specific orchestration:
  - remote run control from the PC
  - repeatable scenario execution
  - structured report export
  - automatic comparison across pipeline variants
- Integrate DeepFilterNet3 on iOS 18+ after the benchmark harness is working.

## Development Strategy

- Expect some audio-pipeline ideas to fail or regress in real rooms even if they look promising in code.
- Develop progressively:
  - start from known-working capture and conversion paths
  - add one small conditioning change at a time
  - preserve multiple viable variants instead of replacing the previous path too early
- Borrow from real-world working code where possible, but keep benchmark variants explicit so they can be compared rather than silently swapped.
- Minimize deployment overhead by batching multiple benchmarkable variants into one app build, then testing several approaches in a single field session.

## Primary Benchmark

The primary benchmark is a live end-to-end acoustic test:

1. The PC loads a known audio source and transcript.
2. The iPhone receives a `RunSpec` from the PC.
3. The iPhone configures the requested pipeline.
4. The PC starts playback.
5. The iPhone captures, conditions, converts, and chunks audio into its final STT-ready form, then streams that output to STT.
6. The PC and iPhone emit structured timing, quality, and health metrics.
7. Results are saved in machine-readable form for comparison.

## Key Deliverables

- A standalone iOS benchmark app project: `ChurchBridgeAudioBench.xcodeproj`
- Shared benchmark core code extracted from the current app
- A PC-side benchmark controller
- Repeatable pipeline comparison runs
- JSON and human-readable run reports

## Current Status

- `ChurchBridgeAudioBench` now lives in its own standalone repository and Xcode project.
- The project entry point is `ChurchBridgeAudioBench.xcodeproj`.
- The benchmark app now supports controller-driven queued multi-variant sessions against the existing backend.
- The benchmark app's controller and backend endpoints are editable in-app and
  persisted per device. They default to `ws://benchmark-controller.local:8765`
  and `http://benchmark-controller.local:8000`; to preset them for a build, add
  `BenchmarkControllerURL` and `BenchmarkBackendBaseURL` to the target's
  Info.plist. Replace `<controller-host>` in the commands below with the address
  of the machine running the controller.
- The benchmark app can auto-connect to the controller on launch and when the app returns to the foreground, which means the phone can be left open and driven remotely by the PC controller.
- The benchmark app currently includes a minimal SwiftUI shell plus a benchmark-owned copy of the current audio capture manager.
- The initial server strategy is to reuse the existing Church Bridge backend in [churchbridge-ai](C:/Users/Dan/Desktop/Projects/churchbridge-ai) rather than build a second ingest stack immediately.
- The app-side stream startup race that could drop first-run server WAV capture has been fixed by waiting for backend `session_started` before beginning capture.
- End-to-end live sessions are now producing backend WAV captures under `churchbridge-ai/tests/audio/captured/benchmarks/`.
- The controller can now schedule local playback automatically from one or more scenario manifests, so one PC command can drive multiple room-audio clips across multiple iPhone pipeline variants.

## Live Verification Snapshot

Latest verified benchmark session:

- `session-20260512T154520Z`

Latest capture artifacts:

- [run-01 apple_aec_only WAV](C:/Users/Dan/Desktop/Projects/churchbridge-ai/tests/audio/captured/benchmarks/session-20260512T154520Z/run-01-apple_aec_only_apple_aec_only_john3_16_room_a-apple_aec_only.wav)
- [run-02 apple_aec_plus_current_cleanup WAV](C:/Users/Dan/Desktop/Projects/churchbridge-ai/tests/audio/captured/benchmarks/session-20260512T154520Z/run-02-apple_aec_plus_current_cleanup_apple_aec_plus_current_cleanup_john3_16_room_a-apple_aec_plus_current_cleanup.wav)
- [run-03 raw_debug WAV](C:/Users/Dan/Desktop/Projects/churchbridge-ai/tests/audio/captured/benchmarks/session-20260512T154520Z/run-03-raw_debug_raw_debug_john3_16_room_a-raw_debug.wav)

Current interpretation:

- `apple_aec_only` is a usable stable baseline.
- `raw_debug` is also producing healthy capture artifacts.
- `apple_aec_plus_current_cleanup` remains unstable and currently behaves like an experimental path despite being labeled `conservative`.
- All meaningful cleanup and final STT-ready conversion for benchmark variants is intended to happen on the iPhone client before backend ingest.

## Open In Xcode

Open:

- `ChurchBridgeAudioBench.xcodeproj`

Select scheme:

- `ChurchBridgeAudioBench`

## Build And Distribution

- Preferred distribution path: Xcode Cloud to TestFlight
- Optional local verification path: clone this repo in the macOS VM and build/archive directly from `ChurchBridgeAudioBench.xcodeproj`

The benchmark app no longer depends on `ChurchBridgeTranslation.xcodeproj`.

## Automated Session Flow

Goal state for normal benchmarking:

1. Open the app on the iPhone.
2. Leave `Auto-Connect On Launch` enabled.
3. Let the phone reconnect itself to the controller.
4. Start a benchmark session from the PC controller.

At that point the controller can:

- send each `RunSpec`
- wait for the iPhone to report `ready`
- start local room-audio playback on the PC
- collect display-feed events and device telemetry
- request backend WAV retention for each run

No manual taps are required between runs once the app is open and connected.

## Controller Automation

Single-scenario automated playback example:

```powershell
python -m controller.run_session `
  --base-url http://<controller-host>:8000 `
  --church-id benchmark-lab `
  --scenario-id john3_16_room_a `
  --expected-transcript "For God so loved the world" `
  --audio-path C:\path\to\john3_16_room_a.wav
```

Multi-file scenario-manifest example:

```powershell
python -m controller.run_session `
  --base-url http://<controller-host>:8000 `
  --church-id benchmark-lab `
  --scenario-file .\scenarios\example-benchmark-session.json
```

Notes:

- `--variants` is now optional; the default run matrix is `apple_aec_only`, `apple_aec_plus_current_cleanup`, `raw_debug`, `deepfilternet3_only`, and `apple_aec_plus_deepfilternet3`.
- Scenario manifests can queue multiple local audio files, and each scenario will be run across the selected pipeline matrix in a stable order.
- The controller uses local playback tools on this PC, so it can simulate a live room session while the iPhone remains untouched.
- Scenario manifests can also reference a time window inside a longer source file by using `playback_start_seconds` and `playback_duration_seconds`.
- Controller summaries now derive canonical final transcript, first partial/final latency, WER, CER, and transcript similarity directly from collected `stt_*` display events.

Mic-array steering sweep on the winning Apple path:

```powershell
python -m controller.run_session `
  --base-url http://<controller-host>:8000 `
  --church-id benchmark-lab `
  --scenario-file .\scenarios\generated-srt-scenarios.json `
  --variants apple_aec_only apple_aec_plus_deepfilternet3 `
  --mic-profiles auto front_cardioid back_cardioid
```

Subtle-vs-strong DFN3 sweep without changing the rest of the run matrix:

```powershell
python -m controller.run_session `
  --base-url http://<controller-host>:8000 `
  --church-id benchmark-lab `
  --scenario-file .\scenarios\generated-srt-scenarios.json `
  --variants apple_aec_plus_deepfilternet3 deepfilternet3_only `
  --dfn3-profiles subtle balanced full
```

Manual DFN3 tuning pass that keeps the model effect intentionally small:

```powershell
python -m controller.run_session `
  --base-url http://<controller-host>:8000 `
  --church-id benchmark-lab `
  --scenario-file .\scenarios\generated-srt-scenarios.json `
  --variants apple_aec_plus_deepfilternet3 `
  --dfn3-profiles subtle `
  --dfn3-wet-mix 0.30 `
  --dfn3-loudness-compensation 0.90 `
  --dfn3-post-gain-db 0.5
```

## Physical Noise Methodology

For more realistic room-noise testing, prefer this setup over synthetic controller-side noise:

- Use the PC speakers for the speech source only.
- Use a separate physical noise source in the room, such as a box fan.
- Keep `--degradation-condition` unset so the controller does not digitally mix speech and noise into the same playback signal.
- Record the room setup in the session artifacts with `--environment-label` and one or more `--environment-note` values.

Example box-fan session:

```powershell
python -m controller.run_session `
  --base-url http://<controller-host>:8000 `
  --church-id benchmark-lab `
  --scenario-file .\scenarios\generated-srt-scenarios.json `
  --environment-label box_fan_medium `
  --environment-note "Speech comes from PC speakers only." `
  --environment-note "Box fan placed 6 ft from phone, medium setting." `
  --environment-note "Phone remains screen-facing toward the speech source."
```

Recommended discipline for repeatable physical-noise sessions:

- keep the phone position fixed
- keep speaker volume fixed
- keep fan position and speed fixed
- run one clean pass with the fan off, then one noisy pass with the fan on
- compare the same scenario matrix across both sessions

Generate windowed scenarios from long sermon recordings plus SRT files:

```powershell
python -m controller.generate_srt_scenarios `
  --source-dir C:\Users\Dan\Desktop\Projects\churchbridge-ai\tests\audio\1 `
  --source-dir C:\Users\Dan\Desktop\Projects\churchbridge-ai\tests\audio\2 `
  --manifest-path .\scenarios\generated-srt-scenarios.json
```

## Detailed Plan

See [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md).

Execution details:

- [detailed-execution-plan.md](./docs/detailed-execution-plan.md)
- [handoff-status-2026-05-12.md](./docs/handoff-status-2026-05-12.md)

Server/backend reuse notes:

- [server-reuse-plan.md](./docs/server-reuse-plan.md)

## License

Source-available under the
[PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0) —
any noncommercial purpose is permitted; commercial use requires a separate
written license. See [LICENSE](./LICENSE) and [LICENSE-FAQ.md](./LICENSE-FAQ.md).

The DeepFilterNet3 signal chain in `core/` implements the architecture from
[DeepFilterNet](https://github.com/Rikorose/DeepFilterNet) (MIT) and contains
auxiliary-data loading derived from
[soniqo/speech-swift](https://github.com/soniqo/speech-swift) (Apache-2.0). The model
weights are fetched at runtime from
[aufklarer/DeepFilterNet3-CoreML](https://huggingface.co/aufklarer/DeepFilterNet3-CoreML)
(Apache-2.0) rather than redistributed here. See
[THIRD-PARTY-NOTICES.md](./THIRD-PARTY-NOTICES.md).

## Acknowledgments

The pipeline under test is **DeepFilterNet3** by Hendrik Schröter, Tobias
Rosenkranz, Alberto N. Escalante-B., and Andreas Maier. The architecture and
trained model are theirs; this harness reimplements the streaming signal chain
in Swift in order to measure it on device.

> Schröter, H., Rosenkranz, T., Escalante-B., A. N., and Maier, A.
> "DeepFilterNet: Perceptually Motivated Real-Time Speech Enhancement."
> INTERSPEECH, 2023.

The model itself is
[aufklarer's INT8 Core ML conversion](https://huggingface.co/aufklarer/DeepFilterNet3-CoreML),
fetched at runtime rather than redistributed here.

Two Swift projects shortened the road materially. The `.npz` auxiliary-data
loading is derived from
[soniqo/speech-swift](https://github.com/soniqo/speech-swift), and
[Ghostkwebb/MetalVoice](https://github.com/Ghostkwebb/MetalVoice) was read as a
streaming reference while this was being designed — no code was taken from it,
but it saved a lot of guessing.

Full license terms are in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md).
