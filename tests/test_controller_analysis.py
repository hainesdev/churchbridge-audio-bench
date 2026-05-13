from __future__ import annotations

import unittest

from controller.analysis import derive_run_analysis
from controller.models import BenchmarkRunSpec, DisplayEvent, TelemetryEvent


class ControllerAnalysisTests(unittest.TestCase):
    def test_derives_transcript_latency_and_route_metrics(self) -> None:
        run_spec = BenchmarkRunSpec(
            benchmark_session_id="session-1",
            run_id="run-1",
            scenario_id="scenario-1",
            pipeline_id="apple_aec_plus_deepfilternet3",
            expected_transcript="No puedes dormir en la noche la biblia te va a hablar",
            mic_profile="front_cardioid",
        )

        display_events = [
            DisplayEvent(
                event_type="stt_partial",
                payload={"type": "stt_partial", "text": "No puedes dormir", "ts": 1_000_000_002_000},
                received_at="2026-05-13T13:00:02Z",
            ),
            DisplayEvent(
                event_type="stt_final",
                payload={"type": "stt_final", "text": "No puedes dormir en la noche.", "ts": 1_000_000_004_000},
                received_at="2026-05-13T13:00:04Z",
            ),
            DisplayEvent(
                event_type="stt_final",
                payload={"type": "stt_final", "text": "La biblia te va a hablar.", "ts": 1_000_000_006_000},
                received_at="2026-05-13T13:00:06Z",
            ),
        ]

        telemetry_events = [
            TelemetryEvent(
                run_id="run-1",
                snapshot={
                    "routeUsesBuiltInMic": True,
                    "selectedInputPort": "MicrophoneBuiltIn | iPhone Microphone",
                    "selectedDataSource": "Front",
                    "selectedPolarPattern": "cardioid",
                    "captureRestartCount": 1,
                    "appliedMicProfile": "Front Cardioid",
                    "dfn3Profile": "Subtle",
                    "dfn3WetMix": 0.35,
                    "dfn3LoudnessCompensation": 0.85,
                    "dfn3PostGainDB": 0.0,
                },
            )
        ]

        playback = {
            "started_at": "2001-09-09T01:46:40Z",
            "playback_delay_ms": 1000,
        }

        analysis = derive_run_analysis(
            run_spec=run_spec,
            display_events=display_events,
            playback=playback,
            telemetry_events=telemetry_events,
        )

        self.assertEqual(
            analysis.final_transcript,
            "No puedes dormir en la noche. La biblia te va a hablar.",
        )
        self.assertEqual(analysis.final_segment_count, 2)
        self.assertEqual(analysis.first_partial_latency_ms, 1000)
        self.assertEqual(analysis.first_final_latency_ms, 3000)
        self.assertAlmostEqual(analysis.wer or 0.0, 0.0, places=4)
        self.assertTrue((analysis.transcript_similarity or 0.0) > 0.95)
        self.assertTrue(analysis.route_uses_built_in_mic)
        self.assertEqual(analysis.selected_data_source, "Front")
        self.assertEqual(analysis.capture_restart_count_max, 1)
        self.assertEqual(analysis.dfn3_profile, "Subtle")


if __name__ == "__main__":
    unittest.main()
