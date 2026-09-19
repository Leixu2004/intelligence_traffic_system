import unittest
from unittest.mock import patch
from datetime import date
from pathlib import Path

import pandas as pd

from backend.dashboard_api import _checkpoint_features, _checkpoint_model_features
from backend.prediction.service import PredictionService


class CheckpointFeatureTests(unittest.TestCase):
    def test_builds_china_makeup_workday_features(self):
        records = [
            {
                "time": timestamp.isoformat(),
                "checkpoint_id": "CP-1",
                "vehicle_count": 30 + index,
            }
            for index, timestamp in enumerate(
                pd.date_range("2016-10-07T16:00:00Z", periods=6, freq="20min")
            )
        ]
        service = PredictionService(Path("missing.onnx"))
        service.history_steps = 6
        service.bin_seconds = 1200
        service.timezone = "Asia/Shanghai"
        service.holiday_policy = "china_statutory_with_makeup"
        service.holiday_calendar_valid_from = date(2016, 9, 19)
        service.holiday_calendar_valid_to = date(2016, 10, 17)
        service.holiday_dates = {f"2016-10-{day:02d}" for day in range(1, 8)}
        service.makeup_workdays = {"2016-10-08", "2016-10-09"}
        service.feature_columns = [
            "vehicle_count",
            "hour",
            "minute",
            "day_of_week",
            "is_holiday",
            "is_makeup_workday",
        ]

        with patch("backend.dashboard_api._traffic_records", return_value=(records, "test", "")):
            features = _checkpoint_model_features("CP-1", service, 20)

        self.assertEqual(features[0], [30.0, 0.0, 0.0, 5.0, 0.0, 1.0])
        with self.assertRaisesRegex(ValueError, "不覆盖日期"):
            service.calendar_feature_values(date(2026, 9, 11))

    def test_builds_complete_minute_series_and_keeps_aggregate_counts(self):
        records = [
            {"time": "2026-09-11T00:00:00Z", "checkpoint_id": "CP-1", "vehicle_count": 10},
            {"time": "2026-09-11T00:02:00Z", "checkpoint_id": "CP-1", "vehicle_count": 30},
        ]
        with patch("backend.dashboard_api._traffic_records", return_value=(records, "test", "")):
            self.assertEqual(_checkpoint_features("CP-1", 20), [10.0, 0.0, 30.0])

    def test_split_on_gap_requires_a_contiguous_recent_history(self):
        records = [
            {"time": "2026-09-11T00:00:00Z", "checkpoint_id": "CP-1", "vehicle_count": 10},
            {"time": "2026-09-11T00:40:00Z", "checkpoint_id": "CP-1", "vehicle_count": 30},
            {"time": "2026-09-11T01:00:00Z", "checkpoint_id": "CP-1", "vehicle_count": 40},
        ]
        service = PredictionService(Path("missing.onnx"))
        service.history_steps = 3
        service.bin_seconds = 1200
        service.missing_bucket_policy = "split_on_gap"

        with patch("backend.dashboard_api._load_local_records", return_value=records):
            with self.assertRaisesRegex(ValueError, "当前只有 2 个"):
                _checkpoint_model_features("CP-1", service, 20)

    def test_builds_model_ordered_calendar_features(self):
        records = [
            {
                "time": timestamp.isoformat(),
                "checkpoint_id": "CP-1",
                "vehicle_count": 21 + index,
            }
            for index, timestamp in enumerate(
                pd.date_range("2026-09-05T06:00:00Z", periods=6, freq="1h")
            )
        ]
        service = PredictionService(Path("missing.onnx"))
        service.history_steps = 6
        service.bin_seconds = 3600
        service.timezone = "UTC"
        service.feature_columns = [
            "vehicle_count",
            "hour",
            "day_of_week",
            "is_holiday",
        ]

        with patch("backend.dashboard_api._traffic_records", return_value=(records, "test", "")):
            features = _checkpoint_model_features("CP-1", service, 20)

        self.assertEqual(len(features), 6)
        self.assertEqual(features[0], [21.0, 6.0, 5.0, 1.0])


if __name__ == "__main__":
    unittest.main()
