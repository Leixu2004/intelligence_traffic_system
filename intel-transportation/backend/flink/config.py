"""Flink SQL 作业的路径与窗口参数，同时供静态校验和本地窗口语义复算使用。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..agent.config import INNER_ROOT

FLINK_ROOT = Path(__file__).resolve().parent
SQL_DIR = FLINK_ROOT / "sql"

DEFAULT_HOP_SLIDE_SECONDS = 300
DEFAULT_HOP_SIZE_SECONDS = 600
DEFAULT_MIN_VEHICLE_COUNT = 6
DEFAULT_KAFKA_TOPIC = "traffic_stream"
DEFAULT_KAFKA_BROKERS = "kafka:29092"
DEFAULT_JDBC_URL = "jdbc:postgresql://timescaledb:5432/traffic"
# 与 docker-compose.yml 的 FLINK_WEB_UI_HOST_PORT 默认值保持一致：本机 8081 常驻着另一套
# Flink，指错会读到别的集群的作业数与槽位数（实测踩过，读起来一切「正常」）。
DEFAULT_WEB_UI_URL = "http://localhost:8181"
DEFAULT_CEP_KAFKA_GROUP_ID = "flink-cep-alert-v1"
DEFAULT_ALERT_SINK_TABLE = "traffic_alerts"

# 9/21 复杂事件处理：分级阈值照课件第 7 页，改这里必须同步改 sql/alert_level.sql，
# 一致性由 sql_checks.check_alert_level_sql + tests/test_cep_artifacts.py 锁住。
RED_SPEED_KMH = 20.0
PURPLE_SPEED_KMH = 10.0
AMBER_SPEED_KMH = 40.0
RED_MINUTES = 10.0
PURPLE_MINUTES = 20.0
ALERT_LEVELS = ("GREEN", "AMBER", "RED", "PURPLE")

# CEP 的 WITHIN 上限。它不再是课件里「10/20 分钟把长拥堵截断」的那个 WITHIN：
# Flink 的贪心量词不回溯，一段低速只能由下一条不再低速的事件闭合（集群实测，
# 见 backend/flink/README「CEP 与课件示例的差别」），段长因此可以远超分级阈值，
# 所以这里给的是一个防止算子状态无限增长的上限，而不是分级用的持续时长。
# 分级仍然看 duration_min >= RED_MINUTES / PURPLE_MINUTES。
CEP_MAX_SEGMENT_MINUTES = 60.0


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


@dataclass(frozen=True)
class FlinkJobSettings:
    """窗口参数与产物路径。数值与 sql/ 目录下的 SQL 文本必须一致，由测试锁住。"""

    sql_dir: Path
    source_ddl: Path
    sink_ddl: Path
    job_sql: Path
    flink_conf: Path
    submit_script: Path
    db_ddl: Path
    hop_slide_seconds: int
    hop_size_seconds: int
    min_vehicle_count: int
    watermark_delay_seconds: int
    kafka_brokers: str
    kafka_topic: str
    kafka_group_id: str
    jdbc_url: str
    sink_table: str
    web_ui_url: str
    # 9/21 复杂事件处理：CEP 交付物路径与口径参数（缺省值即仓库内的实际交付物）
    alert_sink_ddl: Path = SQL_DIR / "alert_sink.sql"
    cep_job_sql: Path = SQL_DIR / "congestion_cep.sql"
    alert_level_sql: Path = SQL_DIR / "alert_level.sql"
    cep_conf: Path = FLINK_ROOT / "conf" / "cep_config.yaml"
    cep_test_data: Path = FLINK_ROOT / "test_data.json"
    alerts_db_ddl: Path = INNER_ROOT / "backend" / "sql" / "flink_traffic_alerts.sql"
    cep_kafka_group_id: str = DEFAULT_CEP_KAFKA_GROUP_ID
    alert_sink_table: str = DEFAULT_ALERT_SINK_TABLE

    @property
    def artifacts(self) -> tuple[Path, ...]:
        return (
            self.source_ddl,
            self.sink_ddl,
            self.job_sql,
            self.flink_conf,
            self.submit_script,
            self.alert_sink_ddl,
            self.cep_job_sql,
            self.alert_level_sql,
            self.cep_conf,
            self.cep_test_data,
        )

    @property
    def missing_artifacts(self) -> tuple[str, ...]:
        return tuple(path.name for path in self.artifacts if not path.is_file())

    @property
    def cep_artifacts(self) -> tuple[Path, ...]:
        return (self.alert_sink_ddl, self.cep_job_sql, self.alert_level_sql, self.cep_conf)


def load_flink_settings() -> FlinkJobSettings:
    return FlinkJobSettings(
        sql_dir=SQL_DIR,
        source_ddl=SQL_DIR / "source_ddl.sql",
        sink_ddl=SQL_DIR / "sink_ddl.sql",
        job_sql=SQL_DIR / "speed_stats_job.sql",
        flink_conf=FLINK_ROOT / "conf" / "flink-conf.yaml",
        submit_script=FLINK_ROOT / "submit.sh",
        db_ddl=INNER_ROOT / "backend" / "sql" / "flink_speed_stats.sql",
        hop_slide_seconds=_integer("FLINK_HOP_SLIDE_SECONDS", DEFAULT_HOP_SLIDE_SECONDS, 10, 3600),
        hop_size_seconds=_integer("FLINK_HOP_SIZE_SECONDS", DEFAULT_HOP_SIZE_SECONDS, 30, 7200),
        min_vehicle_count=_integer("FLINK_MIN_VEHICLE_COUNT", DEFAULT_MIN_VEHICLE_COUNT, 1, 10000),
        watermark_delay_seconds=_integer("FLINK_WATERMARK_DELAY_SECONDS", 10, 0, 3600),
        kafka_brokers=os.getenv("FLINK_KAFKA_BROKERS", DEFAULT_KAFKA_BROKERS).strip()
        or DEFAULT_KAFKA_BROKERS,
        kafka_topic=os.getenv("KAFKA_TOPIC_TRAFFIC", DEFAULT_KAFKA_TOPIC).strip() or DEFAULT_KAFKA_TOPIC,
        kafka_group_id=os.getenv("FLINK_KAFKA_GROUP_ID", "flink-speed-stats-v1").strip()
        or "flink-speed-stats-v1",
        jdbc_url=os.getenv("FLINK_JDBC_URL", DEFAULT_JDBC_URL).strip() or DEFAULT_JDBC_URL,
        sink_table=os.getenv("FLINK_SINK_TABLE", "speed_stats").strip() or "speed_stats",
        web_ui_url=os.getenv("FLINK_WEB_UI_URL", DEFAULT_WEB_UI_URL).strip().rstrip("/") or DEFAULT_WEB_UI_URL,
        cep_kafka_group_id=os.getenv("FLINK_CEP_KAFKA_GROUP_ID", DEFAULT_CEP_KAFKA_GROUP_ID).strip()
        or DEFAULT_CEP_KAFKA_GROUP_ID,
        alert_sink_table=os.getenv("FLINK_ALERT_SINK_TABLE", DEFAULT_ALERT_SINK_TABLE).strip()
        or DEFAULT_ALERT_SINK_TABLE,
    )
