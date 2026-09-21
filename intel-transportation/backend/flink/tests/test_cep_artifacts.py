"""9/21 复杂事件处理交付物的一致性测试。

覆盖三件事：SQL 文本能被静态校验锁住（改坏要报错）、阈值在 config/SQL/复算三处同源、
以及本地复算在 test_data.json 上确实能产出四级预警（没有集群也能证明规则自洽）。
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from backend.flink import alert_consumer, cep_reference
from backend.flink.config import (
    ALERT_LEVELS,
    CEP_MAX_SEGMENT_MINUTES,
    FlinkJobSettings,
    RED_MINUTES,
    load_flink_settings,
)
from backend.flink.cep_reference import Alert, Observation, diff, expected_alerts, load_events
from backend.flink.sql_checks import (
    check_alert_level_sql,
    check_cep_conf,
    check_cep_job_sql,
    check_cep_source_ddl,
    run_sql_checks,
)


def _names(checks) -> dict[str, bool]:
    return {check.name: check.passed for check in checks}


BASE_TIME = datetime(2026, 9, 21, 14, 0, 0)


def _dt(minutes: int = 0, second: int = 0) -> datetime:
    return BASE_TIME + timedelta(minutes=minutes, seconds=second)


class CepArtifactTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_flink_settings()

    def test_cep_artifacts_are_shipped(self):
        self.assertEqual(self.settings.missing_artifacts, ())
        for path in self.settings.cep_artifacts:
            self.assertTrue(path.is_file(), path)
        self.assertTrue(self.settings.alerts_db_ddl.is_file())

    def test_all_shipped_files_pass(self):
        result = run_sql_checks(self.settings)
        failed = [check["name"] for check in result["checks"] if not check["passed"]]
        self.assertEqual(failed, [], f"CEP 交付物不一致: {failed}")
        self.assertTrue(result["passed"])

    def test_cep_and_speed_jobs_use_distinct_consumer_groups(self):
        self.assertNotEqual(self.settings.cep_kafka_group_id, self.settings.kafka_group_id)
        sql = self.settings.source_ddl.read_text(encoding="utf-8")
        renamed = sql.replace(self.settings.cep_kafka_group_id, self.settings.kafka_group_id)
        names = _names(check_cep_source_ddl(self.settings, renamed))
        self.assertFalse(names["cep_source.group_id"])

        merged = FlinkJobSettings(
            **{**self.settings.__dict__, "cep_kafka_group_id": self.settings.kafka_group_id}
        )
        self.assertFalse(
            _names(check_cep_source_ddl(merged, sql))["cep_source.group_id_differs_from_speed_job"]
        )

    def test_missing_pattern_clause_is_caught(self):
        sql = self.settings.cep_job_sql.read_text(encoding="utf-8")
        without_pattern = (
            sql.replace("PATTERN (SLOW+ END_SLOW)", "")
            .replace("PATTERN (CRIT+ END_CRIT)", "")
            .replace("END_SLOW AS END_SLOW.speed_kmh >= 20", "")
            .replace("END_CRIT AS END_CRIT.speed_kmh >= 10", "")
        )
        names = _names(check_cep_job_sql(self.settings, without_pattern))
        self.assertFalse(names["cep.pattern_clause"])
        self.assertFalse(names["cep.closer_uses_opposite_condition"])

    def test_default_skip_strategy_is_caught(self):
        sql = self.settings.cep_job_sql.read_text(encoding="utf-8")
        # 删掉 AFTER MATCH SKIP 会退回默认的 SKIP TO NEXT ROW，同一段拥堵会被反复命中
        names = _names(check_cep_job_sql(self.settings, sql.replace("AFTER MATCH SKIP PAST LAST ROW", "")))
        self.assertFalse(names["cep.skip_past_last_row"])

    def test_thresholds_come_from_config(self):
        sql = self.settings.cep_job_sql.read_text(encoding="utf-8")
        names = _names(check_cep_job_sql(self.settings, sql.replace("SLOW.speed_kmh < 20", "SLOW.speed_kmh < 30")))
        self.assertFalse(names["cep.low_speed_threshold"])

    def test_case_order_purple_before_red_matters(self):
        sql = self.settings.alert_level_sql.read_text(encoding="utf-8")
        swapped = sql.replace(
            "WHEN avg_speed < 10 AND duration_min >= 20 THEN 'PURPLE'\n        "
            "WHEN avg_speed < 20 AND duration_min >= 10 THEN 'RED'",
            "WHEN avg_speed < 20 AND duration_min >= 10 THEN 'RED'\n        "
            "WHEN avg_speed < 10 AND duration_min >= 20 THEN 'PURPLE'",
        )
        self.assertNotEqual(swapped, sql, "替换未命中，测试需要同步更新")
        names = _names(check_alert_level_sql(self.settings, swapped))
        self.assertFalse(names["alert_level.case_order_purple_first"])

    def test_state_ttl_must_exceed_longest_within(self):
        text = self.settings.cep_conf.read_text(encoding="utf-8")
        short_ttl = text.replace("table.exec.state.ttl: 7200000", "table.exec.state.ttl: 600000")
        names = _names(check_cep_conf(self.settings, short_ttl))
        self.assertFalse(names["cep_conf.state_ttl_gt_within"])
        self.assertLess(600_000, int(CEP_MAX_SEGMENT_MINUTES * 60_000))
        self.assertGreater(int(7_200_000), int(CEP_MAX_SEGMENT_MINUTES * 60_000))

    def test_submit_script_runs_cep_files_in_order(self):
        text = self.settings.submit_script.read_text(encoding="utf-8")
        self.assertIn("source_ddl.sql", text)
        order = [name for name in ("alert_sink.sql", "congestion_cep.sql", "alert_level.sql") if name in text]
        self.assertEqual(order, ["alert_sink.sql", "congestion_cep.sql", "alert_level.sql"])
        # 写出必须在视图之后：alert_level.sql 里不能有 CREATE TABLE
        job = self.settings.alert_level_sql.read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE", job)


class CepReferenceTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_flink_settings()
        self.events, self.skipped = load_events(self.settings.cep_test_data)

    def test_reference_runs_on_shipped_test_data(self):
        self.assertGreater(len(self.events), 20)
        self.assertEqual(self.skipped, 1, "test_data.json 应保留一条缺失测速的记录")
        alerts = expected_alerts(self.events)
        levels = {alert.alert_level for alert in alerts}
        self.assertEqual(levels, set(ALERT_LEVELS), f"四级都要能被触发，实际 {levels}")
        self.assertTrue(all(alert.alert_level in ALERT_LEVELS for alert in alerts))
        self.assertTrue(all(alert.source in {"cep", "window"} for alert in alerts))

    def test_diff_against_itself_is_empty(self):
        alerts = expected_alerts(self.events)
        self.assertEqual(diff(alerts, list(alerts)), {"missing": [], "extra": []})

    def test_low_speed_run_below_two_rows_produces_nothing(self):
        # 单条低速不该成一段（对应 PATTERN 用 {2,} 而不是 +）
        series = [
            Observation("CAM-T", _dt(0), 55.0),
            Observation("CAM-T", _dt(1), 8.0),
            Observation("CAM-T", _dt(2), 60.0),
        ]
        self.assertEqual(cep_reference.cep_alerts(series), [])

    def test_run_is_cut_at_within_boundary(self):
        # WITHIN 现在是段长上限（60 分钟），不再负责把长拥堵截断成 10 分钟：
        # 超过上限的段按 Flink 语义判不匹配，宁可不报也不出错报。
        series = [Observation("CAM-T", _dt(minutes), 12.0) for minutes in range(0, 75)]
        series.append(Observation("CAM-T", _dt(75), 55.0))
        self.assertEqual(cep_reference.cep_alerts(series), [])
        # 上限之内的长段要完整成一段，时长可以远超 RED_MINUTES
        short = [Observation("CAM-T", _dt(minutes), 12.0) for minutes in range(0, 15)]
        short.append(Observation("CAM-T", _dt(15), 55.0))
        alerts = [a for a in cep_reference.cep_alerts(short) if a.source == "cep"]
        self.assertTrue(alerts)
        self.assertGreater(max(a.duration_min for a in alerts), RED_MINUTES)

    def test_run_without_closer_emits_nothing(self):
        # 直到数据结束仍在拥堵：没有「恢复」事件闭合这一段，SQL 与复算都不输出
        series = [Observation("CAM-T", _dt(minutes), 12.0) for minutes in range(0, 12)]
        self.assertEqual(cep_reference.cep_alerts(series), [])

    def test_alerts_are_per_camera(self):
        # PARTITION BY camera_id：两台摄像头的低速不能连成同一段
        series = [
            Observation("CAM-A", _dt(0), 8.0),
            Observation("CAM-B", _dt(1), 8.0),
            Observation("CAM-A", _dt(2), 8.0),
            Observation("CAM-B", _dt(3), 8.0),
            Observation("CAM-A", _dt(4), 8.0),
            Observation("CAM-B", _dt(5), 8.0),
            Observation("CAM-A", _dt(6), 60.0),
            Observation("CAM-B", _dt(7), 60.0),
        ]
        by_camera = {alert.camera_id: alert.low_cnt for alert in cep_reference.cep_alerts(series)}
        self.assertEqual(set(by_camera), {"CAM-A", "CAM-B"})
        # 各自的段只数自己的三条低速，不会串成一段 6 条
        self.assertEqual(set(by_camera.values()), {3})

    def test_window_branch_skips_severe_minutes(self):
        series = [Observation("CAM-T", _dt(0), 5.0), Observation("CAM-T", _dt(30), 6.0)]
        # 均速 < 20 的分钟归 CEP，窗口分支不产出（避免和 RED 争同一主键）
        self.assertEqual(cep_reference.window_alerts(series), [])

    def test_environment_override_changes_consumer_group(self):
        with patch.dict(os.environ, {"FLINK_CEP_KAFKA_GROUP_ID": "custom-cep-group"}, clear=True):
            settings = load_flink_settings()
        self.assertEqual(settings.cep_kafka_group_id, "custom-cep-group")


class AlertConsumerTests(unittest.TestCase):
    """课件第 7 页的「连续 5 分钟 speed > 30 自动降一级」在 SQL 里做不到，由消费端补。"""

    def test_downgrade_steps_one_level_and_green_is_stable(self):
        self.assertEqual(alert_consumer.downgrade("PURPLE"), "RED")
        self.assertEqual(alert_consumer.downgrade("RED"), "AMBER")
        self.assertEqual(alert_consumer.downgrade("AMBER"), "GREEN")
        self.assertEqual(alert_consumer.downgrade("GREEN"), "GREEN")

    def test_overlapping_same_level_alert_is_not_repushed(self):
        first = _alert("CAM-A", "RED", 0, 10)
        overlap = _alert("CAM-A", "RED", 5, 12)
        pushes = alert_consumer.consume([first, overlap])
        self.assertEqual([p.action for p in pushes], ["escalate"])

    def test_non_overlapping_same_level_alert_is_a_new_segment(self):
        first = _alert("CAM-A", "RED", 0, 5)
        later = _alert("CAM-A", "RED", 30, 38)
        pushes = alert_consumer.consume([first, later])
        self.assertEqual([p.action for p in pushes], ["escalate", "recur"])

    def test_escalation_reports_previous_level_chain(self):
        pushes = alert_consumer.consume(
            [
                _alert("CAM-A", "AMBER", 0, 3),
                _alert("CAM-A", "RED", 10, 20),
                _alert("CAM-A", "PURPLE", 25, 45),
            ]
        )
        self.assertEqual([p.action for p in pushes], ["escalate", "escalate", "escalate"])
        self.assertEqual(
            [(p.previous_level, p.level) for p in pushes],
            [("GREEN", "AMBER"), ("AMBER", "RED"), ("RED", "PURPLE")],
        )

    def test_same_start_red_and_purple_rows_push_only_the_severe_one(self):
        # 库侧主键允许同一 (camera, start) 出 RED 与 PURPLE 两行，推送只认更严重的那条
        pushes = alert_consumer.consume(
            [
                _alert("CAM-A", "RED", 10, 30, avg=8.0),
                _alert("CAM-A", "PURPLE", 10, 30, avg=6.0),
            ]
        )
        self.assertEqual([(p.action, p.level) for p in pushes], [("escalate", "PURPLE")])

    def test_recovery_needs_five_quiet_minutes_and_steps_one_level(self):
        purple = _alert("CAM-A", "PURPLE", 0, 20, source="cep")
        calm = [_alert("CAM-A", "GREEN", minute, minute, source="window", avg=52.0) for minute in range(21, 26)]
        pushes = alert_consumer.consume([purple] + calm)
        self.assertEqual([p.action for p in pushes], ["escalate", "downgrade"])
        self.assertEqual((pushes[-1].previous_level, pushes[-1].level), ("PURPLE", "RED"))

        # 再连续 5 分钟才降到黄级：一次只降一级
        more_calm = [_alert("CAM-A", "GREEN", minute, minute, source="window", avg=55.0) for minute in range(26, 31)]
        pushes = alert_consumer.consume([purple] + calm + more_calm)
        self.assertEqual([p.level for p in pushes], ["PURPLE", "RED", "AMBER"])

    def test_four_quiet_minutes_not_enough_to_downgrade(self):
        purple = _alert("CAM-A", "PURPLE", 0, 20, source="cep")
        calm = [_alert("CAM-A", "GREEN", minute, minute, source="window", avg=52.0) for minute in range(21, 25)]
        self.assertEqual([p.action for p in alert_consumer.consume([purple] + calm)], ["escalate"])

    def test_green_rows_produce_no_push(self):
        rows = [_alert("CAM-A", "GREEN", minute, minute, source="window", avg=60.0) for minute in range(10)]
        self.assertEqual(alert_consumer.consume(rows), [])

    def test_reference_pipeline_feeds_consumer(self):
        settings = load_flink_settings()
        events, _ = load_events(settings.cep_test_data)
        pushes = alert_consumer.consume(expected_alerts(events))
        self.assertTrue(pushes)
        self.assertTrue({p.action for p in pushes} <= {"escalate", "downgrade", "recur"})
        # 推送条数必须远少于落库预警条数，否则「去重」这项没有兑现
        self.assertLess(len(pushes), len(expected_alerts(events)) // 2)


def _alert(
    camera_id: str,
    level: str,
    start_minute: int,
    end_minute: int,
    *,
    source: str = "cep",
    avg: float = 8.0,
) -> Alert:
    return Alert(
        camera_id=camera_id,
        start_time=_dt(start_minute),
        end_time=_dt(end_minute),
        alert_level=level,
        avg_speed=avg,
        min_speed=avg,
        low_cnt=max(1, end_minute - start_minute),
        duration_min=float(end_minute - start_minute),
        source=source,
    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
