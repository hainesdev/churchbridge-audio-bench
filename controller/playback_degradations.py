from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


NOISE_TYPES = ("white", "pink", "hvac", "crowd", "street", "babble")
ECHO_PROFILES = ("light", "medium", "heavy")
SUPPORTED_CONDITIONS = ("clean", "echo", "noise", "echo_noise")


def _format_numeric(value: float) -> str:
    text = f"{value:g}"
    return text.replace("-", "neg").replace(".", "p")


def _safe_slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = cleaned.strip("-_.")
    return cleaned or "audio"


def _ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


@dataclass(frozen=True)
class PlaybackDegradationSpec:
    condition: str
    echo_profile: str | None = None
    noise_type: str | None = None
    snr_db: float | None = None
    seed: int = 7

    def case_id(self) -> str:
        parts = [self.condition]
        if self.echo_profile:
            parts.append(self.echo_profile)
        if self.noise_type:
            parts.append(self.noise_type)
        if self.snr_db is not None:
            parts.append(f"{_format_numeric(self.snr_db)}db")
        return "_".join(parts)

    def metadata(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "echo_profile": self.echo_profile,
            "noise_type": self.noise_type,
            "snr_db": self.snr_db,
            "seed": self.seed,
            "case_id": self.case_id(),
        }

    @property
    def is_active(self) -> bool:
        return self.condition != "clean"


@dataclass(frozen=True)
class PreparedPlaybackAsset:
    prepared_path: Path
    expected_duration_seconds: float | None
    source_excerpt_path: Path | None = None


def build_degradation_spec(
    condition: str,
    *,
    echo_profile: str | None,
    noise_type: str | None,
    snr_db: float | None,
    seed: int,
) -> PlaybackDegradationSpec:
    if condition not in SUPPORTED_CONDITIONS:
        raise ValueError(f"Unknown condition: {condition}")
    if condition in {"echo", "echo_noise"} and echo_profile not in ECHO_PROFILES:
        raise ValueError(f"Echo profile required for {condition}. Choose one of {ECHO_PROFILES}.")
    if condition in {"noise", "echo_noise"} and noise_type not in NOISE_TYPES:
        raise ValueError(f"Noise type required for {condition}. Choose one of {NOISE_TYPES}.")
    if condition in {"noise", "echo_noise"} and snr_db is None:
        raise ValueError(f"SNR is required for {condition}.")

    if condition == "clean":
        echo_profile = None
        noise_type = None
        snr_db = None
    elif condition == "echo":
        noise_type = None
        snr_db = None
    elif condition == "noise":
        echo_profile = None

    return PlaybackDegradationSpec(
        condition=condition,
        echo_profile=echo_profile,
        noise_type=noise_type,
        snr_db=snr_db,
        seed=seed,
    )


def parse_degradation_from_payload(payload: dict[str, Any]) -> PlaybackDegradationSpec | None:
    raw_degradation = payload.get("degradation")
    if raw_degradation is not None and not isinstance(raw_degradation, dict):
        raise ValueError("Scenario degradation must be an object when provided.")
    degradation_payload = raw_degradation if isinstance(raw_degradation, dict) else payload

    condition = str(
        degradation_payload.get("condition")
        or degradation_payload.get("degradation_condition")
        or degradation_payload.get("degradationCondition")
        or ""
    ).strip()
    if not condition:
        return None

    echo_profile = str(
        degradation_payload.get("echo_profile")
        or degradation_payload.get("echoProfile")
        or ""
    ).strip() or None
    noise_type = str(
        degradation_payload.get("noise_type")
        or degradation_payload.get("noiseType")
        or ""
    ).strip() or None

    raw_snr = degradation_payload.get("snr_db")
    if raw_snr is None:
        raw_snr = degradation_payload.get("snrDb")
    snr_db = float(raw_snr) if raw_snr is not None else None

    raw_seed = degradation_payload.get("seed")
    if raw_seed is None:
        raw_seed = degradation_payload.get("degradation_seed")
    if raw_seed is None:
        raw_seed = degradation_payload.get("degradationSeed")
    seed = int(raw_seed) if raw_seed is not None else 7

    return build_degradation_spec(
        condition,
        echo_profile=echo_profile,
        noise_type=noise_type,
        snr_db=snr_db,
        seed=seed,
    )


