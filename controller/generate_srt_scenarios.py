from __future__ import annotations

import argparse
import html
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


_SRT_TIMECODE = re.compile(r"^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$")
_SRT_INDEX = re.compile(r"^\d+$")
_NOISE_TAG = re.compile(r"\[.*?\]")


@dataclass(frozen=True)
class SRTSegment:
    index: int
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class SourcePair:
    audio_path: Path
    srt_path: Path
    source_label: str


def _timecode_to_seconds(value: str) -> float:
    hours, minutes, rest = value.split(":")
    seconds, millis = rest.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000.0


def _read_text_with_fallback(path: Path) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace"), "utf-8-replace"


def parse_srt(path: Path) -> tuple[list[SRTSegment], str]:
    raw, encoding = _read_text_with_fallback(path)
    segments: list[SRTSegment] = []
    current_index: int | None = None
    current_start = 0.0
    current_end = 0.0
    lines: list[str] = []

    def flush() -> None:
        nonlocal current_index, current_start, current_end, lines
        if current_index is None:
            return
        text = " ".join(lines).strip()
        if text:
            segments.append(
                SRTSegment(
                    index=current_index,
                    start=current_start,
                    end=current_end,
                    text=text,
                )
            )
        current_index = None
        current_start = 0.0
        current_end = 0.0
        lines = []

    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _SRT_INDEX.match(line):
            flush()
            current_index = int(line)
            continue
        if _SRT_TIMECODE.match(line):
            if current_index is None:
                continue
            start_text, end_text = line.split(" --> ", maxsplit=1)
            current_start = _timecode_to_seconds(start_text)
            current_end = _timecode_to_seconds(end_text)
            continue
        if current_index is None:
            continue
        cleaned = html.unescape(_NOISE_TAG.sub("", line)).strip()
        if cleaned:
            lines.append(cleaned)

    flush()
    return segments, encoding


def find_source_pair(directory: Path) -> SourcePair:
    audio_files = list(directory.glob("*.mp3")) + list(directory.glob("*.wav")) + list(directory.glob("*.m4a"))
    srt_files = list(directory.glob("*.srt"))
    if not audio_files:
        raise FileNotFoundError(f"No audio file found in {directory}")
    if not srt_files:
        raise FileNotFoundError(f"No SRT file found in {directory}")
    return SourcePair(
        audio_path=audio_files[0].resolve(),
        srt_path=srt_files[0].resolve(),
        source_label=directory.name,
    )


def _slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned or "scenario"


def _resolve_audio_path(audio_path: Path, *, manifest_path: Path, path_mode: str) -> str:
    if path_mode == "absolute":
        return str(audio_path)
    try:
        return Path(os.path.relpath(audio_path, start=manifest_path.parent)).as_posix()
    except ValueError:
        return str(audio_path)


def _anchor_targets(total_end_seconds: float, clips_per_source: int) -> list[float]:
    if clips_per_source <= 1:
        return [max(total_end_seconds * 0.33, 0.0)]
    first_fraction = 0.15
    last_fraction = 0.72
    step = (last_fraction - first_fraction) / float(clips_per_source - 1)
    return [total_end_seconds * (first_fraction + step * index) for index in range(clips_per_source)]


