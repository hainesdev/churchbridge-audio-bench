from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from controller.models import BenchmarkDFN3TuningConfig, BenchmarkSTTConfig
from controller.run_session import (
    _build_prepared_runs,
    _resolved_dfn3_tunings,
    _resolved_stt_configs,
    _resolved_variants,
)
from controller.scenarios import BenchmarkScenarioFixture


class ControllerRunSessionTests(unittest.TestCase):
    def test_resolves_named_dfn3_tuning_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "dfn3-tunings.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "tunings": [
                            {
                                "label": "speech_guard_light",
                                "profile": "subtle",
                                "wet_mix": 0.15,
                                "loudness_compensation": 1.0,
                                "max_compensation_gain": 3.0,
                            },
                            {
                                "label": "speech_guard_medium",
                                "profile": "subtle",
                                "wet_mix": 0.25,
                                "loudness_compensation": 0.95,
                                "post_gain_db": 0.5,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            args = Namespace(
                dfn3_tuning_file=[str(manifest_path)],
                dfn3_profiles=["subtle"],
                dfn3_wet_mix=None,
                dfn3_loudness_compensation=None,
                dfn3_max_compensation_gain=None,
                dfn3_post_gain_db=None,
                dfn3_peak_limit=None,
            )

            tunings = _resolved_dfn3_tunings(args, repo_root=manifest_path.parent)

        self.assertEqual([tuning.label for tuning in tunings], ["speech_guard_light", "speech_guard_medium"])
        self.assertEqual([tuning.slug() for tuning in tunings], ["speech_guard_light", "speech_guard_medium"])
        self.assertAlmostEqual(tunings[0].wet_mix or 0.0, 0.15, places=4)
        self.assertAlmostEqual(tunings[1].post_gain_db or 0.0, 0.5, places=4)

    def test_raw_compare_is_prepended_once(self) -> None:
        args = Namespace(
            variants=["apple_aec_plus_deepfilternet3", "raw_debug", "apple_aec_only"],
            include_raw_compare=True,
        )

        self.assertEqual(
            _resolved_variants(args),
            ["raw_debug", "apple_aec_plus_deepfilternet3", "apple_aec_only"],
        )

    def test_builds_raw_compare_once_and_dfn3_runs_for_each_tuning(self) -> None:
        scenario = BenchmarkScenarioFixture(
            scenario_id="sermon-clip-1",
            expected_transcript="predica de prueba",
            run_seconds=8.0,
            display_seconds=10.0,
        )
        tunings = [
            BenchmarkDFN3TuningConfig(label="speech_guard_light", profile="subtle", wet_mix=0.15),
            BenchmarkDFN3TuningConfig(label="speech_guard_medium", profile="subtle", wet_mix=0.25),
        ]

        prepared_runs = _build_prepared_runs(
            benchmark_session_id="session-voicechat-dfn3",
            scenarios=[scenario],
            variants=["raw_debug", "apple_aec_plus_deepfilternet3"],
            stt_configs=[BenchmarkSTTConfig(model="chirp_3")],
            mic_profiles=["auto"],
            dfn3_tunings=tunings,
            annotate_stt_model=False,
            annotate_mic_profile=False,
            annotate_dfn3_tuning=True,
            default_run_seconds=5.0,
            default_display_seconds=5.0,
            save_server_capture=True,
        )

        self.assertEqual(len(prepared_runs), 3)
        self.assertEqual([run.run_spec.pipeline_id for run in prepared_runs], ["raw_debug", "apple_aec_plus_deepfilternet3", "apple_aec_plus_deepfilternet3"])
        self.assertIsNone(prepared_runs[0].run_spec.dfn3_tuning)
        self.assertEqual(prepared_runs[1].run_spec.dfn3_tuning.label, "speech_guard_light")
        self.assertEqual(prepared_runs[2].run_spec.dfn3_tuning.label, "speech_guard_medium")
        self.assertIn("dfn3-speech_guard_light", prepared_runs[1].run_spec.run_id)
        self.assertIn("dfn3-speech_guard_medium", prepared_runs[2].run_spec.run_id)

    def test_resolves_stt_model_sweep(self) -> None:
        args = Namespace(stt_models=["chirp_3", "nova-3"])

        stt_configs = _resolved_stt_configs(args)

        self.assertEqual([config.model for config in stt_configs], ["chirp_3", "nova-3"])
        self.assertEqual(stt_configs[0].language_codes, stt_configs[1].language_codes)

    def test_builds_run_ids_with_stt_model_when_sweeping(self) -> None:
        scenario = BenchmarkScenarioFixture(
            scenario_id="sermon-clip-1",
            expected_transcript="predica de prueba",
        )

        prepared_runs = _build_prepared_runs(
            benchmark_session_id="session-stt-compare",
            scenarios=[scenario],
            variants=["apple_aec_plus_deepfilternet3"],
            stt_configs=[BenchmarkSTTConfig(model="chirp_3"), BenchmarkSTTConfig(model="nova-3")],
            mic_profiles=["auto"],
            dfn3_tunings=[BenchmarkDFN3TuningConfig(profile="subtle")],
            annotate_stt_model=True,
            annotate_mic_profile=False,
            annotate_dfn3_tuning=False,
            default_run_seconds=5.0,
            default_display_seconds=5.0,
            save_server_capture=True,
        )

        self.assertEqual([run.run_spec.stt_config.model for run in prepared_runs], ["chirp_3", "nova-3"])
        self.assertIn("stt-chirp_3", prepared_runs[0].run_spec.run_id)
        self.assertIn("stt-nova-3", prepared_runs[1].run_spec.run_id)
        self.assertEqual(prepared_runs[1].run_spec.server_capture_label, "sermon-clip-1-apple_aec_plus_deepfilternet3-stt-nova-3")


if __name__ == "__main__":
    unittest.main()
