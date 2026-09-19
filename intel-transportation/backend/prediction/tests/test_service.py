import unittest
from pathlib import Path
import json
import tempfile
from datetime import date

import numpy as np

from backend.prediction.service import PredictionService, _moving_average, _normalise_batch
from backend.prediction.contracts import PredictRequest


class PredictionServiceTests(unittest.TestCase):
    def test_api_defaults_can_follow_loaded_model_metadata(self):
        payload = PredictRequest(features=[[1, 2, 3, 4, 5, 6]])
        self.assertIsNone(payload.future_steps)
        self.assertIsNone(payload.time_step_seconds)

    def test_moving_average_preserves_sequence_length(self):
        result = _moving_average(np.array([1, 2, 3, 4, 5, 6], dtype=np.float32), 5)
        self.assertEqual(result.shape, (6,))
        self.assertAlmostEqual(float(result[-1]), 4.0, places=4)

    def test_input_normalisation_supports_teaching_shapes(self):
        self.assertEqual(_normalise_batch([[1, 2, 3, 4, 5]]).shape, (1, 5, 1))
        self.assertEqual(_normalise_batch([[1], [2], [3], [4], [5]]).shape, (1, 5, 1))
        self.assertEqual(_normalise_batch([[[1], [2], [3], [4], [5]]]).shape, (1, 5, 1))

    def test_input_normalisation_supports_multivariate_time_series(self):
        sequence = [
            [100, 8, 0, 0],
            [120, 9, 0, 0],
            [130, 10, 0, 0],
        ]
        self.assertEqual(_normalise_batch(sequence, feature_count=4).shape, (1, 3, 4))
        self.assertEqual(
            _normalise_batch([sequence, sequence], feature_count=4).shape,
            (2, 3, 4),
        )

    def test_native_fallback_returns_requested_steps(self):
        service = PredictionService(Path("missing-model.onnx"))
        service.load()
        result = service.predict([[10, 11, 12, 13, 14, 15]], future_steps=4, moving_average_window=5)
        self.assertEqual(len(result.forecast), 4)
        self.assertGreaterEqual(result.current_flow, 0)
        self.assertEqual(result.backend, "numpy-fallback")

    def test_onnx_batch_returns_all_sequences_when_model_is_available(self):
        model_path = Path("backend/prediction/models/location1_trend_v1.onnx")
        if not model_path.exists():
            self.skipTest("ONNX model has not been exported")
        service = PredictionService(model_path)
        service.load()
        result = service.predict([[1, 2, 3, 4, 5, 6], [6, 5, 4, 3, 2, 1]], future_steps=3)
        self.assertEqual(len(result.current_flows), 2)
        self.assertEqual(len(result.forecasts), 2)
        self.assertEqual(len(result.forecasts[0]), 3)

    def test_lstm_metadata_enforces_history_bin_and_target_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "candidate.onnx"
            model_path.with_suffix(".json").write_text(
                json.dumps(
                    {
                        "model": "candidate",
                        "model_type": "lstm",
                        "mean": 10,
                        "std": 2,
                        "history_steps": 5,
                        "forecast_steps": 4,
                        "bin_seconds": 60,
                        "target_column": "entering_vehicle_count",
                    }
                ),
                encoding="utf-8",
            )
            service = PredictionService(model_path)
            service.load()
            with self.assertRaisesRegex(ValueError, "需要 5 个历史点"):
                service.predict([[1, 2, 3, 4]], future_steps=2)
            with self.assertRaisesRegex(ValueError, "使用 60 秒粒度"):
                service.predict(
                    [[1, 2, 3, 4, 5]],
                    future_steps=2,
                    time_step_seconds=900,
                )
            with self.assertRaisesRegex(ValueError, "entering_vehicle_count"):
                service.predict(
                    [[1, 2, 3, 4, 5]],
                    future_steps=2,
                    time_step_seconds=60,
                    target_column="vehicle_count",
                )

    def test_multivariate_metadata_loads_feature_scaler_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "teacher.onnx"
            model_path.with_suffix(".json").write_text(
                json.dumps(
                    {
                        "model": "teacher",
                        "model_type": "lstm",
                        "history_steps": 6,
                        "forecast_steps": 1,
                        "bin_seconds": 3600,
                        "target_column": "vehicle_count",
                        "feature_columns": [
                            "vehicle_count",
                            "hour",
                            "day_of_week",
                            "is_holiday",
                        ],
                        "feature_means": [1000, 12, 3, 0.05],
                        "feature_stds": [200, 6, 2, 0.2],
                        "target_mean": 1000,
                        "target_std": 200,
                    }
                ),
                encoding="utf-8",
            )
            service = PredictionService(model_path)
            service.load()
            self.assertEqual(len(service.feature_columns), 4)
            self.assertEqual(service.info()["feature_columns"][0], "vehicle_count")
            with self.assertRaisesRegex(ValueError, "shape"):
                service.predict([[100, 110, 120, 130, 140, 150]], future_steps=1)

    def test_china_inference_calendar_supports_non_contiguous_years(self):
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "china.onnx"
            model_path.with_suffix(".json").write_text(
                json.dumps(
                    {
                        "model": "china",
                        "model_type": "lstm",
                        "history_steps": 6,
                        "forecast_steps": 1,
                        "bin_seconds": 1200,
                        "target_column": "vehicle_count",
                        "feature_columns": [
                            "vehicle_count",
                            "hour",
                            "minute",
                            "day_of_week",
                            "is_holiday",
                            "is_makeup_workday",
                        ],
                        "feature_offsets": [0, 0, 0, 0, 0, 0],
                        "feature_scales": [1, 1, 1, 1, 1, 1],
                        "holiday_policy": "china_statutory_with_makeup",
                        "inference_calendar_periods": [
                            {
                                "valid_from": "2026-01-01",
                                "valid_to": "2026-12-31",
                                "source_url": "https://www.gov.cn/example",
                                "holiday_dates": ["2026-10-01"],
                                "makeup_workdays": ["2026-09-20"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            service = PredictionService(model_path)
            service.load()

            self.assertEqual(
                service.calendar_feature_values(date(2026, 9, 20)),
                {"is_holiday": 0.0, "is_makeup_workday": 1.0},
            )
            self.assertEqual(
                service.calendar_feature_values(date(2026, 10, 1)),
                {"is_holiday": 1.0, "is_makeup_workday": 0.0},
            )
            with self.assertRaisesRegex(ValueError, "不覆盖日期"):
                service.calendar_feature_values(date(2025, 12, 31))


if __name__ == "__main__":
    unittest.main()
