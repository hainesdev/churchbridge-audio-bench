# Server Reuse Plan

`ChurchBridgeAudioBench` should reuse the existing Church Bridge backend where practical, but the benchmark repo should prefer benchmark-owned copies and rewrites of useful code over a deep runtime dependency on the full `churchbridge-ai` project.

The benchmark's center of gravity should stay on the client side:

- the iPhone should do the meaningful conditioning
- the iPhone should perform the final STT-oriented conversion
- the backend should mostly receive an already-shaped client output

Primary server reference:

- [churchbridge-ai](C:/Users/Dan/Desktop/Projects/churchbridge-ai)

## What To Borrow And Rewrite

### Existing stream contract

Keep the current `/api/stream/v1` contract as the first benchmark backend target, but model the needed payload shapes locally inside the benchmark controller.

Relevant files:

- [stream.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/routes/stream.py)
- [session_manager.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/services/session_manager.py)
- [stt.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/services/stt.py)

Important observations:

- The iPhone app already matches the `session.start` / `audio` / `session.stop` flow.
- The server still accepts base64 Float32 audio from the client.
- `sttConfig` is already modeled and can be extended into benchmark `RunSpec` values later.
- The server now accepts an optional `benchmarkCapture` object on `session.start` so benchmark runs can request named server-side audio retention.
- We should not treat the server as the place where final benchmark audio shaping happens.

### Existing display contract

Reuse the current `/api/display/v1` stream for transcript and translation event capture during benchmark runs, with a benchmark-owned client implementation.

Relevant files:

- [display.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/routes/display.py)
- [broadcaster.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/services/broadcaster.py)

Why this matters:

- The controller can subscribe to the same feed the app already uses.
- This avoids building a benchmark-only result channel before we know we need one.

### Existing recording and benchmark patterns

Borrow structure from the existing replay benchmark and recorder utilities, then rewrite only the parts the benchmark actually needs.

Relevant files:

- [session_recorder.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/services/session_recorder.py)
- [TESTING_AND_BENCHMARKS.md](C:/Users/Dan/Desktop/Projects/churchbridge-ai/TESTING_AND_BENCHMARKS.md)

Useful patterns to carry over:

- per-run artifact bundles
- JSON/JSONL event capture
- latency percentile summaries
- repeatable benchmark entrypoints

## What Not To Copy Blindly

- the full Church Bridge translation pipeline
- Redis and broadcaster internals unless the benchmark controller truly needs them
- sermon-specific enrichment logic
- production database/session persistence

The benchmark harness should consume the existing backend, not fork the entire app server, and it should not import large chunks of production server internals unless there is a clear payoff.

## Recommended First Integration Shape

Phase 1 server strategy:

1. Keep `churchbridge-ai` as the active STT/display backend.
2. Let the benchmark iPhone app connect to that backend using the existing stream contract.
3. Build benchmark-local controller modules that copy and adapt only the useful pieces of:
   - STT config modeling
   - display event collection
   - artifact writing
4. Build the PC benchmark controller so it:
   - sends benchmark `RunSpec` to the iPhone
   - starts playback locally
   - subscribes to `/api/display/v1`
   - collects transcript/timing artifacts
   - requests server-side audio retention for each benchmark run
   - writes benchmark reports under `ChurchBridgeAudioBench/reports/`

This gives us an end-to-end live acoustic benchmark without needing a second backend service.

## Future Optional Borrowing

If we need a benchmark-specific sidecar later, likely borrow in this order:

1. `STTConfig` modeling from [stt.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/services/stt.py)
2. event capture ideas from [session_recorder.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/services/session_recorder.py)
3. replay/scoring harness structure from [TESTING_AND_BENCHMARKS.md](C:/Users/Dan/Desktop/Projects/churchbridge-ai/TESTING_AND_BENCHMARKS.md)

That keeps the benchmark controller small while still leveraging the parts of the existing backend that are already battle-tested.

## Server-Side Capture Expectation

The benchmark flow should assume that some pipeline variants will only show their real strengths or failure modes after offline listening. Because of that, benchmark runs should request backend-side audio retention by default.

Recommended shape in `churchbridge-ai`:

- tag each run with `session_id`, `run_id`, `scenario_id`, and `pipeline_id`
- retain the received benchmark audio stream in a per-run artifact directory
- keep recorder metadata beside the audio so display-feed events and transcripts can be aligned later
- make it easy to inspect both baseline and experimental runs without needing a second field deployment

Current implementation status:

- `churchbridge-ai` now accepts `benchmarkCapture` on `/api/stream/v1`
- named benchmark captures are saved under benchmark-specific audio/event paths
- capture records persist the benchmark identifiers plus a metadata sidecar path
- the remaining client task is to send that payload from the benchmark iPhone stream client

This can likely borrow from [session_recorder.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/services/session_recorder.py), but the benchmark should keep its own run labels and artifact semantics.