def build_scenarios(
    source: SourcePair,
    *,
    segments: list[SRTSegment],
    clip_duration_seconds: float,
    clips_per_source: int,
    playback_lead_in_seconds: float,
    playback_tail_seconds: float,
    manifest_path: Path,
    path_mode: str,
    min_words: int = 18,
) -> list[dict[str, object]]:
    if not segments:
        raise ValueError(f"No subtitle segments were parsed from {source.srt_path}")

    results: list[dict[str, object]] = []
    previous_end = -1.0
    total_end_seconds = segments[-1].end
    targets = _anchor_targets(total_end_seconds, clips_per_source)

    for clip_index, anchor_seconds in enumerate(targets, start=1):
        anchor_segment_index = min(
            range(len(segments)),
            key=lambda index: abs(segments[index].start - anchor_seconds),
        )

        while anchor_segment_index < len(segments) and segments[anchor_segment_index].start < previous_end + max(clip_duration_seconds * 0.75, 8.0):
            anchor_segment_index += 1
        if anchor_segment_index >= len(segments):
            break

        selected: list[SRTSegment] = []
        start_seconds = segments[anchor_segment_index].start
        target_end_seconds = start_seconds + clip_duration_seconds
        for segment in segments[anchor_segment_index:]:
            if segment.start >= target_end_seconds and selected and len(" ".join(item.text for item in selected).split()) >= min_words:
                break
            selected.append(segment)
            if segment.end >= target_end_seconds and len(" ".join(item.text for item in selected).split()) >= min_words:
                break

        if not selected:
            continue

        expected_transcript = " ".join(segment.text for segment in selected).strip()
        if len(expected_transcript.split()) < min_words:
            continue

        end_seconds = selected[-1].end
        duration_seconds = round(end_seconds - start_seconds, 3)
        previous_end = end_seconds
        source_slug = _slugify(source.audio_path.stem)

        results.append(
            {
                "scenario_id": f"{source.source_label}_{source_slug}_clip_{clip_index:02d}",
                "expected_transcript": expected_transcript,
                "audio_path": _resolve_audio_path(source.audio_path, manifest_path=manifest_path, path_mode=path_mode),
                "playback_start_seconds": round(start_seconds, 3),
                "playback_duration_seconds": duration_seconds,
                "playback_lead_in_seconds": playback_lead_in_seconds,
                "playback_tail_seconds": playback_tail_seconds,
                "notes": [
                    f"Source audio: {source.audio_path.name}",
                    f"Source subtitles: {source.srt_path.name}",
                    f"Subtitle window: {start_seconds:.3f}s-{end_seconds:.3f}s",
                ],
            }
        )

    return results


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate benchmark scenario manifests from long audio + SRT pairs.")
    parser.add_argument(
        "--source-dir",
        action="append",
        required=True,
        help="Directory containing one audio file and one SRT file. Can be provided multiple times.",
    )
    parser.add_argument(
        "--manifest-path",
        default=str(Path(__file__).resolve().parents[1] / "scenarios" / "generated-srt-scenarios.json"),
        help="Output JSON manifest path.",
    )
    parser.add_argument("--clips-per-source", type=int, default=2, help="How many playback windows to select from each source.")
    parser.add_argument("--clip-duration-seconds", type=float, default=12.0, help="Target duration per playback window.")
    parser.add_argument("--playback-lead-in-seconds", type=float, default=1.25, help="Capture lead-in before playback starts.")
    parser.add_argument("--playback-tail-seconds", type=float, default=1.0, help="Extra capture time after playback ends.")
    parser.add_argument("--path-mode", choices=("relative", "absolute"), default="relative", help="How to write audio paths into the manifest.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    manifest_path = Path(args.manifest_path).expanduser().resolve()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    all_scenarios: list[dict[str, object]] = []
    for source_dir_text in args.source_dir:
        source_dir = Path(source_dir_text).expanduser().resolve()
        source = find_source_pair(source_dir)
        segments, encoding = parse_srt(source.srt_path)
        scenarios = build_scenarios(
            source,
            segments=segments,
            clip_duration_seconds=max(args.clip_duration_seconds, 1.0),
            clips_per_source=max(args.clips_per_source, 1),
            playback_lead_in_seconds=max(args.playback_lead_in_seconds, 0.0),
            playback_tail_seconds=max(args.playback_tail_seconds, 0.0),
            manifest_path=manifest_path,
            path_mode=args.path_mode,
        )
        if not scenarios:
            raise RuntimeError(f"No scenarios were generated from {source_dir} ({encoding}).")
        print(f"{source_dir.name}: {len(scenarios)} scenarios from {source.audio_path.name} ({encoding})")
        all_scenarios.extend(scenarios)

    manifest = {"scenarios": all_scenarios}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(all_scenarios)} scenarios to {manifest_path}")


if __name__ == "__main__":
    main()
