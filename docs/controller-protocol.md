# Controller Protocol

This document defines the first-pass control plane between the PC benchmark controller and the iPhone benchmark app.

## Goals

- Let the controller start a benchmark run without UI taps.
- Keep the protocol small enough to iterate quickly.
- Return structured results that are easy to save and compare.

## Transport

- Initial transport: WebSocket
- Controller role: server or well-known host
- App role: client
- Message format: UTF-8 JSON objects

## Session flow

1. App connects to controller.
2. Controller sends `hello`.
3. App replies `device_hello`.
4. Controller sends `run_spec`.
5. App validates the request and replies `ready` or `run_rejected`.
6. Controller starts playback and sends `playback_started`.
7. App captures, processes, and streams audio to the STT backend.
8. App sends telemetry snapshots or warnings during the run.
9. App sends `run_result`.
10. Controller stores artifacts and may send `ack`.

## Initial message shapes

### `hello`

```json
{
  "type": "hello",
  "protocol_version": 1
}
```

### `device_hello`

```json
{
  "type": "device_hello",
  "protocol_version": 1,
  "device_name": "Dan's iPhone",
  "system_version": "iOS 18.2",
  "app_version": "0.1.0"
}
```

### `run_spec`

```json
{
  "type": "run_spec",
  "run_id": "2026-05-08T13-20-00Z-apple-aec-plus-deepfilternet3-front-cardioid",
  "scenario_id": "john-3-16-room-a",
  "pipeline_id": "apple_aec_plus_deepfilternet3",
  "expected_transcript": "For God so loved the world",
  "stt_sample_rate": 16000,
  "chunk_duration_ms": 100,
  "run_duration_ms": 5000,
  "save_server_capture": true,
  "server_capture_label": "john-3-16-room-a-apple-aec-plus-deepfilternet3-front-cardioid",
  "controller_started_at": "2026-05-08T13:20:00Z",
  "mic_profile": "front_cardioid",
  "dfn3_tuning": {
    "profile": "subtle",
    "wet_mix": 0.35,
    "loudness_compensation": 0.85,
    "max_compensation_gain": 2.5,
    "post_gain_db": 0.0,
    "peak_limit": 0.98
  }
}
```

### `ready`

```json
{
  "type": "ready",
  "run_id": "2026-05-08T13-20-00Z-apple-aec-plus-deepfilternet3-front-cardioid",
  "pipeline_id": "apple_aec_plus_deepfilternet3",
  "save_server_capture": true,
  "server_capture_label": "john-3-16-room-a-apple-aec-plus-deepfilternet3-front-cardioid"
}
```

### `run_result`

```json
{
  "type": "run_result",
  "run_id": "2026-05-08T13-20-00Z-apple-aec-plus-deepfilternet3-front-cardioid",
  "status": "completed",
  "first_partial_latency_ms": 820,
  "first_final_latency_ms": 2140,
  "wer": 0.08,
  "cer": 0.03,
  "final_transcript": "For God so loved the world",
  "warnings": [],
  "errors": []
}
```

## Notes

- The controller should treat timestamps from the app and controller as separate clocks.
- Benchmark runs should request a server capture label whenever later offline audio inspection matters.
- The intended backend behavior is to retain benchmark audio bundles keyed by session, run, and pipeline so failed or surprising STT results can be replayed and compared later.
- The first implementation should prefer a tolerant decoder so optional fields can grow over time.
- `mic_profile` and `dfn3_tuning` are optional and allow the controller to sweep mic-array steering and subtle DFN3 blend settings across otherwise identical runs.
