from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .audio_helpers import probe_audio_duration_seconds
from .playback_degradations import PlaybackDegradationSpec, parse_degradation_from_payload


def _coerce_path(value: str, *, base_directory: Path, repo_root: Path) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(value)))
    if expanded.is_absolute():
        return expanded.resolve()

    manifest_relative = (base_directory / expanded).resolve()
    if manifest_relative.exists():
        return manifest_relative
    return (repo_root / expanded).resolve()


@dataclass(frozen=True)
class BenchmarkScenarioFixture:
    scenario_id: str
    expected_transcript: str
    audio_path: Path | None = None
    run_seconds: float | None = None
    display_seconds: float | None = None
    playback_start_seconds: float = 0.0
    playback_duration_seconds: float | None = None
    playback_lead_in_seconds: float = 1.25
    playback_tail_seconds: float = 1.0
    degradation: PlaybackDegradationSpec | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    manifest_path: Path | None = None

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        manifest_path: Path,
        repo_root: Path,
    ) -> "BenchmarkScenarioFixture":
        scenario_id = str(payload.get("scenario_id") or payload.get("scenarioId") or "").strip()
        if not scenario_id:
            raise ValueError(f"Scenario manifest {manifest_path} is missing scenario_id.")

        expected_transcript = str(payload.get("expected_transcript") or payload.get("expectedTranscript") or "").strip()
        if not expected_transcript:
            raise ValueError(f"Scenario manifest {manifest_path} is missing expected_transcript for {scenario_id}.")

        raw_audio_path = str(payload.get("audio_path") or payload.get("audioPath") or "").strip()
        audio_path = _coerce_path(raw_audio_path, base_directory=manifest_path.parent, repo_root=repo_root) if raw_audio_path else None
        if audio_path and not audio_path.exists():
            raise FileNotFoundError(f"Scenario audio file does not exist: {audio_path}")

        if payload.get("playback_start_seconds") is not None:
            playback_start_seconds = float(payload["playback_start_seconds"])
        elif payload.get("playbackStartSeconds") is not None:
            playback_start_seconds = float(payload["playbackStartSeconds"])
        elif payload.get("playback_start_ms") is not None:
            playback_start_seconds = float(payload["playback_start_ms"]) / 1000.0
        else:
            playback_start_seconds = 0.0

        if payload.get("playback_duration_seconds") is not None:
            playback_duration_seconds = float(payload["playback_duration_seconds"])
        elif payload.get("playbackDurationSeconds") is not None:
            playback_duration_seconds = float(payload["playbackDurationSeconds"])
        elif payload.get("playback_duration_ms") is not None:
            playback_duration_seconds = float(payload["playback_duration_ms"]) / 1000.0
        else:
            playback_duration_seconds = None

        if payload.get("playback_lead_in_seconds") is not None:
            playback_lead_in_seconds = float(payload["playback_lead_in_seconds"])
        elif payload.get("playbackLeadInSeconds") is not None:
            playback_lead_in_seconds = float(payload["playbackLeadInSeconds"])
        elif payload.get("playback_lead_in_ms") is not None:
            playback_lead_in_seconds = float(payload["playback_lead_in_ms"]) / 1000.0
        else:
            playback_lead_in_seconds = 1.25

        if payload.get("playback_tail_seconds") is not None:
            playback_tail_seconds = float(payload["playback_tail_seconds"])
        elif payload.get("playbackTailSeconds") is not None:
            playback_tail_seconds = float(payload["playbackTailSeconds"])
        elif payload.get("playback_tail_ms") is not None:
            playback_tail_seconds = float(payload["playback_tail_ms"]) / 1000.0
        else:
            playback_tail_seconds = 1.0

        notes = payload.get("notes") or []
        if isinstance(notes, str):
            notes = [notes]
        degradation = parse_degradation_from_payload(payload)

        return cls(
            scenario_id=scenario_id,
            expected_transcript=expected_transcript,
            audio_path=audio_path,
            run_seconds=float(payload["run_seconds"]) if payload.get("run_seconds") is not None else (
                float(payload["runSeconds"]) if payload.get("runSeconds") is not None else None
            ),
            display_seconds=float(payload["display_seconds"]) if payload.get("display_seconds") is not None else (
                float(payload["displaySeconds"]) if payload.get("displaySeconds") is not None else None
            ),
            playback_start_seconds=max(playback_start_seconds, 0.0),
            playback_duration_seconds=max(playback_duration_seconds, 0.0) if playback_duration_seconds is not None else None,
            playback_lead_in_seconds=max(playback_lead_in_seconds, 0.0),
            playback_tail_seconds=max(playback_tail_seconds, 0.0),
            degradation=degradation,
            notes=tuple(str(note) for note in notes),
            manifest_path=manifest_path,
        )

    def with_degradation(self, degradation: PlaybackDegradationSpec | None) -> "BenchmarkScenarioFixture":
        return replace(self, degradation=degradation)

    def computed_run_seconds(self, *, minimum_seconds: float) -> float:
        seconds = max(float(self.run_seconds or 0), float(minimum_seconds))
        if self.playback_duration_seconds is not None:
            seconds = max(
                seconds,
                self.playback_duration_seconds + self.playback_lead_in_seconds + self.playback_tail_seconds,
            )
        elif self.audio_path is not None:
            audio_duration = probe_audio_duration_seconds(self.audio_path)
            if audio_duration is not None:
                available_duration = max(audio_duration - self.playback_start_seconds, 0.0)
                seconds = max(
                    seconds,
                    available_duration + self.playback_lead_in_seconds + self.playback_tail_seconds,
                )
        return seconds

    def computed_display_seconds(self, *, minimum_seconds: float) -> float:
        seconds = max(float(self.display_seconds or 0), float(minimum_seconds))
        return max(seconds, self.computed_run_seconds(minimum_seconds=minimum_seconds) + 2.0)


def load_scenarios_from_manifests(manifest_paths: list[str], *, repo_root: Path) -> list[BenchmarkScenarioFixture]:
    scenarios: list[BenchmarkScenarioFixture] = []
    for manifest_text in manifest_paths:
        manifest_path = Path(os.path.expandvars(os.path.expanduser(manifest_text))).resolve()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw_items = payload.get("scenarios") if isinstance(payload, dict) and isinstance(payload.get("scenarios"), list) else [payload]
        for item in raw_items:
            if not isinstance(item, dict):
                raise ValueError(f"Scenario manifest {manifest_path} must contain objects, not {type(item).__name__}.")
            scenarios.append(
                BenchmarkScenarioFixture.from_payload(
                    item,
                    manifest_path=manifest_path,
                    repo_root=repo_root,
                )
            )
    if not scenarios:
        raise ValueError("No scenarios were loaded from the provided manifest paths.")
    return scenarios
