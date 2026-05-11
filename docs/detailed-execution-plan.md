# Detailed Execution Plan

This document turns the benchmark direction into an execution plan we can work from day to day.

The central objective is:

Produce final STT-ready audio on the iPhone client, compare multiple client-side approaches efficiently, and learn as much as possible from each device deployment.

## 1. Reflection Summary

The project has a few realities we should plan around from the start:

- Audio pipeline work will not be linear.
- Some approaches that look reasonable in code will fail only in real playback conditions.
- We need multiple explicit variants alive at the same time.
- Rebuild and redeploy time is expensive, so each app build should expose several benchmarkable variants.
- The backend is useful as a transcription endpoint and comparison surface, but the meaningful conditioning and conversion work belongs on the client.
- We should borrow from real working code, but prefer copying and rewriting small proven chunks into benchmark-owned code rather than creating broad hard dependencies.

## 2. Success Definition

The benchmark is succeeding when all of these are true:

- The iPhone captures room playback and emits final STT-ready audio locally.
- The app can switch among several named pipeline variants without rebuilding.
- The PC controller can run a compact multi-variant matrix in one session.
- Each run produces comparable telemetry and transcript artifacts.
- Weak approaches can be rejected quickly.
- Strong approaches can be promoted to the next round with clear evidence.

## 3. Working Principles

- Preserve a known-good baseline at all times.
- Add new conditioning steps one at a time.
- Never hide a meaningful audio change behind the same variant identifier.
- Prefer named variants over one constantly-mutating pipeline.
- Keep experimental work client-owned and benchmark-owned.
- Optimize for learning per deployment, not theoretical elegance.

## 4. Variant Taxonomy

All variants should end in client-produced STT-ready output.

Use four buckets:

- `baseline`
  The simplest working client-finalized path. Minimal cleanup beyond Apple voice processing plus final conversion.
- `conservative`
  Small improvements that are likely to help and unlikely to destabilize the pipeline.
- `experimental`
  Approaches that may improve quality but need field proof.
- `diagnostic`
  Paths that exist to expose behavior, isolate regressions, or confirm assumptions.

Recommended early mapping:

- `apple_aec_only`
  `baseline`
- `apple_aec_plus_current_cleanup`
  `conservative`
- `raw_debug`
  `diagnostic`
- `apple_aec_plus_deepfilternet3`
  `experimental`

## 5. Definition Of STT-Ready Audio

For this project, STT-ready audio means:

- capture route chosen intentionally
- Apple voice processing or an explicit alternative applied
- mono float32 normalization completed
- optional conditioning and enhancement completed
- final sample rate conversion completed on-device
- final chunk boundaries decided on-device
- payload shape ready for backend ingestion without the server deciding the last-mile audio conditioning strategy

The exact transport can stay aligned with the current backend contract for now.

## 6. Main Workstreams

### 6.1 Client pipeline workstream

Responsibilities:

- capture setup
- route handling
- conditioning
- enhancement
- final STT-ready conversion
- chunk emission
- per-stage telemetry

Primary files:

- [BenchmarkAudioCaptureManager.swift](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/ChurchBridgeAudioBench/core/BenchmarkAudioCaptureManager.swift)
- [AudioPipelineVariant.swift](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/ChurchBridgeAudioBench/core/AudioPipelineVariant.swift)
- [BenchmarkModels.swift](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/ChurchBridgeAudioBench/core/BenchmarkModels.swift)
- [BenchmarkTelemetry.swift](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/ChurchBridgeAudioBench/core/BenchmarkTelemetry.swift)

### 6.2 Controller workstream

Responsibilities:

- send `RunSpec`
- queue a batch of variants
- start playback
- subscribe to backend transcript/display output
- capture timings
- write per-run artifacts
- compare runs within the same deployment session

Primary files:

- [models.py](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/ChurchBridgeAudioBench/controller/models.py)
- [display_feed_client.py](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/ChurchBridgeAudioBench/controller/display_feed_client.py)
- [report_writer.py](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/ChurchBridgeAudioBench/controller/report_writer.py)

