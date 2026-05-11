# Metric Definitions

This benchmark should save raw inputs and derived metrics separately so later analysis can be repeated without rerunning capture.

## Latency metrics

- `first_partial_latency_ms`: Time from controller playback start to first partial transcript.
- `first_stable_latency_ms`: Time from controller playback start to first transcript segment that does not regress.
- `first_final_latency_ms`: Time from controller playback start to first final transcript.
- `total_completion_latency_ms`: Time from controller playback start to end-of-run transcript completion.

## Transcript quality metrics

- `wer`: Word error rate against the expected transcript.
- `cer`: Character error rate against the expected transcript.
- `dropped_word_count`: Reference words missing from the final transcript.
- `extra_word_count`: Extra words not present in the reference transcript.
- `normalized_transcript`: Final transcript after whitespace and casing normalization used for scoring.

## Audio and pipeline metrics

- `input_sample_rate`: Hardware input sample rate observed by the app.
- `output_sample_rate`: Sample rate emitted toward the STT backend.
- `chunk_sample_count`: Number of mono float32 samples per emitted chunk.
- `tap_callback_count`: Number of audio tap callbacks during the run.
- `processed_frame_count`: Number of frames processed by the selected pipeline.
- `clip_count`: Number of frames or windows that exceeded the clipping threshold.
- `denoiser_avg_ms`: Mean processing time spent inside the enhancement stage.
- `denoiser_p95_ms`: P95 processing time spent inside the enhancement stage.

## Health metrics

- `conversion_failure_count`: Number of resample or format conversion failures.
- `chunk_send_failure_count`: Number of chunk send failures to the STT transport.
- `route_change_count`: Number of route changes detected during the run.
- `warning_count`: Number of non-fatal warnings emitted by the app.
- `error_count`: Number of fatal or terminal errors emitted by the app.

## Storage guidance

- Save raw timestamps in ISO 8601 when they come from wall clock time.
- Save relative timings in milliseconds as integers.
- Save enough raw transcript history to recompute WER and latency later.
