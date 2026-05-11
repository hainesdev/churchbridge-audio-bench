from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from .control_server import BenchmarkControlServer
from .display_feed_client import DisplayFeedClient
from .models import (
    BenchmarkRunArtifact,
    BenchmarkRunSpec,
    BenchmarkSessionPlan,
    BenchmarkSessionSummary,
)
from .report_writer import BenchmarkReportWriter


def _utc_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _build_run_specs(
    *,
    benchmark_session_id: str,
    scenario_id: str,
    expected_transcript: str,
    variants: list[str],
    run_duration_ms: int,
    save_server_capture: bool,
) -> list[BenchmarkRunSpec]:
    run_specs: list[BenchmarkRunSpec] = []
    for index, pipeline_id in enumerate(variants, start=1):
        run_specs.append(
            BenchmarkRunSpec(
                benchmark_session_id=benchmark_session_id,
                run_id=f"run-{index:02d}-{pipeline_id}",
                scenario_id=scenario_id,
                pipeline_id=pipeline_id,
                expected_transcript=expected_transcript,
                run_duration_ms=run_duration_ms,
                save_server_capture=save_server_capture,
                server_capture_label=f"{scenario_id}-{pipeline_id}",
            )
        )
    return run_specs


async def _run_session(args: argparse.Namespace) -> None:
    session_id = args.session_id or f"session-{_utc_slug()}"
    session_root = Path(args.reports_root) / session_id
    run_specs = _build_run_specs(
        benchmark_session_id=session_id,
        scenario_id=args.scenario_id,
        expected_transcript=args.expected_transcript,
        variants=args.variants,
        run_duration_ms=max(int(args.run_seconds * 1_000), 500),
        save_server_capture=not args.disable_server_capture,
    )
    writer = BenchmarkReportWriter(session_root)
    plan = BenchmarkSessionPlan(
        session_id=session_id,
        scenario_id=args.scenario_id,
        run_specs=run_specs,
    )
    writer.write_session_plan(plan)

    run_outputs: list[dict[str, object]] = []
    control_server = BenchmarkControlServer(host=args.controller_host, port=args.controller_port)
    await control_server.start()
    print(f"Waiting for ChurchBridgeAudioBench device on ws://{args.controller_host}:{args.controller_port}")
    device_hello = await control_server.wait_for_device()
    print(f"Device connected: {device_hello.device_name} ({device_hello.system_version})")

    try:
        for run_spec in run_specs:
            client = DisplayFeedClient(base_url=args.base_url, church_id=args.church_id)
            display_seconds = max(args.display_seconds, run_spec.run_duration_ms / 1_000)
            display_task = asyncio.create_task(client.collect_for_duration(display_seconds))
            run_result, telemetry_events, events = await control_server.run_remote_benchmark(
                run_spec=run_spec,
                display_events_task=display_task,
            )
            artifact = BenchmarkRunArtifact(
                run_spec=run_spec,
                device_hello=device_hello,
                run_result=run_result,
                telemetry_events=telemetry_events,
                display_events=events,
                server_capture_requested=run_spec.save_server_capture,
                server_capture_label=run_spec.server_capture_label,
                notes=[
                    "The controller drove this run through the benchmark control WebSocket.",
                    "Server-side capture was requested so the backend can retain benchmark audio for later offline inspection.",
                ],
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            artifact_path = writer.write_run_artifact(artifact)
            run_outputs.append(
                {
                    "run_id": run_spec.run_id,
                    "pipeline_id": run_spec.pipeline_id,
                    "status": run_result.status,
                    "display_event_count": len(events),
                    "telemetry_event_count": len(telemetry_events),
                    "server_capture_requested": run_spec.save_server_capture,
                    "server_capture_label": run_spec.server_capture_label,
                    "artifact_path": str(artifact_path),
                }
            )
    finally:
        await control_server.stop()

    summary = BenchmarkSessionSummary(
        session_id=session_id,
        scenario_id=args.scenario_id,
        run_count=len(run_specs),
        run_outputs=run_outputs,
        server_capture_requested_run_count=sum(1 for run_spec in run_specs if run_spec.save_server_capture),
        notes=[
            "Session order is intentionally stable so the baseline variant can always run first.",
            "Queued session execution now drives the iPhone benchmark app through the benchmark-owned control WebSocket.",
            "Server-side capture should persist benchmark audio under the supplied run labels in churchbridge-ai for later analysis.",
        ],
    )
    summary_path = writer.write_session_summary(summary)
    print(f"Session plan written for {len(run_specs)} runs.")
    print(f"Summary: {summary_path}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a queued ChurchBridgeAudioBench benchmark session.")
    parser.add_argument("--base-url", required=True, help="Backend base URL, for example http://127.0.0.1:8000")
    parser.add_argument("--church-id", required=True, help="Church identifier for the display feed")
    parser.add_argument("--scenario-id", required=True, help="Scenario identifier used in run artifacts")
    parser.add_argument("--expected-transcript", required=True, help="Reference transcript for all queued runs")
    parser.add_argument("--controller-host", default="0.0.0.0", help="Interface for the benchmark control WebSocket server")
    parser.add_argument("--controller-port", type=int, default=8765, help="Port for the benchmark control WebSocket server")
    parser.add_argument(
        "--variants",
        nargs="+",
        required=True,
        help="Ordered pipeline identifiers such as apple_aec_only apple_aec_plus_current_cleanup raw_debug",
    )
    parser.add_argument("--run-seconds", type=float, default=5.0, help="Requested capture duration per run on the iPhone app")
    parser.add_argument("--display-seconds", type=float, default=5.0, help="Seconds to collect display events per run")
    parser.add_argument("--disable-server-capture", action="store_true", help="Do not request backend audio retention for this session")
    parser.add_argument(
        "--reports-root",
        default=str(Path(__file__).resolve().parents[1] / "reports"),
        help="Directory for session and run artifacts",
    )
    parser.add_argument("--session-id", default="", help="Optional explicit session identifier")
    return parser.parse_args()


def main() -> None:
    asyncio.run(_run_session(_parse_args()))


if __name__ == "__main__":
    main()
