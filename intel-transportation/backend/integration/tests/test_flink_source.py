"""Flink → 集成层的数据入口：本地复算夹具与库侧 SQL 的一致性。"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from backend.integration import flink_source
from backend.integration.config import load_integration_settings
from backend.integration.flink_source import (
    DEFAULT_EVENTS_PATH,
    DatabaseWindowReader,
    SimulationWindowReader,
    WindowRow,
    default_window_reader,
    most_severe,
    utc_now_text,
)

LEVELS = ("GREEN", "AMBER", "RED", "PURPLE")


def _row(camera_id: str, window_start: str, level: str, **overrides: Any) -> WindowRow:
    values: dict[str, Any] = {
        "camera_id": camera_id,
        "checkpoint_id": f"CP-{camera_id}",
        "window_start": window_start,
        "avg_speed": 30.0,
        "max_speed": 45.0,
        "vehicle_count": 6,
        "alert_level": level,
        "duration_min": 10.0,
        "source": "timescaledb",
    }
    values.update(overrides)
    return WindowRow(**values)


class SimulationWindowReaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not DEFAULT_EVENTS_PATH.is_file():  # pragma: no cover
            raise AssertionError(f"缺少 9/21 交付的测试车流：{DEFAULT_EVENTS_PATH}")
        cls.reader = SimulationWindowReader(DEFAULT_EVENTS_PATH)
        cls.rows = cls.reader.latest(limit=500)

    def test_reader_reports_simulation_source(self) -> None:
        self.assertEqual("simulation", self.reader.source)
        self.assertTrue(all(row.source == "simulation" for row in self.rows))

    def test_windows_cover_all_four_levels_and_track_speed(self) -> None:
        self.assertEqual(28, len(self.rows))  # 87 事件 / HOP(10min,5min) 的确定性结果
        self.assertEqual(set(LEVELS), {row.alert_level for row in self.rows})
        for row in self.rows:
            self.assertGreater(row.vehicle_count, 0)
            if row.alert_level == "GREEN":
                self.assertGreaterEqual(row.avg_speed, 40.0)

    def test_window_start_format_matches_to_char_output(self) -> None:
        for row in self.rows:
            with self.subTest(window_start=row.window_start):
                self.assertRegex(row.window_start, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

    def test_camera_filter_and_limit(self) -> None:
        one_camera = self.reader.latest(limit=500, camera_id="CAM-02")
        self.assertTrue(one_camera)
        self.assertTrue({row.camera_id for row in one_camera} == {"CAM-02"})
        self.assertEqual(3, len(self.reader.latest(limit=3)))

    def test_checkpoint_id_comes_from_events_not_camera_id(self) -> None:
        checkpoints = {row.checkpoint_id for row in self.rows}
        self.assertEqual({"CP-NORTH-01", "CP-SOUTH-02", "CP-EAST-03"}, checkpoints)

    def test_missing_file_yields_empty_not_crash(self) -> None:
        reader = SimulationWindowReader(Path("does/not/exist.json"))
        self.assertEqual([], reader.latest())

    def test_size_seconds_controls_window_length(self) -> None:
        """窗口长度体现在进窗事件数与窗口起点上。

        duration_min 不能用来判窗口长度：它是联表带出的 CEP 拥堵段时长（本机 test_data.json
        里有一段 25 分钟的拥堵），与 size_seconds 无关。
        """
        by_size = {
            size: SimulationWindowReader(DEFAULT_EVENTS_PATH, size_seconds=size).latest(limit=500)
            for size in (300, 600)
        }
        self.assertTrue(by_size[300] and by_size[600])
        peak = {size: max(row.vehicle_count for row in rows) for size, rows in by_size.items()}
        self.assertLess(peak[300], peak[600])
        self.assertLess(
            min(row.window_start for row in by_size[600]),
            min(row.window_start for row in by_size[300]),
        )


class JoinedAlertTest(unittest.TestCase):
    """联表规则：时段重叠才取，不重叠就 GREEN —— 与 DatabaseWindowReader.SQL 一致。"""

    class _Alert:
        def __init__(self, camera_id: str, start: str, end: str, level: str, duration: float) -> None:
            self.camera_id = camera_id
            self.start_time = datetime.fromisoformat(start)
            self.end_time = datetime.fromisoformat(end)
            self.alert_level = level
            self.duration_min = duration

    def setUp(self) -> None:
        self.alerts = [
            self._Alert("CAM-01", "2026-09-21T14:00:00", "2026-09-21T14:10:00", "RED", 10.0),
            self._Alert("CAM-01", "2026-09-21T14:05:00", "2026-09-21T14:25:00", "PURPLE", 20.0),
            self._Alert("CAM-02", "2026-09-21T14:00:00", "2026-09-21T14:00:59", "AMBER", 1.0),
        ]

    def test_only_overlapping_alerts_join(self) -> None:
        self.assertIsNone(flink_source._joined_alert(self.alerts, "CAM-01", "2026-09-21T15:00:00", "2026-09-21T15:10:00"))
        self.assertIsNone(flink_source._joined_alert(self.alerts, "CAM-03", "2026-09-21T14:00:00", "2026-09-21T14:10:00"))

    def test_most_severe_wins_over_latest(self) -> None:
        joined = flink_source._joined_alert(self.alerts, "CAM-01", "2026-09-21T14:06:00", "2026-09-21T14:16:00")
        self.assertEqual("PURPLE", joined.alert_level)

    def test_same_level_picks_latest_start(self) -> None:
        alerts = self.alerts + [self._Alert("CAM-01", "2026-09-21T14:20:00", "2026-09-21T14:30:00", "PURPLE", 10.0)]
        joined = flink_source._joined_alert(alerts, "CAM-01", "2026-09-21T14:22:00", "2026-09-21T14:32:00")
        self.assertEqual("2026-09-21T14:20:00", joined.start_time.isoformat(timespec="seconds"))


class DatabaseWindowReaderTest(unittest.TestCase):
    def test_sql_is_a_lateral_join_with_overlap_and_coalesce(self) -> None:
        sql = DatabaseWindowReader("postgresql://x").SQL
        for fragment in (
            "FROM speed_stats s",
            "LEFT JOIN LATERAL",
            "traffic_alerts t",
            "t.start_time <= s.window_start + INTERVAL '10' MINUTE",
            "t.end_time >= s.window_start",
            "COALESCE(a.alert_level, 'GREEN')",
            "ORDER BY CASE t.alert_level",
        ):
            self.assertIn(fragment, sql)

    def test_no_bare_now_or_wall_clock_literals(self) -> None:
        self.assertNotIn("NOW()", DatabaseWindowReader("x").SQL.upper())

    def test_empty_dsn_returns_no_rows(self) -> None:
        self.assertEqual([], DatabaseWindowReader("   ").latest())

    def test_missing_driver_raises_instead_of_silently_passing(self) -> None:
        with patch.object(flink_source, "psycopg2", None):
            with self.assertRaises(RuntimeError):
                DatabaseWindowReader("postgresql://user:pw@host/db").latest()


class DefaultReaderTest(unittest.TestCase):
    def test_falls_back_to_simulation_without_dsn(self) -> None:
        reader = default_window_reader(load_integration_settings())
        self.assertIsInstance(reader, SimulationWindowReader)

    def test_uses_database_reader_when_dsn_and_driver_present(self) -> None:
        settings = replace(load_integration_settings(), timescaledb_dsn="postgresql://postgres:postgres@127.0.0.1:55432/traffic")
        with patch.object(flink_source, "psycopg2", object()):
            reader = default_window_reader(settings)
        self.assertIsInstance(reader, DatabaseWindowReader)


class MostSevereTest(unittest.TestCase):
    def test_picks_worst_level_then_latest_window(self) -> None:
        rows = [
            _row("CAM-01", "2026-09-21T14:20:00", "GREEN"),
            _row("CAM-02", "2026-09-21T14:10:00", "RED"),
            _row("CAM-03", "2026-09-21T14:15:00", "AMBER"),
        ]
        self.assertEqual("CAM-02", most_severe(rows).camera_id)
        self.assertIsNone(most_severe([]))
        self.assertIsNone(most_severe([_row("CAM-09", "2026-09-21T14:10:00", "UNKNOWN")]))

    def test_ties_break_on_latest_window_start(self) -> None:
        rows = [
            _row("CAM-01", "2026-09-21T14:10:00", "RED"),
            _row("CAM-02", "2026-09-21T14:20:00", "RED"),
        ]
        self.assertEqual("CAM-02", most_severe(rows).camera_id)


class TimestampTest(unittest.TestCase):
    def test_utc_now_text_is_naive_utc_with_z_suffix(self) -> None:
        text = utc_now_text()
        self.assertRegex(text, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        # 与真实 UTC 相差应在分钟内（不再用已弃用的 datetime.utcnow）
        parsed = datetime.fromisoformat(text[:-1])
        self.assertLess(abs((datetime.now(timezone.utc).replace(tzinfo=None) - parsed).total_seconds()), 120)

    def test_naive_utc_text_normalises_offsets(self) -> None:
        self.assertEqual("2026-09-21T14:20:00", flink_source._naive_utc_text("2026-09-21T14:20:00.000+00:00"))
        self.assertEqual("2026-09-21T14:20:00", flink_source._naive_utc_text("2026-09-21T14:20:00"))
        self.assertEqual("2026-09-21T22:20:00", flink_source._naive_utc_text("2026-09-21T14:20:00-08:00"))

    def test_push_log_round_trip_is_json_safe(self) -> None:
        record = json.loads(json.dumps({"time": utc_now_text(), "levels": list(LEVELS)}))
        self.assertEqual(list(LEVELS), record["levels"])


if __name__ == "__main__":
    unittest.main()
