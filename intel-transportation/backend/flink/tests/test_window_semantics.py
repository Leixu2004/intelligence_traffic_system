import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from backend.flink.config import load_flink_settings
from backend.flink.window_semantics import (
    SpeedWindowStat,
    aggregate_speed_stats,
    hop_window_starts,
    window_group_sizes,
)


def event(moment: datetime, camera: str, speed: float | None) -> dict[str, object]:
    return {
        "event_type": "traffic_observation",
        "time": moment.isoformat(timespec="milliseconds"),
        "camera_id": camera,
        "speed_kmh": speed,
    }


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class HopWindowTests(unittest.TestCase):
    def test_event_belongs_to_two_overlapping_windows(self):
        starts = hop_window_starts(utc("2026-09-21 06:07:00"), 300, 600)
        self.assertEqual(
            [moment.strftime("%H:%M") for moment in starts],
            ["06:00", "06:05"],
        )

    def test_window_assignment_is_half_open(self):
        # 06:10:00.000 正好是 06:00 窗口的右开边界，只归属后两个窗口
        starts = hop_window_starts(utc("2026-09-21 06:10:00"), 300, 600)
        self.assertEqual(
            [moment.strftime("%H:%M") for moment in starts],
            ["06:05", "06:10"],
        )

    def test_first_slide_boundary_belongs_to_window_starting_now(self):
        starts = hop_window_starts(utc("2026-09-21 06:05:00"), 300, 600)
        self.assertEqual(starts[0].strftime("%H:%M"), "06:00")
        self.assertEqual(starts[-1].strftime("%H:%M"), "06:05")

    def test_window_count_equals_size_over_slide(self):
        for seconds in (0, 1, 299, 300, 421, 599):
            moment = datetime(2026, 9, 21, 6, 0, 0, tzinfo=timezone.utc)
            moment = moment.replace(second=seconds % 60, microsecond=seconds * 1000)
            self.assertEqual(len(hop_window_starts(moment, 300, 600)), 2)

    def test_naive_datetime_is_read_as_utc(self):
        naive = datetime(2026, 9, 21, 6, 7, tzinfo=None)
        starts = hop_window_starts(naive, 300, 600)
        self.assertEqual(starts[-1].utcoffset().total_seconds(), 0)

    def test_invalid_window_shape_rejected(self):
        moment = utc("2026-09-21 06:07:00")
        with self.assertRaises(ValueError):
            hop_window_starts(moment, 0, 600)
        with self.assertRaises(ValueError):
            hop_window_starts(moment, 600, 300)


class AggregateTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_flink_settings()

    def test_statistics_over_a_single_window(self):
        events = [
            event(utc("2026-09-21 06:01:00"), "CAM-01", speed)
            for speed in (60.0, 70.0, 80.0, 90.0, 50.0, 55.0, 65.0)
        ]
        stats = aggregate_speed_stats(events)
        self.assertEqual(len(stats), 2)  # 06:00 与 05:55/06:05 两个滑动窗口
        first = stats[0]
        self.assertIsInstance(first, SpeedWindowStat)
        self.assertEqual(first.camera_id, "CAM-01")
        self.assertEqual(first.vehicle_count, 7)
        self.assertEqual(first.max_speed, 90.0)
        self.assertAlmostEqual(first.avg_speed, round(sum([60, 70, 80, 90, 50, 55, 65]) / 7, 1), places=1)

    def test_null_speed_rows_are_dropped(self):
        events = [event(utc("2026-09-21 06:01:00"), "CAM-01", None) for _ in range(10)]
        events.append(event(utc("2026-09-21 06:02:00"), "CAM-01", 42.0))
        stats = aggregate_speed_stats(events, min_vehicle_count=1)
        self.assertEqual([stat.vehicle_count for stat in stats], [1, 1])
        self.assertTrue(all(stat.avg_speed == 42.0 for stat in stats))

    def test_having_clause_filters_thin_windows(self):
        events = [event(utc("2026-09-21 06:01:00"), "CAM-03", 50.0) for _ in range(5)]
        self.assertEqual(aggregate_speed_stats(events), [])
        groups = window_group_sizes(events)
        self.assertTrue(all(size == 5 for size in groups.values()))
        # 再多一条就越过 COUNT(*) > 5
        events.append(event(utc("2026-09-21 06:01:30"), "CAM-03", 50.0))
        self.assertEqual(len(aggregate_speed_stats(events)), 2)

    def test_camera_id_falls_back_to_checkpoint_id(self):
        rows = [
            {
                "time": "2026-09-21T06:01:00+00:00",
                "checkpoint_id": "CP-NORTH-01",
                "speed_kmh": 66.0,
            }
        ] * 7
        stats = aggregate_speed_stats(rows)
        self.assertEqual([stat.camera_id for stat in stats], ["CP-NORTH-01"] * 2)

    def test_unparsable_time_is_skipped(self):
        events = [event(utc("2026-09-21 06:01:00"), "CAM-01", 60.0) for _ in range(7)]
        events.append({"time": "not-a-time", "camera_id": "CAM-01", "speed_kmh": 10.0})
        stats = aggregate_speed_stats(events)
        self.assertTrue(all(stat.max_speed == 60.0 for stat in stats))

    def test_avg_rounds_half_up_like_sql_round(self):
        # 60.0 与 60.1 的平均是 60.05：SQL ROUND(...,1) 四舍五入向上
        events = [
            event(utc("2026-09-21 06:01:00"), "CAM-01", 60.0),
            event(utc("2026-09-21 06:01:10"), "CAM-01", 60.1),
            event(utc("2026-09-21 06:01:20"), "CAM-01", 60.1),
            event(utc("2026-09-21 06:01:30"), "CAM-01", 60.0),
            event(utc("2026-09-21 06:01:40"), "CAM-01", 60.1),
            event(utc("2026-09-21 06:01:50"), "CAM-01", 60.0),
            event(utc("2026-09-21 06:01:55"), "CAM-01", 60.1),
        ]
        stats = aggregate_speed_stats(events)
        self.assertEqual(stats[0].avg_speed, 60.1)

    def test_rows_sorted_by_window_then_camera(self):
        events = []
        for camera in ("CAM-02", "CAM-01"):
            events += [event(utc("2026-09-21 06:03:00"), camera, 50.0) for _ in range(7)]
            events += [event(utc("2026-09-21 06:11:00"), camera, 70.0) for _ in range(7)]
        stats = aggregate_speed_stats(events)
        keys = [(stat.window_start.strftime("%H:%M"), stat.camera_id) for stat in stats]
        self.assertEqual(keys, sorted(keys))

    def test_settings_drive_window_shape(self):
        events = [event(utc("2026-09-21 06:07:00"), "CAM-01", 50.0) for _ in range(10)]
        wide = aggregate_speed_stats(
            events,
            slide_seconds=self.settings.hop_slide_seconds,
            size_seconds=self.settings.hop_size_seconds,
            min_vehicle_count=self.settings.min_vehicle_count,
        )
        self.assertEqual([stat.window_start.strftime("%H:%M") for stat in wide], ["06:00", "06:05"])

    def test_env_tuned_min_count_changes_output(self):
        events = [event(utc("2026-09-21 06:01:00"), "CAM-01", 50.0) for _ in range(6)]
        with patch.dict(os.environ, {"FLINK_MIN_VEHICLE_COUNT": "7"}, clear=True):
            settings = load_flink_settings()
        self.assertEqual(
            aggregate_speed_stats(events, min_vehicle_count=settings.min_vehicle_count), []
        )
        with patch.dict(os.environ, {"FLINK_MIN_VEHICLE_COUNT": "5"}, clear=True):
            settings = load_flink_settings()
        self.assertEqual(len(aggregate_speed_stats(events, min_vehicle_count=settings.min_vehicle_count)), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