def prepare_playback_asset(
    source_audio_path: str | Path,
    *,
    cache_root: Path,
    playback_start_seconds: float = 0.0,
    playback_duration_seconds: float | None = None,
    degradation: PlaybackDegradationSpec,
) -> PreparedPlaybackAsset:
    source_path = Path(source_audio_path).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Audio file does not exist: {source_path}")
    if not degradation.is_active:
        raise ValueError("prepare_playback_asset only supports active degradations.")

    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to prepare degraded playback assets.")

    playback_start_seconds = max(playback_start_seconds, 0.0)
    if playback_duration_seconds is not None:
        playback_duration_seconds = max(playback_duration_seconds, 0.0)

    cache_root.mkdir(parents=True, exist_ok=True)
    source_stat = source_path.stat()
    window_token = f"ss{playback_start_seconds:.3f}|dur{playback_duration_seconds if playback_duration_seconds is not None else 'full'}"
    source_fingerprint = hashlib.sha1(
        f"{source_path}|{source_stat.st_mtime_ns}|{source_stat.st_size}|{window_token}".encode("utf-8")
    ).hexdigest()[:12]
    source_slug = _safe_slug(source_path.stem)
    excerpt_path = cache_root / f"{source_slug}__{source_fingerprint}__clean_excerpt.wav"
    rendered_path = cache_root / f"{source_slug}__{source_fingerprint}__{degradation.case_id()}.wav"

    if rendered_path.exists():
        expected_duration = _probe_wav_duration(rendered_path)
        return PreparedPlaybackAsset(
            prepared_path=rendered_path,
            expected_duration_seconds=expected_duration,
            source_excerpt_path=excerpt_path if excerpt_path.exists() else None,
        )

    _extract_excerpt(
        source_path,
        excerpt_path,
        ffmpeg=ffmpeg,
        playback_start_seconds=playback_start_seconds,
        playback_duration_seconds=playback_duration_seconds,
    )
    samples, sample_rate = _read_pcm16_wav(excerpt_path)
    degraded = apply_degradation(samples, sample_rate, degradation)
    _write_pcm16_wav(rendered_path, degraded, sample_rate)
    expected_duration = _probe_wav_duration(rendered_path)
    return PreparedPlaybackAsset(
        prepared_path=rendered_path,
        expected_duration_seconds=expected_duration,
        source_excerpt_path=excerpt_path,
    )


def _extract_excerpt(
    source_path: Path,
    excerpt_path: Path,
    *,
    ffmpeg: str,
    playback_start_seconds: float,
    playback_duration_seconds: float | None,
) -> None:
    command = [
        ffmpeg,
        "-y",
        "-v",
        "error",
        "-ss",
        f"{playback_start_seconds:.3f}",
        "-i",
        str(source_path),
        "-vn",
    ]
    if playback_duration_seconds is not None:
        command.extend(["-t", f"{playback_duration_seconds:.3f}"])
    command.extend(
        [
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(excerpt_path),
        ]
    )
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed while preparing playback audio for {source_path}: {completed.stderr.strip() or completed.stdout.strip()}"
        )


def _probe_wav_duration(path: Path) -> float | None:
    with wave.open(str(path), "rb") as wav_file:
        frame_rate = wav_file.getframerate()
        frame_count = wav_file.getnframes()
        if frame_rate <= 0:
            return None
        return frame_count / float(frame_rate)


def _read_pcm16_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_rate = wav_file.getframerate()
        sample_width = wav_file.getsampwidth()
        frame_count = wav_file.getnframes()
        raw = wav_file.readframes(frame_count)

    if sample_width != 2:
        raise RuntimeError(f"Expected 16-bit PCM WAV for playback degradation, got {sample_width * 8}-bit at {path}")

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples.astype(np.float32, copy=False), sample_rate


