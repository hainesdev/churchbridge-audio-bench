from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .alignment import SyncMarkerSpec
from .analysis import apply_derived_run_analysis, derive_run_analysis
from .audio_helpers import schedule_audio_playback
from .control_server import BenchmarkControlServer
from .display_feed_client import DisplayFeedClient
from .models import (
    BenchmarkDFN3TuningConfig,
    BenchmarkRunArtifact,
    BenchmarkRunSpec,
    BenchmarkSessionPlan,
    BenchmarkSessionSummary,
    BenchmarkSTTConfig,
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

DEFAULT_MIC_PROFILES = ["auto"]
DEFAULT_DFN3_PROFILES = ["subtle"]
DEFAULT_STT_MODELS = [BenchmarkSTTConfig().model]


def _utc_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_slug(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in str(value or "").strip())
    cleaned = cleaned.strip("-_.")
    return cleaned or "scenario"


def _coerce_path(path_text: str, *, repo_root: Path) -> Path:
    candidate = Path(path_text).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (repo_root / candidate).resolve()


def _optional_float(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value is None or value == "":
            continue
        return float(value)
    return None


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
    stt_configs: list[BenchmarkSTTConfig],
    mic_profiles: list[str],
    dfn3_tunings: list[BenchmarkDFN3TuningConfig],
    annotate_stt_model: bool,
    annotate_mic_profile: bool,
    annotate_dfn3_tuning: bool,
    default_run_seconds: float,
    default_display_seconds: float,
    save_server_capture: bool,
    marker_lead_seconds: float = 0.0,
) -> list[PreparedBenchmarkRun]:
    prepared_runs: list[PreparedBenchmarkRun] = []
    run_index = 1
    for scenario in scenarios:
        run_seconds = scenario.computed_run_seconds(
            minimum_seconds=default_run_seconds, extra_lead_seconds=marker_lead_seconds
        )
        display_seconds = scenario.computed_display_seconds(
            minimum_seconds=max(default_display_seconds, run_seconds),
            extra_lead_seconds=marker_lead_seconds,
        )
        for pipeline_id in variants:
            pipeline_dfn3_tunings = dfn3_tunings if _pipeline_supports_dfn3(pipeline_id) else [None]
            for stt_config in stt_configs:
                for mic_profile in mic_profiles:
                    for dfn3_tuning in pipeline_dfn3_tunings:
                        run_slug_parts = [scenario.scenario_id, pipeline_id]
                        if annotate_stt_model:
                            run_slug_parts.append(f"stt-{stt_config.model}")
                        if annotate_mic_profile:
                            run_slug_parts.append(mic_profile)
                        if annotate_dfn3_tuning and dfn3_tuning is not None:
                            run_slug_parts.append(f"dfn3-{dfn3_tuning.slug()}")

                        run_slug = "-".join(_safe_slug(part) for part in run_slug_parts)
                        prepared_runs.append(
                            PreparedBenchmarkRun(
                                run_spec=BenchmarkRunSpec(
                                    benchmark_session_id=benchmark_session_id,
                                    run_id=f"run-{run_index:02d}-{run_slug}",
                                    scenario_id=scenario.scenario_id,
                                    pipeline_id=pipeline_id,
                                    expected_transcript=scenario.expected_transcript,
                                    run_duration_ms=max(int(run_seconds * 1_000), 500),
                                    save_server_capture=save_server_capture,
                                    server_capture_label=run_slug,
                                    stt_config=stt_config,
                                    mic_profile=mic_profile,
                                    dfn3_tuning=dfn3_tuning,
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


def _resolved_mic_profiles(args: argparse.Namespace) -> list[str]:
    profiles = [str(profile).strip() for profile in (args.mic_profiles or []) if str(profile).strip()]
    return profiles or list(DEFAULT_MIC_PROFILES)


def _build_dfn3_tuning_from_payload(payload: dict[str, Any], *, source: Path | None = None, index: int | None = None) -> BenchmarkDFN3TuningConfig:
    profile = str(payload.get("profile") or "subtle").strip() or "subtle"
    label = str(payload.get("label") or payload.get("name") or "").strip() or None
    if source is not None and not profile:
        location = f"{source}"
        if index is not None:
            location = f"{location} entry {index}"
        raise ValueError(f"DFN3 tuning manifest {location} is missing a profile.")

    return BenchmarkDFN3TuningConfig(
        profile=profile,
        label=label,
        wet_mix=_optional_float(payload, "wet_mix", "wetMix"),
        loudness_compensation=_optional_float(payload, "loudness_compensation", "loudnessCompensation"),
        max_compensation_gain=_optional_float(payload, "max_compensation_gain", "maxCompensationGain"),
        post_gain_db=_optional_float(payload, "post_gain_db", "postGainDB"),
        peak_limit=_optional_float(payload, "peak_limit", "peakLimit"),
    )


def _apply_cli_dfn3_overrides(tuning: BenchmarkDFN3TuningConfig, args: argparse.Namespace) -> BenchmarkDFN3TuningConfig:
    return BenchmarkDFN3TuningConfig(
        profile=tuning.profile,
        label=tuning.label,
        wet_mix=args.dfn3_wet_mix if args.dfn3_wet_mix is not None else tuning.wet_mix,
        loudness_compensation=(
            args.dfn3_loudness_compensation
            if args.dfn3_loudness_compensation is not None
            else tuning.loudness_compensation
        ),
        max_compensation_gain=(
            args.dfn3_max_compensation_gain
            if args.dfn3_max_compensation_gain is not None
            else tuning.max_compensation_gain
        ),
        post_gain_db=args.dfn3_post_gain_db if args.dfn3_post_gain_db is not None else tuning.post_gain_db,
        peak_limit=args.dfn3_peak_limit if args.dfn3_peak_limit is not None else tuning.peak_limit,
    )


def _load_dfn3_tunings_from_manifest(manifest_text: str, *, repo_root: Path) -> list[BenchmarkDFN3TuningConfig]:
    manifest_path = _coerce_path(manifest_text, repo_root=repo_root)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_items = payload.get("tunings") if isinstance(payload, dict) and isinstance(payload.get("tunings"), list) else payload
    if not isinstance(raw_items, list):
        raise ValueError(f"DFN3 tuning manifest {manifest_path} must be a list or an object with a tunings array.")

    tunings: list[BenchmarkDFN3TuningConfig] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            raise ValueError(
                f"DFN3 tuning manifest {manifest_path} entry {index} must be an object, not {type(item).__name__}."
            )
        tunings.append(_build_dfn3_tuning_from_payload(item, source=manifest_path, index=index))
    if not tunings:
        raise ValueError(f"DFN3 tuning manifest {manifest_path} did not define any tunings.")
    return tunings


def _resolved_dfn3_tunings(args: argparse.Namespace, *, repo_root: Path) -> list[BenchmarkDFN3TuningConfig]:
    if args.dfn3_tuning_file:
        tunings: list[BenchmarkDFN3TuningConfig] = []
        for manifest_text in args.dfn3_tuning_file:
            tunings.extend(_load_dfn3_tunings_from_manifest(manifest_text, repo_root=repo_root))
        return [_apply_cli_dfn3_overrides(tuning, args) for tuning in tunings]

    profiles = [str(profile).strip() for profile in (args.dfn3_profiles or []) if str(profile).strip()]
    profiles = profiles or list(DEFAULT_DFN3_PROFILES)
    return [
        BenchmarkDFN3TuningConfig(
            profile=profile,
            wet_mix=args.dfn3_wet_mix,
            loudness_compensation=args.dfn3_loudness_compensation,
            max_compensation_gain=args.dfn3_max_compensation_gain,
            post_gain_db=args.dfn3_post_gain_db,
            peak_limit=args.dfn3_peak_limit,
        )
        for profile in profiles
    ]


def _resolved_variants(args: argparse.Namespace) -> list[str]:
    variants = [str(variant).strip() for variant in (args.variants or []) if str(variant).strip()]
    variants = variants or list(DEFAULT_PIPELINE_VARIANTS)
    if not args.include_raw_compare:
        return variants

    ordered = ["raw_debug"]
    ordered.extend(variant for variant in variants if variant != "raw_debug")
    return ordered


def _resolved_stt_configs(args: argparse.Namespace) -> list[BenchmarkSTTConfig]:
    models = [str(model).strip() for model in (args.stt_models or []) if str(model).strip()]
    models = models or list(DEFAULT_STT_MODELS)
    return [
        BenchmarkSTTConfig.from_payload(
            {
                "model": model,
            }
        )
        for model in models
    ]


def _pipeline_supports_dfn3(pipeline_id: str) -> bool:
    return "deepfilternet3" in str(pipeline_id or "").lower()


def _build_environment_metadata(args: argparse.Namespace) -> dict[str, object] | None:
    label = str(args.environment_label or "").strip()
    notes = [str(note).strip() for note in (args.environment_note or []) if str(note).strip()]
    if not label and not notes:
        return None

    return {
        "label": label or None,
        "notes": notes,
        "uses_controller_degradation": bool(args.degradation_condition),
    }


async def _run_session(args: argparse.Namespace) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    session_id = args.session_id or f"session-{_utc_slug()}"
    session_root = Path(args.reports_root) / session_id
    scenarios = _load_scenarios(args, repo_root=repo_root)
    environment = _build_environment_metadata(args)
    variants = _resolved_variants(args)
    stt_configs = _resolved_stt_configs(args)
    mic_profiles = _resolved_mic_profiles(args)
    dfn3_tunings = _resolved_dfn3_tunings(args, repo_root=repo_root)

    # The marker is prepended to whatever is played, so each capture carries the
    # offset that aligns it to its reference transcript. Without it, playback and
    # capture latency is scored as words missed or invented. It lengthens the
    # played asset, so the capture window has to grow with it.
    sync_marker = None if args.no_sync_marker else SyncMarkerSpec()
    marker_lead_seconds = sync_marker.total_seconds() if sync_marker is not None else 0.0

    prepared_runs = _build_prepared_runs(
        benchmark_session_id=session_id,
        scenarios=scenarios,
        variants=variants,
        stt_configs=stt_configs,
        mic_profiles=mic_profiles,
        dfn3_tunings=dfn3_tunings,
        annotate_stt_model=(
            len({config.model for config in stt_configs}) > 1
            or any(config.model != DEFAULT_STT_MODELS[0] for config in stt_configs)
        ),
        annotate_mic_profile=len(mic_profiles) > 1 or any(profile != "auto" for profile in mic_profiles),
        annotate_dfn3_tuning=(
            len(dfn3_tunings) > 1
            or any(tuning.profile != "subtle" for tuning in dfn3_tunings)
            or any(tuning.label for tuning in dfn3_tunings)
            or any(
                value is not None
                for tuning in dfn3_tunings
                for value in (
                    tuning.wet_mix,
                    tuning.loudness_compensation,
                    tuning.max_compensation_gain,
                    tuning.post_gain_db,
                    tuning.peak_limit,
                )
            )
        ),
        default_run_seconds=max(args.run_seconds, 0.5),
        default_display_seconds=max(args.display_seconds, 0.5),
        save_server_capture=not args.disable_server_capture,
        marker_lead_seconds=marker_lead_seconds,
    )
    writer = BenchmarkReportWriter(session_root)
    session_scenario_id = scenarios[0].scenario_id if len({scenario.scenario_id for scenario in scenarios}) == 1 else "multi-scenario"
    plan = BenchmarkSessionPlan(
        session_id=session_id,
        scenario_id=session_scenario_id,
        run_specs=[prepared_run.run_spec for prepared_run in prepared_runs],
        environment=environment,
    )
    writer.write_session_plan(plan)

    run_outputs: list[dict[str, object]] = []
    control_server = BenchmarkControlServer(host=args.controller_host, port=args.controller_port)
    await control_server.start()
    print(f"Waiting for ChurchBridgeAudioBench device on ws://{args.controller_host}:{args.controller_port}")
    device_hello = await control_server.wait_for_device()
    print(f"Device connected: {device_hello.device_name} ({device_hello.system_version})")

    try:
        for run_number, prepared_run in enumerate(prepared_runs, start=1):
            run_spec = prepared_run.run_spec
            marker_id = run_number % 256
            scenario = prepared_run.scenario
            client = DisplayFeedClient(base_url=args.base_url, church_id=args.church_id)
            display_task = asyncio.create_task(client.collect_for_duration(prepared_run.display_seconds))
            try:
                run_result, telemetry_events, events, playback_result = await control_server.run_remote_benchmark(
                    run_spec=run_spec,
                    display_events_task=display_task,
                    playback_coordinator=(
                        (lambda scenario=scenario, marker_id=marker_id: schedule_audio_playback(
                            scenario.audio_path,
                            playback_delay_seconds=scenario.playback_lead_in_seconds,
                            playback_start_seconds=scenario.playback_start_seconds,
                            playback_duration_seconds=scenario.playback_duration_seconds,
                            degradation=scenario.degradation,
                            sync_marker=sync_marker,
                            marker_id=marker_id,
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
                marker_note = ""
                if sync_marker is not None:
                    marker_note = (
                        f" Sync marker id={marker_id} prepended"
                        f" ({sync_marker.total_seconds():.2f}s lead-in);"
                        " capture offset is recoverable with controller.alignment.find_marker."
                    )
                notes.append(f"Local controller playback was scheduled from {scenario.audio_path}.{window_note}{degradation_note}{marker_note}")
            if environment is not None:
                label = environment.get("label")
                env_note = f"Physical environment label: {label}." if label else "Physical environment metadata was provided for this session."
                notes.append(env_note)
                for note in environment.get("notes", []):
                    notes.append(f"Environment note: {note}")
            notes.extend(scenario.notes)
            playback_payload = playback_result.as_dict() if playback_result else None
            analysis = derive_run_analysis(
                run_spec=run_spec,
                display_events=events,
                playback=playback_payload,
                telemetry_events=telemetry_events,
            )
            run_result = apply_derived_run_analysis(run_result=run_result, analysis=analysis)

            if analysis.route_mismatch_event_count:
                notes.append(
                    f"Built-in mic routing was lost on {analysis.route_mismatch_event_count} telemetry snapshots during this run."
                )
            if analysis.capture_restart_count_max:
                notes.append(f"Capture graph restarted {analysis.capture_restart_count_max} time(s) during this run.")
            artifact = BenchmarkRunArtifact(
                run_spec=run_spec,
                device_hello=device_hello,
                run_result=run_result,
                telemetry_events=telemetry_events,
                display_events=events,
                playback=playback_payload,
                environment=environment,
                server_capture_requested=run_spec.save_server_capture,
                server_capture_label=run_spec.server_capture_label,
                analysis=analysis.as_dict(),
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
                    "stt_model": run_spec.stt_config.model,
                    "stt_language_codes": list(run_spec.stt_config.language_codes),
                    "mic_profile": run_spec.mic_profile,
                    "dfn3_tuning_profile": run_spec.dfn3_tuning.profile if run_spec.dfn3_tuning else None,
                    "dfn3_tuning_label": run_spec.dfn3_tuning.label if run_spec.dfn3_tuning else None,
                    "dfn3_tuning_slug": run_spec.dfn3_tuning.slug() if run_spec.dfn3_tuning else None,
                    "dfn3_tuning": run_spec.dfn3_tuning.as_dict() if run_spec.dfn3_tuning else None,
                    "final_transcript": run_result.final_transcript,
                    "first_partial_latency_ms": run_result.first_partial_latency_ms,
                    "first_final_latency_ms": run_result.first_final_latency_ms,
                    "wer": run_result.wer,
                    "cer": run_result.cer,
                    "transcript_similarity": analysis.transcript_similarity,
                    "applied_mic_profile": analysis.applied_mic_profile,
                    "route_uses_built_in_mic": analysis.route_uses_built_in_mic,
                    "selected_input_port": analysis.selected_input_port,
                    "selected_data_source": analysis.selected_data_source,
                    "selected_polar_pattern": analysis.selected_polar_pattern,
                    "capture_restart_count_max": analysis.capture_restart_count_max,
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
        environment=environment,
        server_capture_requested_run_count=sum(1 for prepared_run in prepared_runs if prepared_run.run_spec.save_server_capture),
        notes=[
            "Session order is intentionally stable so the baseline variant can always run first.",
            "Queued session execution now drives the iPhone benchmark app through the benchmark-owned control WebSocket.",
            "Server-side capture should persist benchmark audio under the supplied run labels in churchbridge-ai for later analysis.",
            "When scenario audio is configured, the controller now schedules local playback automatically with a lead-in so the iPhone can begin capture before room audio starts.",
            "Controller summaries now derive canonical final transcripts, first-partial latency, first-final latency, and text-quality metrics from display-feed STT events.",
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
    parser.add_argument(
        "--stt-models",
        nargs="+",
        default=DEFAULT_STT_MODELS,
        help="Ordered STT model identifiers to sweep per pipeline, for example chirp_3 nova-3.",
    )
    parser.add_argument(
        "--include-raw-compare",
        action="store_true",
        help="Prepend one raw_debug capture per scenario so DFN3 sweeps always include an unprocessed comparison run.",
    )
    parser.add_argument(
        "--mic-profiles",
        nargs="+",
        default=DEFAULT_MIC_PROFILES,
        choices=["auto", "front_cardioid", "back_cardioid"],
        help="Optional built-in mic steering profiles to sweep across each pipeline run.",
    )
    parser.add_argument(
        "--dfn3-profiles",
        nargs="+",
        default=DEFAULT_DFN3_PROFILES,
        choices=["subtle", "balanced", "full"],
        help="Optional DeepFilterNet3 tuning profiles to sweep across DFN3-backed pipelines.",
    )
    parser.add_argument(
        "--dfn3-tuning-file",
        action="append",
        default=[],
        help=(
            "JSON file containing one or more named DFN3 tuning objects. "
            "Each entry can define label, profile, wet_mix, loudness_compensation, "
            "max_compensation_gain, post_gain_db, and peak_limit."
        ),
    )
    parser.add_argument(
        "--dfn3-wet-mix",
        type=float,
        default=None,
        help="Override DFN3 wet mix from 0.0 to 1.0 for all selected DFN3 tuning profiles.",
    )
    parser.add_argument(
        "--dfn3-loudness-compensation",
        type=float,
        default=None,
        help="Override DFN3 loudness compensation blend from 0.0 to 1.0 for all selected DFN3 tuning profiles.",
    )
    parser.add_argument(
        "--dfn3-max-compensation-gain",
        type=float,
        default=None,
        help="Override the maximum DFN3 loudness-compensation gain multiplier.",
    )
    parser.add_argument(
        "--dfn3-post-gain-db",
        type=float,
        default=None,
        help="Override DFN3 post-gain in dB for all selected DFN3 tuning profiles.",
    )
    parser.add_argument(
        "--dfn3-peak-limit",
        type=float,
        default=None,
        help="Override DFN3 peak limiter ceiling from 0.5 to 1.0 for all selected DFN3 tuning profiles.",
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
    parser.add_argument(
        "--environment-label",
        default="",
        help="Optional session label for the physical acoustic setup, for example box_fan_medium_6ft.",
    )
    parser.add_argument(
        "--environment-note",
        action="append",
        default=[],
        help="Optional repeatable note describing the physical room setup, such as fan position or phone distance.",
    )
    parser.add_argument(
        "--no-sync-marker",
        action="store_true",
        help=(
            "Do not prepend the acoustic sync marker to played audio. The marker is what makes"
            " word error rate trustworthy, so disable it only when measuring the marker's own cost."
        ),
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
