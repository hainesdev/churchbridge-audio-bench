from __future__ import annotations

import base64
import asyncio
import shutil
import subprocess
import sys
import wave
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .alignment import SyncMarkerSpec
from .playback_degradations import PlaybackDegradationSpec, prepare_playback_asset


def base64_to_float32_bytes(base64_text: str) -> bytes:
    """Decode base64-encoded Float32 LE audio.

    This mirrors the payload shape already accepted by `churchbridge-ai`, but
    lives in benchmark-owned controller code so we can adapt it freely.
    """

    return base64.b64decode(base64_text)


def resample_float32_to_pcm16(
    data: bytes,
    src_rate: int,
    dst_rate: int = 16_000,
) -> bytes:
    """Resample Float32 LE mono audio bytes to PCM16 LE.

    This is a benchmark-local rewrite of the existing server helper so later
    controller-side artifact capture and offline inspection do not depend on the
    full Church Bridge backend package.
    """

    samples = np.frombuffer(data, dtype=np.float32)
    if src_rate == dst_rate:
        pcm16 = (samples * 32767).clip(-32768, 32767).astype(np.int16)
        return pcm16.tobytes()

    ratio = dst_rate / src_rate
    new_length = max(1, int(len(samples) * ratio))
    indices = np.linspace(0, len(samples) - 1, new_length)
    resampled = np.interp(indices, np.arange(len(samples)), samples)
    pcm16 = (resampled * 32767).clip(-32768, 32767).astype(np.int16)
    return pcm16.tobytes()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _ffplay_path() -> str | None:
    return shutil.which("ffplay")


def _ffprobe_path() -> str | None:
    return shutil.which("ffprobe")


@dataclass
class AudioPlaybackResult:
    audio_path: str
    source_audio_path: str | None
    player: str
    started_at: str
    completed_at: str
    playback_delay_ms: int
    playback_start_seconds: float
    playback_duration_seconds: float | None
    expected_duration_seconds: float | None
    degradation: dict[str, object] | None
    sync_marker: dict[str, object] | None
    return_code: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class ScheduledAudioPlayback:
    audio_path: str
    source_audio_path: str | None
    player: str
    started_at: str
    playback_delay_ms: int
    playback_start_seconds: float
    playback_duration_seconds: float | None
    expected_duration_seconds: float | None
    degradation: dict[str, object] | None
    sync_marker: dict[str, object] | None = None
    completion_task: asyncio.Task[AudioPlaybackResult] | None = field(default=None, repr=False, compare=False)


