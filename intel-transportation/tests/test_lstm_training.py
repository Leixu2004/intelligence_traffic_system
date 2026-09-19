import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from backend.prediction.data import (
    FlowSeries,
    load_flow_series_set,
    prepare_window_partitions,
)
from backend.prediction.train_lstm import _parse_args, build_windows, load_flow_series


class LSTMTrainingTests(unittest.TestCase):
    def test_build_windows_keeps_time_order(self):
        features, targets = build_windows(np.arange(10, dtype=np.float32), 4, 2)
        self.assertEqual(features.shape, (5, 4, 1))
        self.assertEqual(targets.shape, (5, 2))
        np.testing.assert_array_equal(features[0, :, 0], [0, 1, 2, 3])
        np.testing.assert_array_equal(targets[0], [4, 5])

    def test_loads_aggregated_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flow.csv"
            pd.DataFrame({"vehicle_count": [3, 5, 4]}).to_csv(path, index=False)
            np.testing.assert_array_equal(load_flow_series(path), [3, 5, 4])

    def test_selects_entering_vehicle_count_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flow.csv"
            pd.DataFrame(
                {
                    "vehicle_count": [30, 40, 50],
                    "entering_vehicle_count": [3, 4, 5],
                }
            ).to_csv(path, index=False)
            np.testing.assert_array_equal(
                load_flow_series(path, target_column="entering_vehicle_count"),
                [3, 4, 5],
            )

    def test_missing_target_column_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flow.csv"
            pd.DataFrame({"vehicle_count": [3, 5, 4]}).to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "entering_vehicle_count"):
                load_flow_series(path, target_column="entering_vehicle_count")

    def test_multiple_series_are_loaded_without_crossing_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flow.csv"
            pd.DataFrame(
                {
                    "series_id": ["A"] * 5 + ["B"] * 5,
                    "bucket_start_seconds": [0, 60, 120, 180, 240] * 2,
                    "bin_seconds": [60] * 10,
                    "vehicle_count": [1, 2, 3, 4, 5, 101, 102, 103, 104, 105],
                }
            ).to_csv(path, index=False)
            series_set = load_flow_series_set(path)
            self.assertEqual([item.series_id for item in series_set], ["A", "B"])
            for item in series_set:
                features, targets = build_windows(item.values, 2, 1)
                combined = np.concatenate([features[:, :, 0], targets], axis=1)
                self.assertTrue(np.all(combined < 100) or np.all(combined >= 100))

    def test_loads_timestamp_track_trajectory_with_entering_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trajectory.csv"
            pd.DataFrame(
                {
                    "timestamp(ms)": [0, 30_000, 59_000, 60_000, 90_000, 119_000],
                    "track_id": ["A", "B", "A", "A", "C", "C"],
                }
            ).to_csv(path, index=False)
            np.testing.assert_array_equal(
                load_flow_series(
                    path,
                    target_column="entering_vehicle_count",
                ),
                [2, 1],
            )

    def test_series_holdout_has_no_shared_values_between_splits(self):
        source = Path("fixture.csv").resolve()
        series_set = [
            FlowSeries(
                key=f"{source}:A",
                series_id="A",
                source_path=source,
                bin_seconds=60,
                target_column="vehicle_count",
                bucket_starts=np.arange(12) * 60,
                values=np.arange(12, dtype=np.float32),
            ),
            FlowSeries(
                key=f"{source}:B",
                series_id="B",
                source_path=source,
                bin_seconds=60,
                target_column="vehicle_count",
                bucket_starts=np.arange(12) * 60,
                values=np.arange(100, 112, dtype=np.float32),
            ),
        ]
        partitions = prepare_window_partitions(series_set, 3, 2, validation_ratio=0.25)
        train_values = set(partitions.train_features.reshape(-1)) | set(
            partitions.train_targets.reshape(-1)
        )
        validation_values = set(partitions.validation_features.reshape(-1)) | set(
            partitions.validation_targets.reshape(-1)
        )
        self.assertTrue(train_values.isdisjoint(validation_values))
        self.assertEqual(partitions.split_strategy, "series_holdout")

    def test_short_series_is_reported_without_dropping_valid_series(self):
        source = Path("fixture.csv").resolve()
        valid = FlowSeries(
            key=f"{source}:valid",
            series_id="valid",
            source_path=source,
            bin_seconds=60,
            target_column="vehicle_count",
            bucket_starts=np.arange(20) * 60,
            values=np.arange(20, dtype=np.float32),
        )
        short = FlowSeries(
            key=f"{source}:short",
            series_id="short",
            source_path=source,
            bin_seconds=60,
            target_column="vehicle_count",
            bucket_starts=np.arange(4) * 60,
            values=np.arange(4, dtype=np.float32),
        )
        partitions = prepare_window_partitions([valid, short], 3, 2)
        self.assertEqual(len(partitions.skipped_series), 1)
        self.assertIn("short", str(partitions.skipped_series[0]["series"]))

    def test_cli_accepts_explicit_target_column(self):
        args = _parse_args(
            ["轨迹 数据.csv", "--target-column", "entering_vehicle_count"]
        )
        self.assertEqual(args.data, ["轨迹 数据.csv"])
        self.assertEqual(args.target_column, "entering_vehicle_count")


if __name__ == "__main__":
    unittest.main()
