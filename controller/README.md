# PC Controller

This directory will hold the PC-side benchmark controller for `ChurchBridgeAudioBench`.

## Responsibilities

- discover or connect to the iPhone benchmark app
- send `RunSpec` messages
- trigger playback of known audio sources
- collect transcript outputs and timing
- receive telemetry and structured run results
- write JSON and Markdown reports

The controller is important, but it is not the primary audio-processing surface. The main benchmark target is the client-side pipeline that emits final STT-ready audio.

It should also help us get more signal per deployment by running several candidate variants in one session whenever possible.

## Initial implementation plan

1. Start with a lightweight WebSocket control server.
2. Copy and rewrite useful pieces from `churchbridge-ai` into benchmark-owned controller code instead of importing the whole server project.
3. Keep the existing `churchbridge-ai` backend as the first live STT/display target for `/api/stream/v1` and `/api/display/v1`.
4. Load scenario metadata from local JSON fixtures.
5. Support small queued multi-variant run matrices so one app launch can test several approaches.
6. Save one artifact bundle per run under `../reports/`.
7. Add report aggregation after single-run flow works.

## Status

Early scaffold now exists for:

- benchmark-owned run and STT config models in [models.py](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench/controller/models.py)
- benchmark-owned audio helpers in [audio_helpers.py](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench/controller/audio_helpers.py)
- a display feed client in [display_feed_client.py](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench/controller/display_feed_client.py)
- JSON report output in [report_writer.py](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench/controller/report_writer.py)
- scenario-manifest loading in [scenarios.py](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench/controller/scenarios.py)
- automated local playback scheduling in [audio_helpers.py](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/ChurchBridgeAudioBench/controller/audio_helpers.py)

Primary server-side references:

- [churchbridge-ai](C:/Users/Dan/Desktop/Projects/churchbridge-ai)
- [stream.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/routes/stream.py)
- [display.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/routes/display.py)
- [session_recorder.py](C:/Users/Dan/Desktop/Projects/churchbridge-ai/server/services/session_recorder.py)

Design rule:

- treat `churchbridge-ai` as a source of patterns and contract details
- prefer copying and rewriting the small chunks we need into this folder
- avoid a hard runtime dependency on the entire existing server project unless it proves clearly worth it
- optimize for field efficiency by comparing multiple variants per deploy when practical

## Current Automated Flow

Once the iPhone app is open and connected to the controller, the PC can now drive the benchmark end to end:

1. send `RunSpec`
2. wait for `ready`
3. schedule local playback from a configured audio file
4. notify the iPhone to start capture
5. collect display events, telemetry, and the final run result
6. write a per-run artifact bundle plus session summary

Scenario manifests can define one or many audio clips. The controller will run each scenario across the requested pipeline list in order.

Example:

```powershell
python -m controller.run_session `
  --base-url http://192.168.0.202:8000 `
  --church-id benchmark-lab `
  --scenario-file .\scenarios\example-benchmark-session.json
```

If `--variants` is omitted, the controller now uses the default benchmark matrix:

- `apple_aec_only`
- `apple_aec_plus_current_cleanup`
- `raw_debug`
- `deepfilternet3_only`
- `apple_aec_plus_deepfilternet3`

The controller can also sweep mic steering and DFN3 tuning without changing app code between runs:

```powershell
python -m controller.run_session `
  --base-url http://192.168.0.202:8000 `
  --church-id benchmark-lab `
  --scenario-file .\scenarios\generated-srt-scenarios.json `
  --variants apple_aec_only apple_aec_plus_deepfilternet3 `
  --mic-profiles auto front_cardioid back_cardioid `
  --dfn3-profiles subtle balanced
```

Important controller-side additions:

- `--mic-profiles` sweeps built-in mic-array steering profiles across the selected pipelines.
- `--dfn3-profiles` sweeps named DFN3 tuning presets across DFN3-backed pipelines only.
- `--dfn3-wet-mix`, `--dfn3-loudness-compensation`, `--dfn3-max-compensation-gain`, `--dfn3-post-gain-db`, and `--dfn3-peak-limit` let one session override the DFN3 preset numerically.
- Session summaries now include controller-derived final transcript, first partial/final latency, WER, CER, transcript similarity, selected mic data source, selected polar pattern, and max capture restarts per run.

## Physical Room Noise

When you want room noise from a real source like a box fan, leave controller-side degradation disabled and let the controller play speech only.

Recommended command pattern:

```powershell
python -m controller.run_session `
  --base-url http://192.168.0.202:8000 `
  --church-id benchmark-lab `
  --scenario-file .\scenarios\generated-srt-scenarios.json `
  --environment-label box_fan_medium `
  --environment-note "PC speakers provide speech playback only." `
  --environment-note "Box fan on medium, fixed position for whole session."
```

The session summary and each run artifact will record that environment metadata so later analysis can distinguish physical-noise runs from synthetic-noise runs.

## Long-Form Source Audio

The controller can now play a timed window from inside a longer source file instead of requiring one file per short benchmark clip.

Useful manifest fields:

- `playback_start_seconds`
- `playback_duration_seconds`

This is especially helpful when the source material is a long sermon plus an SRT transcript.

Generate a manifest from long audio + SRT pairs:

```powershell
python -m controller.generate_srt_scenarios `
  --source-dir C:\Users\Dan\Desktop\Projects\churchbridge-ai\tests\audio\1 `
  --source-dir C:\Users\Dan\Desktop\Projects\churchbridge-ai\tests\audio\2 `
  --manifest-path .\scenarios\generated-srt-scenarios.json
```
