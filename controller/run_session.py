from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .audio_helpers import schedule_audio_playback
from .control_server import BenchmarkControlServer
from .display_feed_client import DisplayFeedClient
from .models import (
    BenchmarkRunArtifact,
    BenchmarkRunSpec,
    BenchmarkSessionPlan,
    BenchmarkSessionSummary,
)
from .report_writer import BenchmarkReportWriter
from .scenarios import BenchmarkScenarioFixture, load_scenarios_from_manifests
from .playback_degradations import build_degradation_spec

DEFAULT_PIPELINE_VARIANTS = [
    "apple_aec_only",
    "apple_aec_plus_current_cleanup",
    "raw_debug",
    "deepfilternet3_only",
    "apple_aec_plus_deepfilternet3",
]


def _utc_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in str(value or "").strip())
    cleaned = cleaned.strip("-_.")
    return cleaned or "scenario"


@dataclass(frozen=True)
class PreparedBenchmarkRun:
    run_spec: BenchmarkRunSpec
    scenario: BenchmarkScenarioFixture
    display_seconds: float


def _build_prepared_runs(
    *,
    benchmark_session_id: str,
    scenarios: list[BenchmarkScenarioFixture],
    variants: list[str],
    default_run_seconds: float,
    default_display_seconds: float,
    save_server_capture: bool,
) -> list[PreparedBenchmarkRun]:
    prepared_runs: list[PreparedBenchmarkRun] = []
    run_index = 1
    for scenario in scenarios:
        run_seconds = scenario.computed_run_seconds(minimum_seconds=default_run_seconds)
        display_seconds = scenario.computed_display_seconds(minimum_seconds=max(default_display_seconds, run_seconds))
        for pipeline_id in variants:
            prepared_runs.append(
                PreparedBenchmarkRun(
                    run_spec=BenchmarkRunSpec(
                        benchmark_session_id=benchmark_session_id,
                        run_id=f"run-{run_index:02d}-{_safe_slug(scenario.scenario_id)}-{pipeline_id}",
                        scenario_id=scenario.scenario_id,
                        pipeline_id=pipeline_id,
                        expected_transcript=scenario.expected_transcript,
                        run_duration_ms=max(int(run_seconds * 1_000), 500),
                        save_server_capture=save_server_capture,
                        server_capture_label=f"{scenario.scenario_id}-{pipeline_id}",
                    ),
                    scenario=scenario,
                    display_seconds=display_seconds,
                )
            )
            run_index += 1
    return prepared_runs


def _load_scenarios(args: argparse.Namespace, *, repo_root: Path) -> list[BenchmarkScenarioFixture]:
    if args.scenario_file:
        scenarios = load_scenarios_from_manifests(args.scenario_file, repo_root=repo_root)
    else:
        if not args.scenario_id or not args.expected_transcript:
            raise ValueError("Either --scenario-file or both --scenario-id and --expected-transcript are required.")

        audio_path = None
        if args.audio_path:
            candidate = Path(args.audio_path).expanduser()
            audio_path = candidate.resolve() if candidate.is_absolute() else (repo_root / candidate).resolve()
            if not audio_path.exists():
                raise FileNotFoundError(f"Audio file does not exist: {audio_path}")

        scenarios = [
            BenchmarkScenarioFixture(
                scenario_id=args.scenario_id,
                expected_transcript=args.expected_transcript,
                audio_path=audio_path,
                playback_lead_in_seconds=max(args.playback_lead_in_seconds, 0.0),
                playback_tail_seconds=max(args.playback_tail_seconds, 0.0),
            )
        ]

    degradation_override = _build_degradation_override(args)
    if degradation_override is not None:
        scenarios = [scenario.with_degradation(degradation_override) for scenario in scenarios]
    return scenarios


def _build_degradation_override(args: argparse.Namespace):
    if not args.degradation_condition:
        return None
    return build_degradation_spec(
        args.degradation_condition,
        echo_profile=args.echo_profile,
        noise_type=args.noise_type,
        snr_db=args.noise_snr_db,
        seed=args.degradation_seed,
    )


