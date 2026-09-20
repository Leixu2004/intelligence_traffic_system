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
DEFAULT_WEB_UI_URL = "http://localhost:8081"


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

    @property
    def artifacts(self) -> tuple[Path, ...]:
        return (self.source_ddl, self.sink_ddl, self.job_sql, self.flink_conf, self.submit_script)

    @property
    def missing_artifacts(self) -> tuple[str, ...]:
        return tuple(path.name for path in self.artifacts if not path.is_file())


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
    )
