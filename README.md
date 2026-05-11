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
- The benchmark app currently includes a minimal SwiftUI shell plus a benchmark-owned copy of the current audio capture manager.
- The initial server strategy is to reuse the existing Church Bridge backend in [churchbridge-ai](C:/Users/Dan/Desktop/Projects/churchbridge-ai) rather than build a second ingest stack immediately.

## Open In Xcode

Open:

- `ChurchBridgeAudioBench.xcodeproj`

Select scheme:

- `ChurchBridgeAudioBench`

## Build And Distribution

- Preferred distribution path: Xcode Cloud to TestFlight
- Optional local verification path: clone this repo in the macOS VM and build/archive directly from `ChurchBridgeAudioBench.xcodeproj`

The benchmark app no longer depends on `ChurchBridgeTranslation.xcodeproj`.

## Detailed Plan

See [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md).

Execution details:

- [detailed-execution-plan.md](./docs/detailed-execution-plan.md)

Server/backend reuse notes:

- [server-reuse-plan.md](./docs/server-reuse-plan.md)
