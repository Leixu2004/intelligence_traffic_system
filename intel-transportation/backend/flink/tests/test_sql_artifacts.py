import os
import unittest
from unittest.mock import patch

from backend.flink.config import FlinkJobSettings, load_flink_settings
from backend.flink.sql_checks import _select_columns, check_job_sql, check_source_ddl, run_sql_checks


class SettingsTests(unittest.TestCase):
    def test_defaults_follow_courseware_window(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = load_flink_settings()
        self.assertEqual(settings.hop_slide_seconds, 300)
        self.assertEqual(settings.hop_size_seconds, 600)
        self.assertEqual(settings.min_vehicle_count, 6)
        self.assertEqual(settings.kafka_topic, "traffic_stream")
        self.assertEqual(settings.sink_table, "speed_stats")

    def test_env_overrides_are_clamped(self):
        env = {
            "FLINK_HOP_SLIDE_SECONDS": "999999",
            "FLINK_HOP_SIZE_SECONDS": "not-a-number",
            "FLINK_MIN_VEHICLE_COUNT": "0",
            "FLINK_KAFKA_BROKERS": "  broker-2:9092 ",
            "FLINK_WEB_UI_URL": "http://flink:8081/",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = load_flink_settings()
        self.assertEqual(settings.hop_slide_seconds, 3600)  # 上限钳制
        self.assertEqual(settings.hop_size_seconds, 600)  # 非法值回落默认
        self.assertEqual(settings.min_vehicle_count, 1)  # 下限钳制
        self.assertEqual(settings.kafka_brokers, "broker-2:9092")
        self.assertEqual(settings.web_ui_url, "http://flink:8081")

    def test_shipped_artifacts_exist(self):
        settings = load_flink_settings()
        self.assertEqual(settings.missing_artifacts, ())
        self.assertTrue(settings.db_ddl.is_file())


class SqlConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_flink_settings()

    def test_all_checks_pass_on_shipped_files(self):
        result = run_sql_checks(self.settings)
        failed = [check["name"] for check in result["checks"] if not check["passed"]]
        self.assertEqual(failed, [], f"交付物不一致: {failed}")
        self.assertTrue(result["passed"])
        self.assertGreaterEqual(result["total"], 30)

    def test_missing_artifact_is_reported_not_raised(self):
        broken = FlinkJobSettings(
            **{**self.settings.__dict__, "job_sql": self.settings.sql_dir / "nope.sql"}
        )
        result = run_sql_checks(broken)
        self.assertFalse(result["passed"])
        self.assertIn("nope.sql", result["artifacts_missing"])

    def test_watermark_and_topic_checks_react_to_edits(self):
        sql = self.settings.source_ddl.read_text(encoding="utf-8")
        self.assertTrue(all(check.passed for check in check_source_ddl(self.settings, sql)))
        no_watermark = sql.replace("WATERMARK FOR event_time", "-- WATERMARK")
        self.assertFalse(
            [c for c in check_source_ddl(self.settings, no_watermark) if c.name == "source.watermark"][0].passed
        )
        wrong_topic = sql.replace("'topic' = 'traffic_stream'", "'topic' = 'camera-data'")
        self.assertFalse(
            [c for c in check_source_ddl(self.settings, wrong_topic) if c.name == "source.topic"][0].passed
        )

    def test_job_checks_reject_wrong_window_size(self):
        sql = self.settings.job_sql.read_text(encoding="utf-8")
        wrong_size = sql.replace("INTERVAL '10' MINUTE", "INTERVAL '15' MINUTE")
        names = {check.name: check.passed for check in check_job_sql(self.settings, wrong_size)}
        self.assertFalse(names["job.hop_window"])

    def test_projection_helper_reads_aliases_in_order(self):
        columns = _select_columns(
            "INSERT INTO t\nSELECT window_start,\n"
            "  camera_id,\n  ROUND(AVG(speed_kmh), 1) AS avg_speed,\n"
            "  MAX(speed_kmh) AS max_speed,\n  COUNT(*) AS vehicle_count\nFROM TABLE(HOP("
        )
        self.assertEqual(columns, ["window_start", "camera_id", "avg_speed", "max_speed", "vehicle_count"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
