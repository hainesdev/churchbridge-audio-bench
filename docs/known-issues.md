# Known issues and open work

What is wrong with this rig right now, in the order it affects the numbers.
Anything listed here is a reason not to quote a figure from a session run
before the date it was fixed.

## 1. Capture alignment — *fix landed, not yet validated on hardware*

**The problem.** The controller starts playback on a timer that begins when the
run command is dispatched. The phone starts its capture engine when it receives
that command. Those two events are separated by an unmeasured amount — the
engine spinning up, the audio session activating, the socket connecting — and
nothing in the rig ever knew what it was.

Word error rate is acutely sensitive to it. If the head of the clip plays before
the phone is capturing, the opening words are simply never recorded, and the
scorer counts them as deletions. The symptom is visible in the retained runs:
reference `"que no puedes dormir..."` scored against
`"no puedes dormir..."` — the recognizer did not mishear anything, it was
handed audio that started late.

**The fix.** `controller/alignment.py` prepends an acoustic sync marker to
whatever is played — the clapperboard, in software. It is:

- a **300 ms linear chirp**, 800 Hz → 4 kHz, found by matched filter. A sweep is
  used rather than a click because correlation integrates over the whole sweep,
  so it stays sharp under noise and room reverb where a transient smears.
- an **8-bit BFSK register** (1200/2400 Hz) carrying the run number, so a capture
  found on disk later proves which run produced it instead of relying on a
  filename.
- everything below 4 kHz, comfortably under the 8 kHz Nyquist limit of the
  16 kHz stream, so it survives the pipeline's downsampling.

Measured against synthetic captures: sample-exact at −5 dB SNR, correct through
48 → 16 kHz decimation, ID round-trips, and it reports `found=False` rather than
guessing on unmarked audio. On by default; `--no-sync-marker` disables it.

**The subtlety that nearly reintroduced the bug.** The marker lengthens the
played asset by ~0.92 s. `computed_run_seconds` budgets
`clip + lead_in + tail`, so on the first wiring the marker was silently spent
out of the 1.0 s tail margin — which would have clipped the *end* of every clip,
the same truncation from the other direction. The capture window now grows with
the marker (`extra_lead_seconds`), and there is a regression test asserting the
tail margin survives.

**Still open:**

- [ ] Run a session on hardware and confirm the marker is found in the returned
      capture. Until that happens the offset is measured in tests, not in a room.
- [ ] Confirm the recognizer does not transcribe the marker itself. A chirp and
      tone pair should not produce tokens, but "should not" is not a measurement,
      and a spurious token at the head of every run would inflate WER as an
      insertion. The 200 ms lead-out exists to keep it clear of speech.
- [ ] Feed the measured offset into scoring — right now the marker is recorded in
      the run artifact but nothing consumes it. Trimming the capture to the
      marker before scoring is the point of the exercise.

## 2. The wet/dry mix is set by ear, not by measurement

The shipped value is **25% wet / 75% dry**. It was chosen by listening to
captured audio, because the WER numbers could not be trusted for the reason
above.

That much is defensible — 100% wet was measurably quieter and materially worse,
with 8–48% of output samples coming back as exact digital silence and the
recognizer returning nothing at all in the worst runs. The gross comparison
survives any plausible alignment error.

What does **not** survive it is the fine comparison. Whether 0.25 beats 0.30 or
0.35 is unresolved, and no sweep result should be quoted until §1 is validated
and the sweep is re-run.

- [ ] Re-run the wet-mix sweep with markers, on identical room conditions.
- [ ] Score against marker-trimmed captures rather than raw ones.

## 3. Historical captures predate the marker

Roughly 340 runs are already on disk under
`churchbridge-ai/tests/audio/captured/benchmarks/`, and none of them carry a
marker.

`find_reference_offset` handles this: it correlates the capture against the
audio the rig knows it played, coarse pass on a decimated copy and then refined
at full rate. Confidence runs lower than for a chirp — the room colours the
capture, so it is never an exact copy of what was played — hence a lower
threshold.

- [ ] Batch-align the retained captures and re-score, so the historical sessions
      become comparable to post-marker ones instead of being written off.

## 4. Server-side resampling has no anti-aliasing filter

`resample_float32_to_pcm16` (and its counterpart in `churchbridge-ai`)
resamples by linear interpolation with no pre-filter, so content above 8 kHz
folds back into the speech band on the way down to 16 kHz. Modest for speech,
cheap to fix, and it sits upstream of every recognition result in every run.

- [ ] Add a low-pass before decimation.

## 5. `pytest tests/server` fails silently

Reported for completeness: the server test invocation exits without running
anything useful and without saying why. It is not blocking bench work, but it
means a green run there proves nothing.

- [ ] Diagnose the collection failure.

## Not issues

Two things that look like gaps and are not, recorded so they stop being
re-investigated:

- **The 48 kHz requirement is enforced, not assumed.** The DFN3 processor guards
  its input rate and returns audio unenhanced with a surfaced warning naming the
  rate it actually received.
- **Fallback capture paths are chosen by capability and reported.** A path that
  degrades silently would be the defect; this one names the reason it fell back.
