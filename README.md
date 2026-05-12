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
- The benchmark app now defaults to the lab controller/backend endpoints at `ws://192.168.0.202:8765` and `http://192.168.0.202:8000`.
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
  --base-url http://192.168.0.202:8000 `
  --church-id benchmark-lab `
  --scenario-id john3_16_room_a `
  --expected-transcript "For God so loved the world" `
  --audio-path C:\path\to\john3_16_room_a.wav
```

Multi-file scenario-manifest example:

```powershell
python -m controller.run_session `
  --base-url http://192.168.0.202:8000 `
  --church-id benchmark-lab `
  --scenario-file .\scenarios\example-benchmark-session.json
```

Notes:

- `--variants` is now optional; the default run matrix is `apple_aec_only`, `apple_aec_plus_current_cleanup`, `raw_debug`, and `apple_aec_plus_deepfilternet3`.
- Scenario manifests can queue multiple local audio files, and each scenario will be run across the selected pipeline matrix in a stable order.
- The controller uses local playback tools on this PC, so it can simulate a live room session while the iPhone remains untouched.
- Scenario manifests can also reference a time window inside a longer source file by using `playback_start_seconds` and `playback_duration_seconds`.

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
