from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_model() -> str:
    return os.getenv("GOOGLE_SPEECH_MODEL", "chirp_3").strip() or "chirp_3"


def _default_location() -> str:
    return os.getenv("GOOGLE_CLOUD_LOCATION", "us").strip() or "us"


def _default_recognizer() -> str:
    return os.getenv("GOOGLE_SPEECH_RECOGNIZER", "_").strip() or "_"


def _dedupe_language_codes(codes: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for code in codes:
        cleaned = str(code).strip()
        if not cleaned:
            continue
        normalized = cleaned.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(cleaned)
    return tuple(ordered)


def _default_language_codes() -> tuple[str, ...]:
    raw_codes = os.getenv("GOOGLE_SPEECH_LANGUAGE_CODES", "").strip()
    if raw_codes:
        return _dedupe_language_codes(raw_codes.split(","))

    primary = os.getenv("GOOGLE_SPEECH_LANGUAGE", "es-US").strip() or "es-US"
    secondary = os.getenv("GOOGLE_SPEECH_SECONDARY_LANGUAGE", "").strip()
    if secondary:
        return _dedupe_language_codes((primary, secondary))
    if primary.lower().startswith("es"):
        return _dedupe_language_codes((primary, "en-US"))
    if primary.lower().startswith("en"):
        return _dedupe_language_codes((primary, "es-US"))
    return (primary,)


def _parse_language_codes(payload: dict[str, Any]) -> tuple[str, ...]:
    raw_codes = payload.get("languageCodes")
    if isinstance(raw_codes, (list, tuple)):
        codes = _dedupe_language_codes(raw_codes)
        if codes:
            return codes

    legacy_language = str(payload.get("language") or "").strip()
    if legacy_language:
        return _dedupe_language_codes(legacy_language.split(","))

    return _default_language_codes()


@dataclass(frozen=True)
class BenchmarkSTTConfig:
    """Benchmark-owned STT config modeled after the existing server payload shape."""

    model: str = _default_model()
    language_codes: tuple[str, ...] = field(default_factory=_default_language_codes)
    interim_results: bool = True
    utterance_end_ms: int = 2000
    vad_events: bool = True
    smart_format: bool = True
    punctuate: bool = True
    confidence_hold_threshold: float = 0.72
    low_confidence_hold_secs: float = 2.5
    location: str = _default_location()
    recognizer: str = _default_recognizer()
    diarization_enabled: bool = False
    diarization_min_speakers: int = 2
    diarization_max_speakers: int = 2

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "BenchmarkSTTConfig":
        payload = payload or {}
        location = str(payload.get("location") or "").strip() or _default_location()
        recognizer = str(payload.get("recognizer") or "").strip() or _default_recognizer()
        return cls(
            model=str(payload.get("model") or "").strip() or _default_model(),
            language_codes=_parse_language_codes(payload),
            interim_results=bool(payload.get("interimResults", cls.interim_results)),
            utterance_end_ms=max(500, int(payload.get("utteranceEndMs", cls.utterance_end_ms))),
            vad_events=bool(payload.get("vadEvents", cls.vad_events)),
            smart_format=bool(payload.get("smartFormat", cls.smart_format)),
            punctuate=bool(payload.get("punctuate", cls.punctuate)),
            confidence_hold_threshold=float(payload.get("confidenceHoldThreshold", cls.confidence_hold_threshold)),
            low_confidence_hold_secs=float(payload.get("lowConfidenceHoldSecs", cls.low_confidence_hold_secs)),
            location=location,
            recognizer=recognizer,
            diarization_enabled=bool(payload.get("diarizationEnabled", cls.diarization_enabled)),
            diarization_min_speakers=max(1, int(payload.get("diarizationMinSpeakers", cls.diarization_min_speakers))),
            diarization_max_speakers=max(1, int(payload.get("diarizationMaxSpeakers", cls.diarization_max_speakers))),
        )

    def public_payload(self) -> dict[str, Any]:
        primary_language = self.language_codes[0] if self.language_codes else ""
        return {
            "model": self.model,
            "language": primary_language,
            "languageCodes": list(self.language_codes),
            "location": self.location,
            "recognizer": self.recognizer,
            "interimResults": self.interim_results,
            "utteranceEndMs": self.utterance_end_ms,
            "vadEvents": self.vad_events,
            "smartFormat": self.smart_format,
            "punctuate": self.punctuate,
            "confidenceHoldThreshold": self.confidence_hold_threshold,
            "lowConfidenceHoldSecs": self.low_confidence_hold_secs,
            "diarizationEnabled": self.diarization_enabled,
            "diarizationMinSpeakers": self.diarization_min_speakers,
            "diarizationMaxSpeakers": self.diarization_max_speakers,
        }


@dataclass(frozen=True)
class BenchmarkRunSpec:
    benchmark_session_id: str
    run_id: str
    scenario_id: str
    pipeline_id: str
    expected_transcript: str
    stt_sample_rate: int = 16_000
    chunk_duration_ms: int = 100
    run_duration_ms: int = 5_000
    save_server_capture: bool = True
    server_capture_label: str | None = None
    controller_started_at: str = field(default_factory=_utc_now_iso)
    stt_config: BenchmarkSTTConfig = field(default_factory=BenchmarkSTTConfig)

    def controller_payload(self) -> dict[str, Any]:
        return {
            "type": "run_spec",
            "benchmark_session_id": self.benchmark_session_id,
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "pipeline_id": self.pipeline_id,
            "expected_transcript": self.expected_transcript,
            "stt_sample_rate": self.stt_sample_rate,
            "chunk_duration_ms": self.chunk_duration_ms,
            "run_duration_ms": self.run_duration_ms,
            "save_server_capture": self.save_server_capture,
            "server_capture_label": self.server_capture_label,
            "controller_started_at": self.controller_started_at,
            "stt_config": self.stt_config.public_payload(),
        }


@dataclass
class DeviceHello:
    protocol_version: int
    device_name: str
    system_version: str
    app_version: str
    received_at: str = field(default_factory=_utc_now_iso)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "DeviceHello":
        return cls(
            protocol_version=int(payload.get("protocol_version") or 0),
            device_name=str(payload.get("device_name") or "Unknown device"),
            system_version=str(payload.get("system_version") or "Unknown system"),
            app_version=str(payload.get("app_version") or "Unknown version"),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TelemetryEvent:
    run_id: str
    snapshot: dict[str, Any]
    received_at: str = field(default_factory=_utc_now_iso)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TelemetryEvent":
        return cls(
            run_id=str(payload.get("run_id") or ""),
            snapshot=dict(payload.get("snapshot") or {}),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkRunResultMessage:
    run_id: str
    pipeline_id: str
    status: str
    final_transcript: str
    warnings: list[str]
    errors: list[str]
    first_partial_latency_ms: int | None = None
    first_final_latency_ms: int | None = None
    wer: float | None = None
    cer: float | None = None
    received_at: str = field(default_factory=_utc_now_iso)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "BenchmarkRunResultMessage":
        return cls(
            run_id=str(payload.get("run_id") or ""),
            pipeline_id=str(payload.get("pipeline_id") or ""),
            status=str(payload.get("status") or "unknown"),
            final_transcript=str(payload.get("final_transcript") or ""),
            warnings=[str(item) for item in payload.get("warnings") or []],
            errors=[str(item) for item in payload.get("errors") or []],
            first_partial_latency_ms=payload.get("first_partial_latency_ms"),
            first_final_latency_ms=payload.get("first_final_latency_ms"),
            wer=payload.get("wer"),
            cer=payload.get("cer"),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DisplayEvent:
    event_type: str
    payload: dict[str, Any]
    received_at: str = field(default_factory=_utc_now_iso)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "DisplayEvent":
        return cls(
            event_type=str(payload.get("type") or "unknown"),
            payload=dict(payload),
        )

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = data.pop("event_type")
        return data


@dataclass
class BenchmarkRunArtifact:
    run_spec: BenchmarkRunSpec
    device_hello: DeviceHello | None = None
    run_result: BenchmarkRunResultMessage | None = None
    telemetry_events: list[TelemetryEvent] = field(default_factory=list)
    display_events: list[DisplayEvent] = field(default_factory=list)
    server_capture_requested: bool = False
    server_capture_label: str | None = None
    notes: list[str] = field(default_factory=list)
    completed_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_spec": self.run_spec.controller_payload(),
            "device_hello": self.device_hello.as_dict() if self.device_hello else None,
            "run_result": self.run_result.as_dict() if self.run_result else None,
            "telemetry_events": [event.as_dict() for event in self.telemetry_events],
            "display_events": [event.as_dict() for event in self.display_events],
            "server_capture_requested": self.server_capture_requested,
            "server_capture_label": self.server_capture_label,
            "notes": list(self.notes),
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True)
class BenchmarkSessionPlan:
    session_id: str
    scenario_id: str
    run_specs: list[BenchmarkRunSpec]
    created_at: str = field(default_factory=_utc_now_iso)

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "scenario_id": self.scenario_id,
            "created_at": self.created_at,
            "run_specs": [run_spec.controller_payload() for run_spec in self.run_specs],
        }


@dataclass
class BenchmarkSessionSummary:
    session_id: str
    scenario_id: str
    run_count: int
    run_outputs: list[dict[str, Any]]
    server_capture_requested_run_count: int = 0
    notes: list[str] = field(default_factory=list)
    completed_at: str = field(default_factory=_utc_now_iso)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