def probe_audio_duration_seconds(path: str | Path) -> float | None:
    audio_path = Path(path).expanduser().resolve()
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file does not exist: {audio_path}")

    ffprobe = _ffprobe_path()
    if ffprobe:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nokey=1:noprint_wrappers=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            try:
                duration = float((completed.stdout or "").strip())
                if duration > 0:
                    return duration
            except ValueError:
                pass

    if audio_path.suffix.lower() == ".wav":
        with wave.open(str(audio_path), "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            if frame_rate > 0:
                return frame_count / float(frame_rate)

    return None


async def schedule_audio_playback(
    path: str | Path,
    *,
    playback_delay_seconds: float,
    playback_start_seconds: float = 0.0,
    playback_duration_seconds: float | None = None,
    degradation: PlaybackDegradationSpec | None = None,
    sync_marker: SyncMarkerSpec | None = None,
    marker_id: int | None = None,
) -> ScheduledAudioPlayback:
    audio_path = Path(path).expanduser().resolve()
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file does not exist: {audio_path}")

    playback_start_seconds = max(playback_start_seconds, 0.0)
    if playback_duration_seconds is not None:
        playback_duration_seconds = max(playback_duration_seconds, 0.0)

    full_duration_seconds = probe_audio_duration_seconds(audio_path)
    available_duration_seconds = None
    if full_duration_seconds is not None:
        available_duration_seconds = max(full_duration_seconds - playback_start_seconds, 0.0)
        if playback_start_seconds >= full_duration_seconds:
            raise ValueError(
                f"Playback start {playback_start_seconds:.3f}s exceeds audio duration {full_duration_seconds:.3f}s for {audio_path}"
            )

    if playback_duration_seconds is not None:
        expected_duration_seconds = (
            min(playback_duration_seconds, available_duration_seconds)
            if available_duration_seconds is not None
            else playback_duration_seconds
        )
    else:
        expected_duration_seconds = available_duration_seconds if available_duration_seconds is not None else full_duration_seconds

    prepared_audio_path = audio_path
    prepared_playback_start_seconds = playback_start_seconds
    prepared_playback_duration_seconds = playback_duration_seconds
    degradation_metadata = degradation.metadata() if degradation is not None else None
    marker_metadata: dict[str, object] | None = None
    if sync_marker is not None:
        # Recorded so a capture found later can be aligned without guessing the
        # marker geometry that produced it.
        marker_metadata = {"spec": sync_marker.as_dict(), "marker_id": marker_id}
    needs_prepared_asset = (degradation is not None and degradation.is_active) or sync_marker is not None
    if needs_prepared_asset:
        cache_root = Path(__file__).resolve().parents[1] / "reports" / "_prepared_playback"
        prepared_asset = await asyncio.to_thread(
            prepare_playback_asset,
            audio_path,
            cache_root=cache_root,
            playback_start_seconds=playback_start_seconds,
            playback_duration_seconds=playback_duration_seconds,
            degradation=degradation if degradation is not None else PlaybackDegradationSpec(),
            sync_marker=sync_marker,
            marker_id=marker_id,
        )
        prepared_audio_path = prepared_asset.prepared_path
        prepared_playback_start_seconds = 0.0
        prepared_playback_duration_seconds = None
        expected_duration_seconds = prepared_asset.expected_duration_seconds

    player = "ffplay" if _ffplay_path() else ("winsound" if sys.platform.startswith("win") else "unavailable")
    planned_start = _utc_now() + timedelta(seconds=max(playback_delay_seconds, 0))
    task = asyncio.create_task(
        _play_audio_with_delay(
            prepared_audio_path,
            playback_delay_seconds=max(playback_delay_seconds, 0),
            playback_start_seconds=prepared_playback_start_seconds,
            playback_duration_seconds=prepared_playback_duration_seconds,
            expected_duration_seconds=expected_duration_seconds,
            source_audio_path=audio_path,
            degradation=degradation_metadata,
            sync_marker=marker_metadata,
        )
    )
    return ScheduledAudioPlayback(
        audio_path=str(prepared_audio_path),
        source_audio_path=str(audio_path),
        player=player,
        started_at=planned_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        playback_delay_ms=int(round(max(playback_delay_seconds, 0) * 1_000)),
        playback_start_seconds=prepared_playback_start_seconds,
        playback_duration_seconds=prepared_playback_duration_seconds,
        expected_duration_seconds=expected_duration_seconds,
        degradation=degradation_metadata,
        sync_marker=marker_metadata,
        completion_task=task,
    )


async def _play_audio_with_delay(
    audio_path: Path,
    *,
    playback_delay_seconds: float,
    playback_start_seconds: float,
    playback_duration_seconds: float | None,
    expected_duration_seconds: float | None,
    source_audio_path: Path | None = None,
    degradation: dict[str, object] | None = None,
    sync_marker: dict[str, object] | None = None,
) -> AudioPlaybackResult:
    if playback_delay_seconds > 0:
        await asyncio.sleep(playback_delay_seconds)
    started_at = _utc_now_iso()
    return await play_audio_file(
        audio_path,
        started_at=started_at,
        playback_delay_ms=int(round(playback_delay_seconds * 1_000)),
        playback_start_seconds=playback_start_seconds,
        playback_duration_seconds=playback_duration_seconds,
        expected_duration_seconds=expected_duration_seconds,
        source_audio_path=source_audio_path,
        degradation=degradation,
        sync_marker=sync_marker,
    )


async def play_audio_file(
    path: str | Path,
    *,
    started_at: str | None = None,
    playback_delay_ms: int = 0,
    playback_start_seconds: float = 0.0,
    playback_duration_seconds: float | None = None,
    expected_duration_seconds: float | None = None,
    source_audio_path: str | Path | None = None,
    degradation: dict[str, object] | None = None,
    sync_marker: dict[str, object] | None = None,
) -> AudioPlaybackResult:
    audio_path = Path(path).expanduser().resolve()
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file does not exist: {audio_path}")

    started_at = started_at or _utc_now_iso()
    playback_start_seconds = max(playback_start_seconds, 0.0)
    if playback_duration_seconds is not None:
        playback_duration_seconds = max(playback_duration_seconds, 0.0)
    ffplay = _ffplay_path()
    if ffplay:
        command = [
            ffplay,
            "-v",
            "error",
            "-nodisp",
            "-autoexit",
            "-nostats",
        ]
        if playback_start_seconds > 0:
            command.extend(["-ss", f"{playback_start_seconds:.3f}"])
        if playback_duration_seconds is not None:
            command.extend(["-t", f"{playback_duration_seconds:.3f}"])
        command.append(str(audio_path))
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        return_code = await process.wait()
        if return_code != 0:
            raise RuntimeError(f"ffplay exited with status {return_code} for {audio_path}")
        return AudioPlaybackResult(
            audio_path=str(audio_path),
            source_audio_path=str(Path(source_audio_path).expanduser().resolve()) if source_audio_path is not None else None,
            player="ffplay",
            started_at=started_at,
            completed_at=_utc_now_iso(),
            playback_delay_ms=playback_delay_ms,
            playback_start_seconds=playback_start_seconds,
            playback_duration_seconds=playback_duration_seconds,
            expected_duration_seconds=expected_duration_seconds,
            degradation=degradation,
            sync_marker=sync_marker,
            return_code=return_code,
        )

    if sys.platform.startswith("win") and audio_path.suffix.lower() == ".wav":
        if playback_start_seconds > 0 or playback_duration_seconds is not None:
            raise RuntimeError("Windowed playback requires ffplay when using Windows WAV fallback.")
        import winsound

        await asyncio.to_thread(winsound.PlaySound, str(audio_path), winsound.SND_FILENAME)
        return AudioPlaybackResult(
            audio_path=str(audio_path),
            source_audio_path=str(Path(source_audio_path).expanduser().resolve()) if source_audio_path is not None else None,
            player="winsound",
            started_at=started_at,
            completed_at=_utc_now_iso(),
            playback_delay_ms=playback_delay_ms,
            playback_start_seconds=playback_start_seconds,
            playback_duration_seconds=playback_duration_seconds,
            expected_duration_seconds=expected_duration_seconds,
            degradation=degradation,
            sync_marker=sync_marker,
            return_code=0,
        )

    raise RuntimeError(
        "No supported local audio playback tool was found. Install ffplay or use a WAV file on Windows."
    )