async def _run_session(args: argparse.Namespace) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    session_id = args.session_id or f"session-{_utc_slug()}"
    session_root = Path(args.reports_root) / session_id
    scenarios = _load_scenarios(args, repo_root=repo_root)
    prepared_runs = _build_prepared_runs(
        benchmark_session_id=session_id,
        scenarios=scenarios,
        variants=args.variants,
        default_run_seconds=max(args.run_seconds, 0.5),
        default_display_seconds=max(args.display_seconds, 0.5),
        save_server_capture=not args.disable_server_capture,
    )
    writer = BenchmarkReportWriter(session_root)
    session_scenario_id = scenarios[0].scenario_id if len({scenario.scenario_id for scenario in scenarios}) == 1 else "multi-scenario"
    plan = BenchmarkSessionPlan(
        session_id=session_id,
        scenario_id=session_scenario_id,
        run_specs=[prepared_run.run_spec for prepared_run in prepared_runs],
    )
    writer.write_session_plan(plan)

    run_outputs: list[dict[str, object]] = []
    control_server = BenchmarkControlServer(host=args.controller_host, port=args.controller_port)
    await control_server.start()
    print(f"Waiting for ChurchBridgeAudioBench device on ws://{args.controller_host}:{args.controller_port}")
    device_hello = await control_server.wait_for_device()
    print(f"Device connected: {device_hello.device_name} ({device_hello.system_version})")

    try:
        for prepared_run in prepared_runs:
            run_spec = prepared_run.run_spec
            scenario = prepared_run.scenario
            client = DisplayFeedClient(base_url=args.base_url, church_id=args.church_id)
            display_task = asyncio.create_task(client.collect_for_duration(prepared_run.display_seconds))
            try:
                run_result, telemetry_events, events, playback_result = await control_server.run_remote_benchmark(
                    run_spec=run_spec,
                    display_events_task=display_task,
                    playback_coordinator=(
                        (lambda scenario=scenario: schedule_audio_playback(
                            scenario.audio_path,
                            playback_delay_seconds=scenario.playback_lead_in_seconds,
                            playback_start_seconds=scenario.playback_start_seconds,
                            playback_duration_seconds=scenario.playback_duration_seconds,
                            degradation=scenario.degradation,
                        ))
                        if scenario.audio_path is not None
                        else None
                    ),
                )
            finally:
                if not display_task.done():
                    display_task.cancel()
                    try:
                        await display_task
                    except asyncio.CancelledError:
                        pass

            notes = [
                "The controller drove this run through the benchmark control WebSocket.",
                "Server-side capture was requested so the backend can retain benchmark audio for later offline inspection.",
            ]
            if scenario.audio_path is not None:
                window_note = ""
                if scenario.playback_start_seconds > 0 or scenario.playback_duration_seconds is not None:
                    window_note = (
                        f" Window: start={scenario.playback_start_seconds:.3f}s"
                        f", duration={scenario.playback_duration_seconds if scenario.playback_duration_seconds is not None else 'full'}s."
                    )
                degradation_note = ""
                if scenario.degradation is not None:
                    degradation_note = f" Degradation: {scenario.degradation.metadata()}."
                notes.append(f"Local controller playback was scheduled from {scenario.audio_path}.{window_note}{degradation_note}")
            notes.extend(scenario.notes)
            artifact = BenchmarkRunArtifact(
                run_spec=run_spec,
                device_hello=device_hello,
                run_result=run_result,
                telemetry_events=telemetry_events,
                display_events=events,
                playback=playback_result.as_dict() if playback_result else None,
                server_capture_requested=run_spec.save_server_capture,
                server_capture_label=run_spec.server_capture_label,
                notes=notes,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            artifact_path = writer.write_run_artifact(artifact)
            run_outputs.append(
                {
                    "run_id": run_spec.run_id,
                    "scenario_id": run_spec.scenario_id,
                    "pipeline_id": run_spec.pipeline_id,
                    "status": run_result.status,
                    "display_event_count": len(events),
                    "telemetry_event_count": len(telemetry_events),
                    "server_capture_requested": run_spec.save_server_capture,
                    "server_capture_label": run_spec.server_capture_label,
                    "playback_audio_path": str(scenario.audio_path) if scenario.audio_path is not None else None,
                    "playback_degradation": scenario.degradation.metadata() if scenario.degradation is not None else None,
                    "playback_start_seconds": scenario.playback_start_seconds,
                    "playback_duration_seconds": scenario.playback_duration_seconds,
                    "playback_player": playback_result.player if playback_result else None,
                    "playback_rendered_audio_path": playback_result.audio_path if playback_result else None,
                    "artifact_path": str(artifact_path),
                }
            )
    finally:
        await control_server.stop()

    summary = BenchmarkSessionSummary(
        session_id=session_id,
        scenario_id=session_scenario_id,
        run_count=len(prepared_runs),
        run_outputs=run_outputs,
        server_capture_requested_run_count=sum(1 for prepared_run in prepared_runs if prepared_run.run_spec.save_server_capture),
        notes=[
            "Session order is intentionally stable so the baseline variant can always run first.",
            "Queued session execution now drives the iPhone benchmark app through the benchmark-owned control WebSocket.",
            "Server-side capture should persist benchmark audio under the supplied run labels in churchbridge-ai for later analysis.",
            "When scenario audio is configured, the controller now schedules local playback automatically with a lead-in so the iPhone can begin capture before room audio starts.",
        ],
    )
    summary_path = writer.write_session_summary(summary)
    print(f"Session plan written for {len(prepared_runs)} runs.")
    print(f"Summary: {summary_path}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a queued ChurchBridgeAudioBench benchmark session.")
    parser.add_argument("--base-url", required=True, help="Backend base URL, for example http://127.0.0.1:8000")
    parser.add_argument("--church-id", required=True, help="Church identifier for the display feed")
    parser.add_argument("--scenario-id", default="", help="Scenario identifier used in run artifacts when not loading a scenario manifest")
    parser.add_argument("--expected-transcript", default="", help="Reference transcript for the run when not loading a scenario manifest")
    parser.add_argument("--controller-host", default="0.0.0.0", help="Interface for the benchmark control WebSocket server")
    parser.add_argument("--controller-port", type=int, default=8765, help="Port for the benchmark control WebSocket server")
    parser.add_argument(
        "--variants",
        nargs="+",
        default=DEFAULT_PIPELINE_VARIANTS,
        help=(
            "Ordered pipeline identifiers. Defaults to the standard benchmark matrix: "
            "apple_aec_only apple_aec_plus_current_cleanup raw_debug deepfilternet3_only apple_aec_plus_deepfilternet3"
        ),
    )
    parser.add_argument("--run-seconds", type=float, default=5.0, help="Requested capture duration per run on the iPhone app")
    parser.add_argument("--display-seconds", type=float, default=5.0, help="Seconds to collect display events per run")
    parser.add_argument("--audio-path", default="", help="Optional local audio file to play automatically for a single-scenario run")
    parser.add_argument(
        "--scenario-file",
        action="append",
        default=[],
        help="Scenario manifest JSON. Can be provided multiple times; each file can define one scenario or a {\"scenarios\": [...]} list.",
    )
    parser.add_argument(
        "--playback-lead-in-seconds",
        type=float,
        default=1.25,
        help="Delay between telling the iPhone to start capture and beginning local playback for one-off --audio-path runs.",
    )
    parser.add_argument(
        "--playback-tail-seconds",
        type=float,
        default=1.0,
        help="Extra capture time to preserve after the audio file ends when duration is inferred from the media file.",
    )
    parser.add_argument(
        "--degradation-condition",
        choices=["clean", "echo", "noise", "echo_noise"],
        default="",
        help="Optional controller-side playback degradation to apply to every scenario in this session.",
    )
    parser.add_argument(
        "--echo-profile",
        choices=["light", "medium", "heavy"],
        default="",
        help="Echo profile for --degradation-condition echo or echo_noise.",
    )
    parser.add_argument(
        "--noise-type",
        choices=["white", "pink", "hvac", "crowd", "street", "babble"],
        default="",
        help="Background-noise type for --degradation-condition noise or echo_noise.",
    )
    parser.add_argument(
        "--noise-snr-db",
        type=float,
        default=None,
        help="Target signal-to-noise ratio in dB for --degradation-condition noise or echo_noise.",
    )
    parser.add_argument(
        "--degradation-seed",
        type=int,
        default=7,
        help="Random seed for controller-side playback degradation.",
    )
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
