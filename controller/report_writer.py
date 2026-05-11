from __future__ import annotations

import json
from pathlib import Path

from .models import BenchmarkRunArtifact, BenchmarkSessionPlan, BenchmarkSessionSummary


class BenchmarkReportWriter:
    def __init__(self, reports_root: Path) -> None:
        self._reports_root = reports_root

    def write_session_plan(self, plan: BenchmarkSessionPlan) -> Path:
        self._reports_root.mkdir(parents=True, exist_ok=True)
        target = self._reports_root / "session-plan.json"
        target.write_text(
            json.dumps(plan.as_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return target

    def write_run_artifact(self, artifact: BenchmarkRunArtifact) -> Path:
        run_directory = self._reports_root / artifact.run_spec.run_id
        run_directory.mkdir(parents=True, exist_ok=True)
        target = run_directory / "run.json"
        target.write_text(
            json.dumps(artifact.as_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return target

    def write_session_summary(self, summary: BenchmarkSessionSummary) -> Path:
        self._reports_root.mkdir(parents=True, exist_ok=True)
        target = self._reports_root / "summary.json"
        target.write_text(
            json.dumps(summary.as_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return target
