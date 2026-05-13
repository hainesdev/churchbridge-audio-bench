# Handoff Status - 2026-05-12

This document is meant to let another agent resume work without re-deriving the current benchmark state.

## Current Repos

Benchmark app and controller:

- [ChurchBridgeAudioBench](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench)

Backend used for live runs and server-side capture:

- [churchbridge-ai](C:/Users/Dan/Desktop/Projects/churchbridge-ai)

Verified revisions used for the latest documented session `session-20260512T154520Z`:

- `ChurchBridgeAudioBench`: `07e55fcef5187f6987339471b61778272b7632b6`
- `churchbridge-ai`: `701e6fe3f9f1e70628162f3daf9beb5393c762e1`

## Current Goal

Evaluate multiple iPhone client-side audio pipelines that produce final STT-ready audio on-device before streaming to the backend.

Important clarification:

- `apple_aec_plus_current_cleanup` is intended to perform its cleanup on the iPhone client.
- The backend should receive already-shaped output, not decide the final audio conditioning strategy.

## What Is Working

- The standalone Xcode project and repo split are done.
- Xcode Cloud / TestFlight distribution is working well enough to install benchmark builds on device.
- The controller can run a queued multi-variant session.
- The app can connect to the controller, receive `RunSpec`, wait for `playback_started`, capture, stream, and report completion.
- The backend now stores benchmark WAV captures and metadata per run under benchmark-specific directories.
- The missing first-run WAV issue appears fixed.

## Most Important Recent Fix

The app-side stream startup used to race the backend session handshake.

Symptom:

- the first run could complete locally
- metadata could be written server-side
- but no audio chunks would reach the backend in time for WAV capture

Fix:

- [BenchmarkStreamSocketClient.swift](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench/core/BenchmarkStreamSocketClient.swift) now waits for backend `session_started` before returning from `connect(...)`
- [BenchmarkViewModel.swift](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench/app/BenchmarkViewModel.swift) now only starts capture after that awaited stream connection succeeds

Related commit:

- `07e55fc` `Harden benchmark stream startup and export settings`

## Latest Verified Session

Benchmark session:

- `session-20260512T154520Z`

Session summary:

- [summary.json](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench/reports/session-20260512T154520Z/summary.json)

Backend capture directory:

- [session-20260512T154520Z](C:/Users/Dan/Desktop/Projects/churchbridge-ai/tests/audio/captured/benchmarks/session-20260512T154520Z)

Per-run WAVs:

- [run-01 apple_aec_only](C:/Users/Dan/Desktop/Projects/churchbridge-ai/tests/audio/captured/benchmarks/session-20260512T154520Z/run-01-apple_aec_only_apple_aec_only_john3_16_room_a-apple_aec_only.wav)
- [run-02 apple_aec_plus_current_cleanup](C:/Users/Dan/Desktop/Projects/churchbridge-ai/tests/audio/captured/benchmarks/session-20260512T154520Z/run-02-apple_aec_plus_current_cleanup_apple_aec_plus_current_cleanup_john3_16_room_a-apple_aec_plus_current_cleanup.wav)
- [run-03 raw_debug](C:/Users/Dan/Desktop/Projects/churchbridge-ai/tests/audio/captured/benchmarks/session-20260512T154520Z/run-03-raw_debug_raw_debug_john3_16_room_a-raw_debug.wav)

Observed file sizes:

- `run-01 apple_aec_only`: about `156,844` bytes
- `run-02 apple_aec_plus_current_cleanup`: about `3,244` bytes
- `run-03 raw_debug`: about `160,044` bytes

Interpretation from the evidence above:

- `apple_aec_only` and `raw_debug` both produced full-sized WAV capture artifacts in this session.
- `apple_aec_plus_current_cleanup` produced a much smaller WAV capture artifact in this session.
- This is enough to say the first and third variants currently look healthier from a capture-retention perspective.
- This is not yet enough to claim transcript quality or audio-content quality without listening to the captures and comparing downstream behavior.

