from __future__ import annotations

import base64

import numpy as np


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
