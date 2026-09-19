import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from backend.prediction.service import PredictionService
from backend.prediction.train_lstm import (
    _config_from_args,
    _parse_args,
    china_kdd2017_config,
    teacher_compat_config,
    train_model,
)


class TeacherTrainingTests(unittest.TestCase):
    def test_china_profile_defaults_include_timezone_and_makeup_feature(self):
        config = china_kdd2017_config()

        self.assertEqual(config.bin_seconds, 1200)
        self.assertEqual(config.timezone, "Asia/Shanghai")
        self.assertEqual(config.holiday_policy, "china_statutory_with_makeup")
        self.assertIn("minute", config.feature_columns)
        self.assertIn("is_makeup_workday", config.feature_columns)
        self.assertIn("2016-10-08", config.makeup_workdays)

    def test_teacher_profile_defaults_match_documented_contract(self):
        config = teacher_compat_config()

        self.assertEqual(config.history_steps, 6)
        self.assertEqual(config.forecast_steps, 1)
        self.assertEqual(config.bin_seconds, 3600)
        self.assertEqual(config.hidden_sizes, (64, 32))
        self.assertEqual(config.feature_columns, (
            "vehicle_count",
            "hour",
            "day_of_week",
            "is_holiday",
        ))
        self.assertEqual(config.scaler_type, "minmax")

    def test_cli_applies_teacher_defaults_and_explicit_overrides(self):
        args = _parse_args([
            "flow.csv",
            "--profile",
            "teacher_compat",
            "--epochs",
            "3",
        ])

        config = _config_from_args(args)

        self.assertEqual(config.history_steps, 6)
        self.assertEqual(config.epochs, 3)
        self.assertEqual(config.validation_ratio, 0.15)

    def test_teacher_training_writes_reusable_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_path = root / "teacher.csv"
            output_path = root / "teacher_lstm.onnx"
            timestamps = pd.date_range("2026-01-01", periods=96, freq="h")
            pd.DataFrame(
                {
                    "timestamp": timestamps,
                    "series_id": "fixture",
                    "vehicle_count": [100 + (index % 24) * 5 for index in range(96)],
                    "is_holiday": [int(value.dayofweek >= 5) for value in timestamps],
                }
            ).to_csv(data_path, index=False)
            config = teacher_compat_config(
                epochs=2,
                batch_size=16,
                minimum_training_windows=1,
                early_stopping_patience=2,
            )

            metadata = train_model([data_path], output_path, config)

            self.assertTrue(output_path.is_file())
            self.assertTrue(output_path.with_suffix(".pt").is_file())
            self.assertTrue(output_path.with_suffix(".json").is_file())
            self.assertTrue(root.joinpath("teacher_lstm_scaler.json").is_file())
            artifact_dir = root / "teacher_lstm_artifacts"
            self.assertTrue(artifact_dir.joinpath("training_log.csv").is_file())
            self.assertTrue(artifact_dir.joinpath("metrics_summary.json").is_file())
            self.assertTrue(artifact_dir.joinpath("per_series_metrics.json").is_file())
            self.assertTrue(artifact_dir.joinpath("test_model", "predictions.csv").is_file())
            self.assertEqual(metadata["profile"], "teacher_compat")
            self.assertEqual(metadata["test_windows"], 9)
            persisted = json.loads(output_path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["feature_columns"][0], "vehicle_count")
            self.assertEqual(persisted["split_strategy"], "global_chronological")
            self.assertFalse(persisted["output_includes_current"])
            service = PredictionService(output_path)
            service.load()
            result = service.predict(
                [
                    [100, 0, 3, 0],
                    [105, 1, 3, 0],
                    [110, 2, 3, 0],
                    [115, 3, 3, 0],
                    [120, 4, 3, 0],
                    [125, 5, 3, 0],
                ],
                future_steps=1,
                time_step_seconds=3600,
                target_column="vehicle_count",
            )
            self.assertEqual(result.current_flow, 125.0)
            self.assertEqual(len(result.forecast), 1)
            self.assertEqual(result.backend, "onnxruntime-cpu")


if __name__ == "__main__":
    unittest.main()