### 6.3 Reporting workstream

Responsibilities:

- artifact format
- run summaries
- metric comparison
- decision-friendly output

This work should stay simple at first. The early win is consistent raw artifacts and basic summaries, not polished dashboards.

### 6.4 Reference reuse workstream

Responsibilities:

- inspect real working code
- copy small useful chunks
- rewrite them into benchmark-owned modules
- keep source references documented

Primary references:

- [AudioCaptureManager.swift](/C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/ChurchBridgeTranslation/AudioCaptureManager.swift)
- [StreamSocketClient.swift](/C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/ChurchBridgeTranslation/StreamSocketClient.swift)
- [stream.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/routes/stream.py)
- [display.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/routes/display.py)
- [session_recorder.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/services/session_recorder.py)
- [speech-swift](https://github.com/soniqo/speech-swift)
- [MetalVoice](https://github.com/Ghostkwebb/MetalVoice)

## 7. Phase Plan

### Phase 0: Stabilize the harness

Goal:

Have a benchmark target that builds, launches, and exposes visible diagnostics.

Deliverables:

- benchmark target builds in the VM
- benchmark app launches on simulator and device
- basic benchmark screen exists
- benchmark-owned capture manager exists

Exit criteria:

- target builds cleanly
- screen can start and stop capture
- route and sample-rate diagnostics update

### Phase 1: Lock the baseline client-finalized path

Goal:

Establish one trustworthy pipeline that produces final STT-ready output on-device.

Deliverables:

- `apple_aec_only` finalized as a true client-output baseline
- output sample rate and chunking stable
- telemetry proves local conversion is happening

Tasks:

- make the baseline path explicit in code
- ensure sample-rate conversion is client-side
- emit chunk metadata in telemetry
- confirm transport payload shape is acceptable to the backend

Exit criteria:

- baseline variant runs end to end
- backend accepts output without requiring a new server-side decision about audio shaping
- report captures enough metadata to compare later variants against baseline

### Phase 2: Add conservative and diagnostic variants

Goal:

Create a small useful comparison set before deeper experimentation.

Deliverables:

- `apple_aec_plus_current_cleanup`
- `raw_debug`
- variant identifiers surfaced cleanly in app and controller artifacts

Tasks:

- separate variant selection from capture lifecycle
- keep cleanup stages modular
- expose active variant in UI and run artifacts

Exit criteria:

- three variants can run from one build
- switching variants does not require code changes or redeploy

### Phase 3: Single-session multi-variant controller loop

Goal:

Reduce deployment overhead by running several approaches per launch.

Deliverables:

- controller can queue multiple `RunSpec`s
- app can execute them sequentially
- reports are written per run
- summary comparison can group a session's variants together

Tasks:

- extend controller protocol to support batches or queued runs
- define run ordering rules
- add per-session summary artifact
- add lightweight retry or skip behavior if one variant fails

Exit criteria:

- one deployment can run a compact matrix without rebuild
- a failed run does not corrupt the whole session

### Phase 4: Add experimental branches

Goal:

Introduce more aggressive approaches without destabilizing the baseline set.

Deliverables:

- `apple_aec_plus_deepfilternet3`
- optional post-enhancement gate or limiter experiments
- optional AGC-tuned variant
- optional VAD-assisted variant

Tasks:

- integrate one experimental stage at a time
- benchmark CPU and latency cost
- compare against the locked baseline and conservative path

Exit criteria:

- experimental variants can be rejected or promoted based on measured data
- the baseline remains available throughout

### Phase 5: Session efficiency and unattended runs

Goal:

Maximize learning per device session.

Deliverables:

- queued run matrices
- autorun support
- report aggregation
- optional device automation for repeated sessions

Tasks:

- add queued specs
- add summary outputs
- add simple scenario packs
- add optional install/launch automation after the core loop is stable

Exit criteria:

- repeated multi-variant sessions can be triggered with minimal manual work

## 8. Deployment-Efficient Run Strategy

We should design around a compact run matrix.

Recommended early session shape:

- 3 variants per deployment
- 1 scenario per session at first
- 2 repetitions per variant when time allows
- strict ordering that always includes the baseline first

Recommended first matrix:

1. `apple_aec_only`
2. `apple_aec_plus_current_cleanup`
3. `raw_debug`

If the session is stable, add:

4. one experimental variant

Session rules:

- Always include the baseline in every session.
- Only introduce one new experimental idea per session at first.
- Keep scenario count low until the control plane is stable.
- Prefer fewer scenarios with more useful variant comparisons over broad but noisy coverage.

## 9. Decision Gates

### Gate A: Is the harness trustworthy?

Questions:

- Are route, sample rate, and chunk metrics believable?
- Is the app clearly producing client-finalized output?
- Can we rerun the same variant reliably?

If no:

- do not add more variants yet

### Gate B: Is a new variant worth carrying?

Questions:

- Does it improve transcript quality or latency?
- Does it avoid clear runtime regressions?
- Is its benefit repeatable?

If no:

- retire or park the variant

### Gate C: Is deployment efficiency improving?

Questions:

- Can multiple variants be compared per deployment?
- Is each field session producing enough evidence to decide something?

If no:

- prioritize controller batching and run packaging before adding more DSP ideas

## 10. Artifact Plan

Each run should save:

- run spec
- variant id
- device metadata
- controller timings
- app telemetry
- transcript outputs
- scoring summary
- notes about failures or anomalies

Each session should save:

- ordered run list
- comparison summary
- recommended next actions

Preferred structure:

```text
reports/
  YYYY-MM-DD/
    session-<id>/
      run-01-apple_aec_only.json
      run-02-apple_aec_plus_current_cleanup.json
      run-03-raw_debug.json
      summary.json
      summary.md
```

## 11. Failure Planning

We should plan for these recurring failure classes:

- route instability
- chunking mismatches
- conversion artifacts
- STT acceptance problems
- latency spikes
- real-room regressions
- deployment friction

Response rules:

- preserve a baseline that still runs
- isolate the failing variant instead of rewriting the whole stack
- capture enough telemetry to explain the failure
- prefer rollback by variant selection, not by emergency refactor

## 12. Immediate Execution Plan

### Step 1

Finish the baseline client-finalized path:

- make the baseline variant clearly represent Apple voice processing plus final client-side conversion
- expose output sample rate and chunk metadata in UI and telemetry

### Step 2

Turn the pipeline selection into a true variant registry:

- map variant ids to explicit strategies
- keep baseline, conservative, and diagnostic variants alive together

### Step 3

Add controller-side single-run orchestration:

- create one executable controller entrypoint
- build one `RunSpec`
- subscribe to `/api/display/v1`
- write one report

### Step 4

Extend to queued runs:

- allow one session to send several specs
- persist per-run outputs and one summary

### Step 5

Begin experimental variants:

- add one experimental conditioning path
- compare only against the stable baseline set

## 13. Recommended Near-Term Backlog

Highest priority:

1. baseline client-finalized pipeline verification
2. variant registry in app code
3. controller executable for one run
4. queued multi-variant session support

Next priority:

1. richer telemetry for chunk and conversion stages
2. per-session summary reporting
3. first experimental variant

Later priority:

1. DFN3 integration
2. automation for repeated field sessions
3. larger scenario packs

## 14. What To Avoid

- mutating the only working pipeline in place
- rebuilding for every small comparison
- importing the entire existing backend into the benchmark controller
- adding several experimental DSP ideas before the controller loop is efficient
- trusting simulator-only audio behavior

## 15. Summary

The plan is not just to build a benchmark app.

The plan is to build a benchmark system that:

- keeps several client-side approaches alive
- measures them consistently
- reduces deployment overhead
- makes it easy to reject weak ideas and keep strong ones moving