def _write_pcm16_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    pcm16 = (np.asarray(samples, dtype=np.float32).clip(-1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm16.tobytes())


def apply_degradation(samples: np.ndarray, sample_rate: int, spec: PlaybackDegradationSpec) -> np.ndarray:
    base = np.asarray(samples, dtype=np.float32)
    if spec.condition == "clean":
        return base.copy()

    rng = np.random.default_rng(spec.seed)
    working = base.copy()

    if spec.condition in {"echo", "echo_noise"}:
        working = _apply_echo(working, sample_rate, spec.echo_profile or "medium")

    if spec.condition in {"noise", "echo_noise"}:
        noise = _generate_noise(spec.noise_type or "pink", len(working), sample_rate, rng)
        working = _mix_at_snr(working, noise, float(spec.snr_db))

    return _peak_limit(working)


def _rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def _peak_limit(samples: np.ndarray, peak: float = 0.95) -> np.ndarray:
    current_peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if current_peak == 0.0:
        return samples.astype(np.float32, copy=True)
    scale = min(1.0, peak / current_peak)
    return (samples * scale).astype(np.float32, copy=False)


def _fft_convolve(signal: np.ndarray, impulse: np.ndarray) -> np.ndarray:
    full_len = len(signal) + len(impulse) - 1
    n_fft = 1 << (full_len - 1).bit_length()
    signal_fft = np.fft.rfft(signal, n=n_fft)
    impulse_fft = np.fft.rfft(impulse, n=n_fft)
    return np.fft.irfft(signal_fft * impulse_fft, n=n_fft)[:full_len].astype(np.float32)


def _apply_echo(samples: np.ndarray, sample_rate: int, profile: str) -> np.ndarray:
    impulse = _build_echo_impulse(profile, sample_rate)
    wet = _fft_convolve(samples.astype(np.float32), impulse)[: len(samples)]
    dry_rms = max(_rms(samples), 1e-6)
    wet_rms = max(_rms(wet), 1e-6)
    return ((wet * (dry_rms / wet_rms)) * 0.98).astype(np.float32)


def _build_echo_impulse(profile: str, sample_rate: int) -> np.ndarray:
    if profile not in ECHO_PROFILES:
        raise ValueError(f"Unknown echo profile: {profile}")

    taps_by_profile = {
        "light": [
            (0.0, 1.0),
            (0.035, 0.35),
            (0.072, 0.22),
            (0.110, 0.12),
        ],
        "medium": [
            (0.0, 1.0),
            (0.050, 0.42),
            (0.110, 0.30),
            (0.180, 0.18),
            (0.260, 0.10),
        ],
        "heavy": [
            (0.0, 1.0),
            (0.065, 0.48),
            (0.135, 0.38),
            (0.220, 0.28),
            (0.340, 0.18),
            (0.480, 0.10),
        ],
    }
    taps = taps_by_profile[profile]
    ir_len = int(sample_rate * (max(delay for delay, _ in taps) + 0.15))
    impulse = np.zeros(ir_len, dtype=np.float32)
    for delay_s, gain in taps:
        idx = min(ir_len - 1, int(round(delay_s * sample_rate)))
        impulse[idx] += gain

    tail_start = int(round(0.02 * sample_rate))
    if tail_start < ir_len:
        tail = np.exp(-np.linspace(0.0, 4.0 if profile == "light" else 6.0, ir_len - tail_start))
        tail_gain = {"light": 0.025, "medium": 0.05, "heavy": 0.08}[profile]
        impulse[tail_start:] += tail.astype(np.float32) * tail_gain

    impulse /= max(float(np.sum(np.abs(impulse))), 1.0)
    return impulse


def _generate_noise(
    noise_type: str,
    n_samples: int,
    sample_rate: int,
    rng: np.random.Generator,
) -> np.ndarray:
    if noise_type not in NOISE_TYPES:
        raise ValueError(f"Unknown noise type: {noise_type}")

    if noise_type == "white":
        noise = rng.standard_normal(n_samples)
    elif noise_type == "pink":
        noise = _colored_noise(n_samples, sample_rate, rng, exponent=1.0)
    elif noise_type == "hvac":
        hum = _tone(sample_rate, n_samples, 60.0) * 0.12 + _tone(sample_rate, n_samples, 120.0) * 0.06
        rumble = _colored_noise(n_samples, sample_rate, rng, exponent=1.3, lowpass_hz=350.0)
        noise = hum + rumble
    elif noise_type == "crowd":
        speech_bed = _band_limited_noise(n_samples, sample_rate, rng, 180.0, 3600.0)
        envelope = _slow_envelope(n_samples, sample_rate, rng, min_hz=0.25, max_hz=1.2)
        noise = speech_bed * envelope
    elif noise_type == "street":
        rumble = _colored_noise(n_samples, sample_rate, rng, exponent=1.4, lowpass_hz=220.0) * 0.8
        hiss = _band_limited_noise(n_samples, sample_rate, rng, 1800.0, 7000.0) * 0.35
        bursts = np.zeros(n_samples, dtype=np.float32)
        for start in range(0, n_samples, max(sample_rate // 2, 1)):
            if rng.random() < 0.12:
                burst_len = min(n_samples - start, int(sample_rate * rng.uniform(0.05, 0.18)))
                window = np.hanning(max(burst_len, 2))[:burst_len]
                bursts[start : start + burst_len] += (rng.standard_normal(burst_len) * window * 0.5).astype(np.float32)
        noise = rumble + hiss + bursts
    else:
        layers = []
        for _ in range(6):
            band = _band_limited_noise(n_samples, sample_rate, rng, 220.0, 4200.0)
            band *= _slow_envelope(n_samples, sample_rate, rng, min_hz=0.6, max_hz=3.0)
            layers.append(band)
        noise = np.sum(layers, axis=0) / max(len(layers), 1)

    noise = np.asarray(noise, dtype=np.float32)
    noise_rms = max(_rms(noise), 1e-6)
    return (noise / noise_rms).astype(np.float32)


def _mix_at_snr(clean: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    clean_rms = max(_rms(clean), 1e-6)
    target_noise_rms = clean_rms / (10.0 ** (snr_db / 20.0))
    noise_rms = max(_rms(noise), 1e-6)
    mixed = clean + noise * (target_noise_rms / noise_rms)
    return mixed.astype(np.float32)


def _tone(sample_rate: int, n_samples: int, hz: float) -> np.ndarray:
    t = np.arange(n_samples, dtype=np.float32) / float(sample_rate)
    return np.sin(2.0 * np.pi * hz * t).astype(np.float32)


def _slow_envelope(
    n_samples: int,
    sample_rate: int,
    rng: np.random.Generator,
    min_hz: float,
    max_hz: float,
) -> np.ndarray:
    t = np.arange(n_samples, dtype=np.float32) / float(sample_rate)
    parts = []
    for _ in range(3):
        freq = rng.uniform(min_hz, max_hz)
        phase = rng.uniform(0, 2 * np.pi)
        parts.append(np.sin(2 * np.pi * freq * t + phase))
    envelope = np.mean(parts, axis=0)
    envelope = 0.45 + 0.55 * ((envelope - envelope.min()) / max(envelope.max() - envelope.min(), 1e-6))
    return envelope.astype(np.float32)


def _colored_noise(
    n_samples: int,
    sample_rate: int,
    rng: np.random.Generator,
    exponent: float,
    lowpass_hz: float | None = None,
) -> np.ndarray:
    freqs = np.fft.rfftfreq(n_samples, d=1.0 / float(sample_rate))
    phases = rng.standard_normal(freqs.size) + 1j * rng.standard_normal(freqs.size)
    scale = np.ones_like(freqs, dtype=np.float64)
    nonzero = freqs > 0
    scale[nonzero] = 1.0 / np.power(freqs[nonzero], exponent / 2.0)
    if lowpass_hz is not None:
        scale *= 1.0 / (1.0 + np.power(freqs / max(lowpass_hz, 1.0), 4.0))
    spectrum = phases * scale
    spectrum[0] = 0.0
    return np.fft.irfft(spectrum, n=n_samples).astype(np.float32)


def _band_limited_noise(
    n_samples: int,
    sample_rate: int,
    rng: np.random.Generator,
    low_hz: float,
    high_hz: float,
) -> np.ndarray:
    freqs = np.fft.rfftfreq(n_samples, d=1.0 / float(sample_rate))
    phases = rng.standard_normal(freqs.size) + 1j * rng.standard_normal(freqs.size)
    mask = (freqs >= low_hz) & (freqs <= high_hz)
    spectrum = np.where(mask, phases, 0.0)
    spectrum[0] = 0.0
    return np.fft.irfft(spectrum, n=n_samples).astype(np.float32)
