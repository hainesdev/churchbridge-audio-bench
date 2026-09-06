"""Tests for capture/reference alignment.

Run with:  python -m pytest tests/test_alignment.py -v
"""

import numpy as np
import pytest

from controller.alignment import (
    MarkerDetection,
    SyncMarkerSpec,
    align_capture,
    find_marker,
    find_reference_offset,
    generate_marker,
    prepend_marker,
)


SR = 48000


def _speech_like(seconds: float = 6.0, sample_rate: int = SR, seed: int = 5) -> np.ndarray:
    """Bursts separated by gaps — enough structure to stand in for speech.

    Pitch comes from the seed as well as the burst pattern, so two different
    seeds are genuinely different audio rather than one envelope over a shared
    carrier.
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * sample_rate)
    t = np.arange(n) / sample_rate
    fundamental = rng.uniform(110, 240)
    carrier = np.sin(2 * np.pi * fundamental * t) + 0.5 * np.sin(
        2 * np.pi * fundamental * rng.uniform(2.1, 3.4) * t
    )
    envelope = np.zeros(n)
    cursor = int(0.2 * sample_rate)
    while cursor < n - int(0.3 * sample_rate):
        length = int(rng.uniform(0.25, 0.6) * sample_rate)
        length = min(length, n - cursor)
        if length <= 1:
            break
        envelope[cursor : cursor + length] = np.hanning(length)
        cursor += length + int(rng.uniform(0.1, 0.3) * sample_rate)
    return (carrier * envelope * 0.6).astype(np.float64)


def _add_noise(signal: np.ndarray, snr_db: float, sample_rate: int = SR, seed: int = 9) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(len(signal)) / sample_rate
    # broadband plus a low hum, roughly what a box fan contributes
    noise = rng.normal(0, 1, len(signal)) + 3 * np.sin(2 * np.pi * 120 * t)
    noise *= np.sqrt(np.mean(signal**2)) / np.sqrt(np.mean(noise**2)) / (10 ** (snr_db / 20))
    return signal + noise


def _capture(content: np.ndarray, lead_seconds: float, sample_rate: int = SR) -> np.ndarray:
    return np.concatenate([np.zeros(int(lead_seconds * sample_rate)), content])


# ---------------------------------------------------------------------------
# Marker geometry
# ---------------------------------------------------------------------------

def test_marker_length_matches_spec():
    spec = SyncMarkerSpec()
    marker = generate_marker(spec, SR, 0)
    assert len(marker) == pytest.approx(spec.total_seconds() * SR, rel=0.01)


def test_marker_stays_below_the_16k_nyquist_limit():
    """The marker has to survive the pipeline's downsample to 16 kHz."""
    spec = SyncMarkerSpec()
    assert spec.chirp_end_hz < 8000
    assert spec.bit_high_hz < 8000


def test_prepend_marker_scales_to_content_peak():
    content = _speech_like(2.0)
    played = prepend_marker(content, SR, SyncMarkerSpec(), 1)
    assert np.max(np.abs(played)) <= max(np.max(np.abs(content)), 1e-9) * 1.001


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lead", [0.0, 0.75, 2.5])
def test_finds_marker_at_any_lead(lead):
    spec = SyncMarkerSpec()
    captured = _capture(prepend_marker(_speech_like(), SR, spec, 3), lead)
    found = find_marker(captured, SR, spec)
    assert found.found
    assert found.offset_seconds == pytest.approx(lead + spec.total_seconds(), abs=0.01)


@pytest.mark.parametrize("snr_db", [20, 10, 0, -5])
def test_survives_noise_down_to_negative_snr(snr_db):
    """The sweep integrates over 300 ms, so it outlasts a loud room."""
    spec = SyncMarkerSpec()
    captured = _add_noise(_capture(prepend_marker(_speech_like(), SR, spec, 3), 1.2), snr_db)
    found = find_marker(captured, SR, spec)
    assert found.found
    assert found.offset_seconds == pytest.approx(1.2 + spec.total_seconds(), abs=0.02)


