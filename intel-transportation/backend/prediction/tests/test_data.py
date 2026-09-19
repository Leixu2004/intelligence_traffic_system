import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from backend.prediction.data import (
    CHINA_CHECKPOINT_FEATURE_COLUMNS,
    TEACHER_FEATURE_COLUMNS,
    load_teacher_compat_series,
    prepare_teacher_compat_partitions,
)


def _write_uci_csv(path: Path, periods: int, *, gap_after: int | None = None) -> None:
    timestamps = list(pd.date_range("2020-01-01", periods=periods, freq="1h"))
    if gap_after is not None:
        timestamps = timestamps[:gap_after] + [
            value + pd.Timedelta(hours=1) for value in timestamps[gap_after:]
        ]
    pd.DataFrame(
        {
            "holiday": ["New Years Day"] + ["None"] * (periods - 1),
            "date_time": timestamps,
            "traffic_volume": np.arange(periods, dtype=np.float32),
        }
    ).to_csv(path, index=False)


class TeacherCompatDataTests(unittest.TestCase):
    def test_china_profile_keeps_makeup_weekend_distinct_from_holiday(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "china.csv"
            timestamps = pd.date_range("2016-10-01", "2016-10-10", freq="20min")
            dates = timestamps.date
            pd.DataFrame(
                {
                    "timestamp": timestamps,
                    "series_id": "tollgate-1-entry",
                    "vehicle_count": np.arange(len(timestamps), dtype=np.float32),
                    "is_holiday": [int(value.day <= 7) for value in dates],
                    "is_makeup_workday": [
                        int(value in {pd.Timestamp("2016-10-08").date(), pd.Timestamp("2016-10-09").date()})
                        for value in dates
                    ],
                }
            ).to_csv(path, index=False)

            item = load_teacher_compat_series(
                path,
                feature_columns=CHINA_CHECKPOINT_FEATURE_COLUMNS,
                bin_seconds=1200,
                holiday_policy="china_statutory_with_makeup",
            )[0]

            october_first = 0
            october_eighth = 7 * 24 * 3
            self.assertEqual(item.features.shape[1], 6)
            self.assertEqual(item.features[october_first, 4:].tolist(), [1.0, 0.0])
            self.assertEqual(item.features[october_eighth, 2:].tolist(), [0.0, 5.0, 0.0, 1.0])

    def test_builds_six_by_four_windows_and_single_step_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Metro_Interstate_Traffic_Volume.csv"
            _write_uci_csv(path, 60)
            series_set = load_teacher_compat_series(path)
            partitions = prepare_teacher_compat_partitions(series_set)

            self.assertEqual(partitions.feature_columns, TEACHER_FEATURE_COLUMNS)
            self.assertEqual(partitions.train.features.shape[1:], (6, 4))
            self.assertEqual(partitions.train.targets.shape[1:], (1,))
            self.assertEqual(float(partitions.train.features[0, 0, 0]), 0.0)
            self.assertEqual(float(partitions.train.targets[0, 0]), 6.0)
            self.assertEqual(float(partitions.train.features[0, 0, 3]), 1.0)

    def test_raw_time_coordinates_do_not_overlap_across_partitions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metro.csv"
            _write_uci_csv(path, 90)
            partitions = prepare_teacher_compat_partitions(
                load_teacher_compat_series(path), purge_steps=2
            )

            def coordinates(window_set):
                return {
                    (series_key, int(timestamp))
                    for index, series_key in enumerate(window_set.series_keys)
                    for timestamp in np.append(
                        window_set.feature_times[index], window_set.target_times[index]
                    )
                }

            train = coordinates(partitions.train)
            validation = coordinates(partitions.validation)
            test = coordinates(partitions.test)
            self.assertTrue(train.isdisjoint(validation))
            self.assertTrue(train.isdisjoint(test))
            self.assertTrue(validation.isdisjoint(test))
            self.assertLess(
                int(partitions.train.target_times.max()),
                int(partitions.validation.feature_times.min()),
            )
            self.assertLess(
                int(partitions.validation.target_times.max()),
                int(partitions.test.feature_times.min()),
            )

    def test_windows_do_not_cross_hourly_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metro.csv"
            _write_uci_csv(path, 60, gap_after=30)
            series_set = load_teacher_compat_series(path)

            self.assertEqual(len(series_set), 2)
            self.assertTrue(all("#segment" in item.series_id for item in series_set))
            for item in series_set:
                np.testing.assert_array_equal(np.diff(item.bucket_starts), 3600)

    def test_scaler_values_only_contain_raw_training_points(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metro.csv"
            _write_uci_csv(path, 60)
            frame = pd.read_csv(path)
            frame.loc[42:, "traffic_volume"] = 10_000
            frame.to_csv(path, index=False)

            partitions = prepare_teacher_compat_partitions(
                load_teacher_compat_series(path)
            )
            self.assertLess(float(partitions.scaler_values[:, 0].max()), 10_000)
            self.assertEqual(float(partitions.validation.features[:, :, 0].max()), 10_000)
            self.assertEqual(float(partitions.test.features[:, :, 0].max()), 10_000)

    def test_short_series_is_reported_while_valid_series_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metro.csv"
            timestamps = list(pd.date_range("2020-01-01", periods=60, freq="1h"))
            short_times = list(pd.date_range("2021-01-01", periods=3, freq="1h"))
            pd.DataFrame(
                {
                    "series_id": ["valid"] * 60 + ["short"] * 3,
                    "date_time": timestamps + short_times,
                    "traffic_volume": list(range(60)) + list(range(3)),
                }
            ).to_csv(path, index=False)

            partitions = prepare_teacher_compat_partitions(
                load_teacher_compat_series(path)
            )
            self.assertTrue(
                any(":short" in str(item["series"]) for item in partitions.skipped_series)
            )
            self.assertTrue(all(":valid" in key for key in partitions.train.series_keys))


if __name__ == "__main__":
    unittest.main()
