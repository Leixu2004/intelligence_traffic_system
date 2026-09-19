"""Small dependency-light checks for the dashboard data layer."""

import unittest
from unittest.mock import patch

import pandas as pd

from dashboard.data_loader import (
    _calendar_feature_row,
    _normalise_traffic,
    _remote_prediction,
    aggregate_traffic,
    calculate_metrics,
    load_traffic_data,
)


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class DashboardDataLoaderTests(unittest.TestCase):
    def test_china_calendar_row_marks_makeup_weekend_as_workday_feature(self):
        info = {
            "holiday_policy": "china_statutory_with_makeup",
            "holiday_calendar_valid_from": "2016-09-19",
            "holiday_calendar_valid_to": "2016-10-17",
            "holiday_dates": [f"2016-10-{day:02d}" for day in range(1, 8)],
            "makeup_workdays": ["2016-10-08", "2016-10-09"],
        }
        row = _calendar_feature_row(
            pd.Timestamp("2016-10-07T16:20:00Z"),
            88.0,
            [
                "vehicle_count",
                "hour",
                "minute",
                "day_of_week",
                "is_holiday",
                "is_makeup_workday",
            ],
            "Asia/Shanghai",
            info,
        )

        self.assertEqual(row, [88.0, 0.0, 20.0, 5.0, 0.0, 1.0])

    def test_local_bundle_has_stable_schema(self):
        bundle = load_traffic_data(source_mode="local")
        self.assertFalse(bundle.traffic.empty)
        self.assertIn("checkpoint_id", bundle.traffic.columns)
        self.assertIn("speed_kmh", bundle.traffic.columns)

    def test_aggregation_and_metrics(self):
        traffic = pd.DataFrame(
            {
                "time": pd.to_datetime(["2026-09-10T00:00:00Z", "2026-09-10T00:05:00Z"]),
                "vehicle_id": ["A", "B"],
                "checkpoint_id": ["CP-1", "CP-1"],
                "gps_lng": [116.4, 116.4],
                "gps_lat": [39.9, 39.9],
                "speed_kmh": [40.0, 60.0],
                "vehicle_count": [1, 1],
            }
        )
        aggregate = aggregate_traffic(traffic, "15min")
        metrics = calculate_metrics(traffic, pd.DataFrame())
        self.assertEqual(int(aggregate["vehicle_count"].sum()), 2)
        self.assertEqual(metrics["vehicle_count"], 2)
        self.assertEqual(metrics["hot_checkpoint"], "CP-1")
        self.assertEqual(metrics["alerts"], 1)

    def test_timescale_aggregate_preserves_vehicle_count(self):
        traffic = _normalise_traffic(
            pd.DataFrame(
                [
                    {
                        "time": "2026-09-10T00:00:00Z",
                        "vehicle_id": "CP-1-bucket",
                        "checkpoint_id": "CP-1",
                        "vehicle_count": 50,
                        "average_speed": 42.5,
                    }
                ]
            )
        )
        aggregate = aggregate_traffic(traffic, "15min")
        self.assertEqual(int(aggregate["vehicle_count"].sum()), 50)

    @patch("dashboard.data_loader.requests.post")
    @patch("dashboard.data_loader.requests.get")
    def test_remote_prediction_adapts_multivariate_single_step_model(self, get, post):
        get.return_value = _FakeResponse(
            {
                "data": {
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
                    "timezone": "UTC",
                }
            }
        )
        post.side_effect = [
            _FakeResponse({"data": {"forecast": [170.0]}}),
            _FakeResponse({"data": {"forecast": [180.0]}}),
        ]
        history = pd.DataFrame(
            {
                "time": pd.date_range("2026-09-05T00:00:00Z", periods=6, freq="1h"),
                "vehicle_count": [100, 110, 120, 130, 140, 150],
            }
        )

        result = _remote_prediction("http://api", history, 2, "1h")

        self.assertEqual(result.values, [170.0, 180.0])
        self.assertEqual(result.interval, pd.Timedelta(hours=1))
        self.assertEqual(post.call_count, 2)
        first_payload = post.call_args_list[0].kwargs["json"]
        self.assertEqual(len(first_payload["features"]), 6)
        self.assertEqual(len(first_payload["features"][0]), 4)
        self.assertEqual(first_payload["features"][0][-1], 1.0)

    @patch("dashboard.data_loader.PREDICTION_API_KEY", "test-secret")
    @patch("dashboard.data_loader.requests.post")
    @patch("dashboard.data_loader.requests.get")
    def test_remote_prediction_sends_configured_api_key(self, get, post):
        get.return_value = _FakeResponse(
            {
                "data": {
                    "model_type": "lstm",
                    "history_steps": 2,
                    "forecast_steps": 1,
                    "bin_seconds": 60,
                    "target_column": "vehicle_count",
                    "feature_columns": ["vehicle_count"],
                }
            }
        )
        post.return_value = _FakeResponse({"data": {"forecast": [12.0]}})
        history = pd.DataFrame(
            {
                "time": pd.date_range("2026-09-16T00:00:00Z", periods=2, freq="1min"),
                "vehicle_count": [10, 11],
            }
        )

        result = _remote_prediction("http://api", history, 1, "1min")

        self.assertEqual(result.values, [12.0])
        self.assertEqual(post.call_args.kwargs["headers"], {"X-API-Key": "test-secret"})

    def test_2026_china_calendar_period_marks_makeup_workday(self):
        info = {
            "holiday_policy": "china_statutory_with_makeup",
            "inference_calendar_periods": [
                {
                    "valid_from": "2026-01-01",
                    "valid_to": "2026-12-31",
                    "holiday_dates": ["2026-10-01"],
                    "makeup_workdays": ["2026-09-20"],
                }
            ],
        }
        row = _calendar_feature_row(
            pd.Timestamp("2026-09-20T00:00:00Z"),
            42.0,
            ["vehicle_count", "is_holiday", "is_makeup_workday"],
            "Asia/Shanghai",
            info,
        )

        self.assertEqual(row, [42.0, 0.0, 1.0])


if __name__ == "__main__":
    unittest.main()
