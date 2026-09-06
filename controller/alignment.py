"""Acoustic sync marker for aligning captured audio to the reference timeline.

The rig plays a clip through speakers and the phone captures it. Playback start,
capture start, and network delivery each add unknown latency, so the captured
audio is offset from the reference transcript by an amount nobody measured. Word
error rate is acutely sensitive to that: a timing slip is scored as words missed
or invented when nothing was misrecognized.

This is the clapperboard. A short marker is prepended to the played audio, and
because it is played through the same speakers into the same room, finding it in
the capture gives the offset directly — no clock sync, no shared timebase.

The marker has two parts:

1. A **chirp** (frequency sweep). Cross-correlating the capture against the known
   sweep gives a sharp peak even at poor SNR, because the correlation integrates
   over the whole sweep rather than relying on a single transient. A clap or
   click smears under room reverb; a sweep does not.
2. An optional **BFSK register** — a few bits encoding a run identifier, so a
   capture found later on disk can prove which run produced it rather than
   relying on a filename.

Everything sits between 800 Hz and 4 kHz, comfortably below the 8 kHz Nyquist
limit of the 16 kHz stream, so the marker survives the pipeline's downsampling
intact.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np


DEFAULT_ID_BITS = 8


@dataclass(frozen=True)
class SyncMarkerSpec:
    """Shape of the marker. Both ends must agree on this to align."""

    chirp_seconds: float = 0.30
    chirp_start_hz: float = 800.0
    chirp_end_hz: float = 4000.0
    gap_seconds: float = 0.10
    id_bits: int = DEFAULT_ID_BITS
    bit_seconds: float = 0.04
    bit_low_hz: float = 1200.0
    bit_high_hz: float = 2400.0
    lead_out_seconds: float = 0.20
    amplitude: float = 0.5

    @property
    def carries_id(self) -> bool:
        return self.id_bits > 0

    def total_seconds(self) -> float:
        payload = self.id_bits * self.bit_seconds if self.carries_id else 0.0
        return self.chirp_seconds + self.gap_seconds + payload + self.lead_out_seconds

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class MarkerDetection:
    """Where the marker was found, and how much to trust it."""

    found: bool
    offset_samples: int
    offset_seconds: float
    confidence: float
    marker_id: int | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _tukey(length: int, alpha: float = 0.25) -> np.ndarray:
    """Tapered window so the sweep starts and ends without a click."""
    if length <= 0:
        return np.zeros(0, dtype=np.float64)
    if alpha <= 0:
        return np.ones(length, dtype=np.float64)
    window = np.ones(length, dtype=np.float64)
    taper = int(alpha * (length - 1) / 2.0)
    if taper < 1:
        return window
    ramp = 0.5 * (1 + np.cos(np.pi * (np.linspace(0, 1, taper) - 1)))
    window[:taper] = ramp
    window[-taper:] = ramp[::-1]
    return window


def generate_chirp(spec: SyncMarkerSpec, sample_rate: int) -> np.ndarray:
    """Linear sweep from chirp_start_hz to chirp_end_hz."""
    count = int(round(spec.chirp_seconds * sample_rate))
    if count <= 0:
        return np.zeros(0, dtype=np.float64)
    t = np.arange(count, dtype=np.float64) / sample_rate
    duration = count / sample_rate
    rate = (spec.chirp_end_hz - spec.chirp_start_hz) / duration
    phase = 2 * np.pi * (spec.chirp_start_hz * t + 0.5 * rate * t * t)
    return np.sin(phase) * _tukey(count)


def _bit_tone(freq: float, count: int, sample_rate: int) -> np.ndarray:
    t = np.arange(count, dtype=np.float64) / sample_rate
    return np.sin(2 * np.pi * freq * t) * _tukey(count, alpha=0.35)


def generate_marker(
    spec: SyncMarkerSpec,
    sample_rate: int,
    marker_id: int | None = None,
) -> np.ndarray:
    """Build the full marker: chirp, gap, optional id register, lead-out."""
    parts: list[np.ndarray] = [generate_chirp(spec, sample_rate)]
    parts.append(np.zeros(int(round(spec.gap_seconds * sample_rate)), dtype=np.float64))

    if spec.carries_id:
        value = 0 if marker_id is None else int(marker_id) & ((1 << spec.id_bits) - 1)
        bit_count = int(round(spec.bit_seconds * sample_rate))
        for index in range(spec.id_bits):
            bit = (value >> (spec.id_bits - 1 - index)) & 1
            freq = spec.bit_high_hz if bit else spec.bit_low_hz
            parts.append(_bit_tone(freq, bit_count, sample_rate))

    parts.append(np.zeros(int(round(spec.lead_out_seconds * sample_rate)), dtype=np.float64))
    return np.concatenate(parts) * spec.amplitude


def prepend_marker(
    samples: np.ndarray,
    sample_rate: int,
    spec: SyncMarkerSpec | None = None,
    marker_id: int | None = None,
) -> np.ndarray:
    """Return `samples` with the marker in front of it."""
    spec = spec or SyncMarkerSpec()
    marker = generate_marker(spec, sample_rate, marker_id)
    scale = float(np.max(np.abs(samples))) if samples.size else 1.0
    if scale <= 0:
        scale = 1.0
    return np.concatenate([marker * scale, np.asarray(samples, dtype=np.float64)])


def _normalized_correlation(signal: np.ndarray, template: np.ndarray) -> np.ndarray:
    """Matched filter, normalized by local signal energy.

    Plain cross-correlation peaks wherever the signal is merely loud. Dividing
    by the energy of each window makes the score a similarity in 0..1, so the
    confidence figure means something and a fan blast cannot masquerade as a
    sweep.
    """
    n = len(signal)
    m = len(template)
    if m == 0 or n < m:
        return np.zeros(0, dtype=np.float64)

    size = 1 << int(np.ceil(np.log2(n + m)))
    corr = np.fft.irfft(
        np.fft.rfft(signal, size) * np.conj(np.fft.rfft(template, size)), size
    )[: n - m + 1]

    # Sliding energy of the signal under the template window.
    power = np.concatenate([[0.0], np.cumsum(signal.astype(np.float64) ** 2)])
    window_energy = power[m:] - power[:-m]
    window_energy = window_energy[: len(corr)]

    template_energy = float(np.sum(template.astype(np.float64) ** 2))
    denom = np.sqrt(np.maximum(window_energy, 1e-12) * max(template_energy, 1e-12))
    return corr / denom


def _decode_id(
    captured: np.ndarray,
    sample_rate: int,
    spec: SyncMarkerSpec,
    chirp_start: int,
) -> int | None:
    """Read the BFSK register that follows the chirp."""
    if not spec.carries_id:
        return None
    bit_count = int(round(spec.bit_seconds * sample_rate))
    if bit_count <= 0:
        return None
    cursor = chirp_start + int(round(spec.chirp_seconds * sample_rate)) + int(
        round(spec.gap_seconds * sample_rate)
    )
    value = 0
    for _ in range(spec.id_bits):
        segment = captured[cursor : cursor + bit_count]
        if len(segment) < bit_count:
            return None
        windowed = segment * np.hanning(len(segment))
        spectrum = np.abs(np.fft.rfft(windowed))
        freqs = np.fft.rfftfreq(len(segment), 1.0 / sample_rate)

        def energy_at(target: float) -> float:
            band = (freqs > target - 120) & (freqs < target + 120)
            return float(spectrum[band].sum()) if band.any() else 0.0

        value = (value << 1) | (1 if energy_at(spec.bit_high_hz) > energy_at(spec.bit_low_hz) else 0)
        cursor += bit_count
    return value


def find_marker(
    captured: np.ndarray,
    sample_rate: int,
    spec: SyncMarkerSpec | None = None,
    *,
    search_seconds: float = 12.0,
    min_confidence: float = 0.25,
) -> MarkerDetection:
    """Locate the marker in a captured recording.

    Returns the offset of the *content* — the first sample after the marker —
    so a caller can trim straight to the audio the reference transcript covers.
    """
    spec = spec or SyncMarkerSpec()
    captured = np.asarray(captured, dtype=np.float64)
    if captured.size == 0:
        return MarkerDetection(False, 0, 0.0, 0.0, None)

    limit = min(len(captured), int(round(search_seconds * sample_rate)))
    haystack = captured[:limit]
    template = generate_chirp(spec, sample_rate)

    scores = _normalized_correlation(haystack, template)
    if scores.size == 0:
        return MarkerDetection(False, 0, 0.0, 0.0, None)

    peak = int(np.argmax(scores))
    confidence = float(np.clip(scores[peak], 0.0, 1.0))
    if confidence < min_confidence:
        return MarkerDetection(False, 0, 0.0, confidence, None)

    content_start = peak + int(round(spec.total_seconds() * sample_rate))
    marker_id = _decode_id(captured, sample_rate, spec, peak)
    return MarkerDetection(
        found=True,
        offset_samples=content_start,
        offset_seconds=content_start / float(sample_rate),
        confidence=confidence,
        marker_id=marker_id,
    )


def align_capture(
    captured: np.ndarray,
    sample_rate: int,
    spec: SyncMarkerSpec | None = None,
    **kwargs,
) -> tuple[np.ndarray, MarkerDetection]:
    """Trim a capture to the content the reference transcript describes.

    When no marker is found the audio is returned untouched, with the detection
    reporting `found=False`. Silently guessing an offset would be worse than
    admitting the capture cannot be aligned.
    """
    detection = find_marker(captured, sample_rate, spec, **kwargs)
    if not detection.found:
        return np.asarray(captured, dtype=np.float64), detection
    return np.asarray(captured, dtype=np.float64)[detection.offset_samples :], detection


def find_reference_offset(
    captured: np.ndarray,
    reference: np.ndarray,
    sample_rate: int,
    *,
    search_seconds: float = 12.0,
    min_confidence: float = 0.15,
    decimate: int = 4,
) -> MarkerDetection:
    """Align a capture that has no marker, using the audio that was played.

    Every capture already taken predates the marker, but the rig knows what it
    played, so the played audio works as the template. Correlation is done on a
    decimated copy for speed and then refined at full rate around the peak.

    Confidence runs lower than for a chirp — room response and noise colour the
    capture, so it is never an exact copy of what was played — hence the lower
    default threshold.
    """
    captured = np.asarray(captured, dtype=np.float64)
    reference = np.asarray(reference, dtype=np.float64)
    if captured.size == 0 or reference.size == 0:
        return MarkerDetection(False, 0, 0.0, 0.0, None)

    limit = min(len(captured), int(round(search_seconds * sample_rate)) + len(reference))
    haystack = captured[:limit]

    # A few seconds of the reference is plenty to localise, and keeps the
    # correlation cheap on long clips.
    template = reference[: int(round(4.0 * sample_rate))]
    step = max(int(decimate), 1)
    coarse = _normalized_correlation(haystack[::step], template[::step])
    if coarse.size == 0:
        return MarkerDetection(False, 0, 0.0, 0.0, None)

    coarse_peak = int(np.argmax(coarse)) * step
    span = step * 4
    lo = max(coarse_peak - span, 0)
    hi = min(coarse_peak + span + len(template), len(haystack))
    fine = _normalized_correlation(haystack[lo:hi], template)
    if fine.size == 0:
        return MarkerDetection(False, 0, 0.0, float(np.clip(coarse.max(), 0, 1)), None)

    peak = lo + int(np.argmax(fine))
    confidence = float(np.clip(fine.max(), 0.0, 1.0))
    if confidence < min_confidence:
        return MarkerDetection(False, 0, 0.0, confidence, None)
    return MarkerDetection(
        found=True,
        offset_samples=peak,
        offset_seconds=peak / float(sample_rate),
        confidence=confidence,
        marker_id=None,
    )
