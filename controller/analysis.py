from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from .models import BenchmarkRunResultMessage, BenchmarkRunSpec, DisplayEvent, TelemetryEvent

_PUNCTUATION_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _datetime_to_epoch_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def _event_epoch_ms(event: DisplayEvent) -> int | None:
    ts = event.payload.get("ts")
    if isinstance(ts, (int, float)):
        return int(ts)
    return _datetime_to_epoch_ms(_parse_iso_datetime(event.received_at))


def normalize_transcript_for_scoring(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
    normalized = _PUNCTUATION_RE.sub(" ", normalized)
    return " ".join(normalized.split())


def _edit_distance(left: list[str] | str, right: list[str] | str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    current = [0] * (len(right) + 1)

    for left_index, left_item in enumerate(left, start=1):
        current[0] = left_index
        for right_index, right_item in enumerate(right, start=1):
            substitution_cost = 0 if left_item == right_item else 1
            current[right_index] = min(
                previous[right_index] + 1,
                current[right_index - 1] + 1,
                previous[right_index - 1] + substitution_cost,
            )
        previous, current = current, previous

    return previous[-1]


def compute_word_error_rate(expected_text: str, actual_text: str) -> float | None:
    expected_tokens = normalize_transcript_for_scoring(expected_text).split()
    actual_tokens = normalize_transcript_for_scoring(actual_text).split()
    if not expected_tokens:
        return None
    return _edit_distance(expected_tokens, actual_tokens) / len(expected_tokens)


def compute_character_error_rate(expected_text: str, actual_text: str) -> float | None:
    expected = normalize_transcript_for_scoring(expected_text)
    actual = normalize_transcript_for_scoring(actual_text)
    if not expected:
        return None
    return _edit_distance(expected, actual) / len(expected)


def compute_transcript_similarity(expected_text: str, actual_text: str) -> float | None:
    expected = normalize_transcript_for_scoring(expected_text)
    actual = normalize_transcript_for_scoring(actual_text)
    if not expected or not actual:
        return None
    return SequenceMatcher(a=expected, b=actual).ratio()


def _canonical_final_transcript(display_events: list[DisplayEvent]) -> tuple[str, int]:
    segments: list[str] = []
    for event in display_events:
        if event.event_type != "stt_final":
            continue
        text = " ".join(str(event.payload.get("text") or "").split())
        if not text:
            continue
        if segments and segments[-1] == text:
            continue
        segments.append(text)
    return " ".join(segments).strip(), len(segments)


def _first_latency_ms(display_events: list[DisplayEvent], *, event_type: str, anchor_ms: int | None) -> int | None:
    if anchor_ms is None:
        return None
    matching = [event for event in display_events if event.event_type == event_type]
    for event in matching:
        event_ms = _event_epoch_ms(event)
        if event_ms is None:
            continue
        return max(event_ms - anchor_ms, 0)
    return None


def _playback_anchor_ms(playback: dict[str, Any] | None) -> int | None:
    if not playback:
        return None
    started_ms = _datetime_to_epoch_ms(_parse_iso_datetime(str(playback.get("started_at") or "")))
    if started_ms is None:
        return None
    return started_ms + int(playback.get("playback_delay_ms") or 0)


@dataclass(frozen=True)
class DerivedRunAnalysis:
    final_transcript: str
    final_segment_count: int
    first_partial_latency_ms: int | None
    first_final_latency_ms: int | None
    wer: float | None
    cer: float | None
    transcript_similarity: float | None
    requested_mic_profile: str | None
    applied_mic_profile: str
    route_uses_built_in_mic: bool
    selected_input_port: str
    selected_data_source: str
    selected_polar_pattern: str
    capture_restart_count_max: int
    route_mismatch_event_count: int
    dfn3_profile: str | None
    dfn3_wet_mix: float | None
    dfn3_loudness_compensation: float | None
    dfn3_post_gain_db: float | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def derive_run_analysis(
    *,
    run_spec: BenchmarkRunSpec,
    display_events: list[DisplayEvent],
    playback: dict[str, Any] | None,
    telemetry_events: list[TelemetryEvent],
) -> DerivedRunAnalysis:
    final_transcript, final_segment_count = _canonical_final_transcript(display_events)
    playback_anchor_ms = _playback_anchor_ms(playback)

    latest_snapshot = telemetry_events[-1].snapshot if telemetry_events else {}
    capture_restart_count_max = max(
        int(event.snapshot.get("captureRestartCount") or 0) for event in telemetry_events
    ) if telemetry_events else 0
    route_mismatch_event_count = sum(
        1 for event in telemetry_events if event.snapshot.get("routeUsesBuiltInMic") is False
    )

    return DerivedRunAnalysis(
        final_transcript=final_transcript,
        final_segment_count=final_segment_count,
        first_partial_latency_ms=_first_latency_ms(display_events, event_type="stt_partial", anchor_ms=playback_anchor_ms),
        first_final_latency_ms=_first_latency_ms(display_events, event_type="stt_final", anchor_ms=playback_anchor_ms),
        wer=compute_word_error_rate(run_spec.expected_transcript, final_transcript),
        cer=compute_character_error_rate(run_spec.expected_transcript, final_transcript),
        transcript_similarity=compute_transcript_similarity(run_spec.expected_transcript, final_transcript),
        requested_mic_profile=run_spec.mic_profile,
        applied_mic_profile=str(latest_snapshot.get("appliedMicProfile") or ""),
        route_uses_built_in_mic=bool(latest_snapshot.get("routeUsesBuiltInMic")),
        selected_input_port=str(latest_snapshot.get("selectedInputPort") or ""),
        selected_data_source=str(latest_snapshot.get("selectedDataSource") or ""),
        selected_polar_pattern=str(latest_snapshot.get("selectedPolarPattern") or ""),
        capture_restart_count_max=capture_restart_count_max,
        route_mismatch_event_count=route_mismatch_event_count,
        dfn3_profile=str(latest_snapshot.get("dfn3Profile") or "") or None,
        dfn3_wet_mix=_coerce_optional_float(latest_snapshot.get("dfn3WetMix")),
        dfn3_loudness_compensation=_coerce_optional_float(latest_snapshot.get("dfn3LoudnessCompensation")),
        dfn3_post_gain_db=_coerce_optional_float(latest_snapshot.get("dfn3PostGainDB")),
    )


def apply_derived_run_analysis(
    *,
    run_result: BenchmarkRunResultMessage,
    analysis: DerivedRunAnalysis,
) -> BenchmarkRunResultMessage:
    run_result.final_transcript = analysis.final_transcript or run_result.final_transcript
    if analysis.first_partial_latency_ms is not None:
        run_result.first_partial_latency_ms = analysis.first_partial_latency_ms
    if analysis.first_final_latency_ms is not None:
        run_result.first_final_latency_ms = analysis.first_final_latency_ms
    if analysis.wer is not None and not math.isnan(analysis.wer):
        run_result.wer = analysis.wer
    if analysis.cer is not None and not math.isnan(analysis.cer):
        run_result.cer = analysis.cer
    return run_result


def _coerce_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