def test_survives_downsampling_to_16k():
    spec = SyncMarkerSpec()
    captured = _add_noise(_capture(prepend_marker(_speech_like(), SR, spec, 3), 1.0), 12)
    decimated = captured[::3]
    found = find_marker(decimated, SR // 3, spec)
    assert found.found
    assert found.offset_seconds == pytest.approx(1.0 + spec.total_seconds(), abs=0.02)


@pytest.mark.parametrize("marker_id", [0, 1, 42, 200, 255])
def test_id_register_round_trips(marker_id):
    spec = SyncMarkerSpec()
    captured = _add_noise(_capture(prepend_marker(_speech_like(), SR, spec, marker_id), 0.9), 15)
    assert find_marker(captured, SR, spec).marker_id == marker_id


def test_reports_not_found_rather_than_guessing():
    """An unmarked capture must fail loudly, not return a plausible offset."""
    found = find_marker(_speech_like(), SR, SyncMarkerSpec())
    assert not found.found
    assert found.offset_samples == 0


def test_empty_capture_is_handled():
    assert find_marker(np.zeros(0), SR) == MarkerDetection(False, 0, 0.0, 0.0, None)


# ---------------------------------------------------------------------------
# Trimming
# ---------------------------------------------------------------------------

def test_align_capture_trims_to_content():
    spec = SyncMarkerSpec()
    content = _speech_like(5.0)
    trimmed, found = align_capture(_capture(prepend_marker(content, SR, spec, 8), 0.6), SR, spec)
    assert found.found
    assert len(trimmed) == pytest.approx(len(content), abs=0.02 * SR)


def test_align_capture_returns_audio_untouched_when_unmarked():
    content = _speech_like(3.0)
    trimmed, found = align_capture(content, SR)
    assert not found.found
    assert len(trimmed) == len(content)


# ---------------------------------------------------------------------------
# Marker-free fallback, for captures taken before the marker existed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lead,snr_db", [(1.3, None), (2.75, 15), (0.6, 6)])
def test_reference_correlation_recovers_offset(lead, snr_db):
    reference = _speech_like(6.0)
    captured = _capture(reference, lead)
    if snr_db is not None:
        captured = _add_noise(captured, snr_db)
    found = find_reference_offset(captured, reference, SR)
    assert found.found
    assert found.offset_seconds == pytest.approx(lead, abs=0.02)


def test_reference_correlation_declines_unrelated_audio():
    reference = _speech_like(5.0, seed=1)
    unrelated = _speech_like(5.0, seed=99)
    assert not find_reference_offset(unrelated, reference, SR).found


# ---------------------------------------------------------------------------
# The capture window has to grow to hold the marker
# ---------------------------------------------------------------------------

def test_marker_does_not_eat_the_tail_margin():
    """The marker lengthens the played asset, so the run has to lengthen too.

    Budgeting it out of the existing tail margin would clip the end of the clip
    — the exact truncation the marker exists to detect.
    """
    from controller.scenarios import BenchmarkScenarioFixture

    spec = SyncMarkerSpec()
    scenario = BenchmarkScenarioFixture(
        scenario_id="s",
        expected_transcript="t",
        playback_duration_seconds=13.41,
    )
    plain = scenario.computed_run_seconds(minimum_seconds=5.0)
    marked = scenario.computed_run_seconds(
        minimum_seconds=5.0, extra_lead_seconds=spec.total_seconds()
    )
    assert marked == pytest.approx(plain + spec.total_seconds(), abs=1e-6)

    # The clip still finishes inside the window with the tail margin intact.
    consumed = (
        scenario.playback_lead_in_seconds
        + spec.total_seconds()
        + scenario.playback_duration_seconds
    )
    assert marked - consumed == pytest.approx(scenario.playback_tail_seconds, abs=1e-6)


def test_display_window_also_grows_with_the_marker():
    from controller.scenarios import BenchmarkScenarioFixture

    spec = SyncMarkerSpec()
    scenario = BenchmarkScenarioFixture(
        scenario_id="s", expected_transcript="t", playback_duration_seconds=13.41
    )
    plain = scenario.computed_display_seconds(minimum_seconds=5.0)
    marked = scenario.computed_display_seconds(
        minimum_seconds=5.0, extra_lead_seconds=spec.total_seconds()
    )
    assert marked > plain
