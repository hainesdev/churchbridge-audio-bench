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

- benchmark-owned run and STT config models in [models.py](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/ChurchBridgeAudioBench/controller/models.py)
- benchmark-owned audio helpers in [audio_helpers.py](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/ChurchBridgeAudioBench/controller/audio_helpers.py)
- a display feed client in [display_feed_client.py](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/ChurchBridgeAudioBench/controller/display_feed_client.py)
- JSON report output in [report_writer.py](C:/Users/Dan/Desktop/Projects/macOS-ios-dev/shared/churchbridge-ios/ChurchBridgeAudioBench/controller/report_writer.py)

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