## Variant Intent

### `apple_aec_only`

Intended behavior:

- Apple voice-processing capture
- client-side mono conversion
- client-side final resampling to STT rate
- minimal additional cleanup beyond the explicit conversion path

Current status:

- best current baseline candidate from recent capture-retention evidence
- still needs transcript-quality and audio-content validation before being treated as fully proven

### `apple_aec_plus_current_cleanup`

Intended behavior:

- Apple voice-processing capture
- client-side mono conversion
- client-side final resampling to STT rate
- client-side speech-focused cleanup via the benchmark-local robust filtering path

Current implementation note:

- this is not currently wired to a separate production cleanup module
- it uses the benchmark-local `robustVoiceFilter` path in [BenchmarkAudioCaptureManager.swift](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench/core/BenchmarkAudioCaptureManager.swift)

Current status:

- unstable
- recent captures suggest route/restart or stage-stability problems rather than total controller/backend failure

### `raw_debug`

Intended behavior:

- minimal processing
- useful for isolating whether instability is caused by the more opinionated cleanup path

Current status:

- strongest current diagnostic comparison path from recent capture-retention evidence
- still needs audio-content validation before stronger quality claims

## Current Backend Save Locations

Server-side audio:

- `churchbridge-ai/tests/audio/captured/benchmarks/<session-id>/`

Server-side metadata and event logs:

- `churchbridge-ai/logs/sessions/benchmarks/<session-id>/`

Important reminder:

- benchmark WAVs are not stored under the benchmark repo itself
- they are stored under `churchbridge-ai`

## Relevant Files

App-side stream and run orchestration:

- [BenchmarkStreamSocketClient.swift](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench/core/BenchmarkStreamSocketClient.swift)
- [BenchmarkViewModel.swift](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench/app/BenchmarkViewModel.swift)
- [BenchmarkAudioCaptureManager.swift](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench/core/BenchmarkAudioCaptureManager.swift)
- [BenchmarkModels.swift](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench/core/BenchmarkModels.swift)

Controller-side run orchestration:

- [run_session.py](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench/controller/run_session.py)
- [control_server.py](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench/controller/control_server.py)
- [display_feed_client.py](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench/controller/display_feed_client.py)

Backend-side capture:

- [stream.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/routes/stream.py)
- [session_manager.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/services/session_manager.py)
- [session_recorder.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/services/session_recorder.py)

## Suggested Next Steps

Highest-value next investigations:

1. explain why `apple_aec_plus_current_cleanup` collapses to a tiny WAV while `apple_aec_only` and `raw_debug` stay healthy
2. inspect route-change and restart behavior during that variant specifically
3. compare the actual captured audio content and not just file size
4. decide whether to demote `apple_aec_plus_current_cleanup` from the default compact session until it is stable again

## Latest Successful Verification Command

This is the command used for the latest documented successful session.

Environment assumptions for that run:

- backend repo revision: `701e6fe3f9f1e70628162f3daf9beb5393c762e1`
- benchmark app revision: `07e55fcef5187f6987339471b61778272b7632b6`
- the backend was already running and reachable on the local LAN at `http://192.168.0.202:8000`
- the iPhone and controller PC were on the same network
- the benchmark app was already installed on the phone via TestFlight and configured to talk to the controller and backend

From [ChurchBridgeAudioBench](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench):

```powershell
python -m controller.run_session `
  --base-url http://192.168.0.202:8000 `
  --church-id benchmark-lab `
  --scenario-id john3_16_room_a `
  --expected-transcript "For God so loved the world" `
  --variants apple_aec_only apple_aec_plus_current_cleanup raw_debug `
  --run-seconds 5 `
  --display-seconds 7
```

## Important Caveats

- The benchmark app must be rebuilt and redeployed through TestFlight after app-side Swift changes.
- Local VM USB debugging is not the expected path here.
- Some older docs still assume the benchmark lived under the old shared `churchbridge-ios` project; prefer this standalone repo as source of truth.
