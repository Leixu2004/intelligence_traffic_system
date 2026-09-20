"""交付物静态校验：在没有 Flink 集群时，也能确认 SQL 作业自身是一致的。

这里只做文本层面的交叉核对（连接器参数、Watermark、窗口参数、列顺序、结果表 DDL、
配置键、口令泄漏），不解析 Flink 语法，因此**不能**替代 sql-client 的语法检查，
更不构成「集群已跑通」的证据。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import FlinkJobSettings

FORBIDDEN_SECRET_PATTERNS = (
    r"sk-[A-Za-z0-9]{16,}",
    r"'password'\s*=\s*'(?!postgres')[^']+'",
)

# Flink SQL 里 TIME 是保留字，引用本项目 JSON 字段 time 必须加反引号
RESERVED_IDENTIFIER_USAGE = "`time`"


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    message: str


def _interval_text(seconds: int) -> str:
    if seconds % 60 == 0:
        return f"INTERVAL '{seconds // 60}' MINUTE"
    return f"INTERVAL '{seconds}' SECOND"


def _read(path: Any) -> str:
    return path.read_text(encoding="utf-8")


def _strip_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _table_block(sql: str, table_name: str) -> str:
    match = re.search(
        rf"CREATE TABLE\s+{re.escape(table_name)}\s*\((.*?)\n\)\s*WITH",
        sql,
        re.DOTALL | re.IGNORECASE,
    )
    return match.group(1) if match else ""


NON_COLUMN_KEYWORDS = ("PRIMARY", "CONSTRAINT", "WATERMARK", "PERIOD", "EXCLUDE")


def _column_names(block: str) -> list[str]:
    names: list[str] = []
    for raw_line in block.splitlines():
        line = raw_line.strip().rstrip(",")
        if not line or line.startswith("--"):
            continue
        if line.split(None, 1)[0].upper() in NON_COLUMN_KEYWORDS:
            continue
        if re.search(r"\sAS\s", line):  # 计算列不是物理字段
            continue
        match = re.match(r"^`?([A-Za-z_][A-Za-z0-9_]*)`?\s+[A-Z]", line)
        if match:
            names.append(match.group(1))
    return names


def _select_columns(job_sql: str) -> list[str]:
    """取 INSERT INTO ... SELECT 的投影列名（按出现顺序）。"""
    match = re.search(
        r"INSERT\s+INTO\s+\w+\s+SELECT(.*?)FROM\s+TABLE",
        job_sql,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return []
    columns: list[str] = []
    depth = 0
    buffer = ""
    for char in match.group(1):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            columns.append(buffer)
            buffer = ""
            continue
        buffer += char
    columns.append(buffer)
    names: list[str] = []
    for column in columns:
        alias = re.search(r"AS\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", column.strip(), re.IGNORECASE)
        if alias:
            names.append(alias.group(1))
            continue
        head = re.match(r"^\s*`?([A-Za-z_][A-Za-z0-9_]*)`?", column.strip())
        if head:
            names.append(head.group(1))
    return names


def check_source_ddl(settings: FlinkJobSettings, sql: str) -> list[Check]:
    stripped = _strip_comments(sql)
    block = _table_block(stripped, "kafka_traffic_observation")
    delay = _interval_text(settings.watermark_delay_seconds)
    return [
        Check(
            "source.kafka_connector",
            "'connector' = 'kafka'" in stripped,
            "Source 必须使用 kafka 连接器",
        ),
        Check(
            "source.topic",
            f"'topic' = '{settings.kafka_topic}'" in stripped,
            f"topic 应为 {settings.kafka_topic}（本项目既有 traffic_stream，非课件的 camera-data）",
        ),
        Check(
            "source.brokers",
            f"'properties.bootstrap.servers' = '{settings.kafka_brokers}'" in stripped,
            f"bootstrap.servers 应为 {settings.kafka_brokers}",
        ),
        Check(
            "source.group_id",
            f"'properties.group.id' = '{settings.kafka_group_id}'" in stripped,
            f"group.id 应为 {settings.kafka_group_id}",
        ),
        Check(
            "source.watermark",
            "WATERMARK FOR event_time AS event_time - INTERVAL" in stripped and delay in stripped,
            f"需要事件时间 Watermark，乱序容忍 {delay}",
        ),
        Check(
            "source.event_time_column",
            "event_time AS COALESCE(" in stripped,
            "event_time 需由 JSON 字段 time 计算得到（兼容 T 分隔与空格分隔两种时间格式）",
        ),
        Check(
            "source.reserved_identifier",
            RESERVED_IDENTIFIER_USAGE in stripped,
            "time 是 Flink 保留字，必须写成 `time`",
        ),
        Check(
            "source.speed_field",
            "speed_kmh" in block,
            "Source 需声明本项目字段名 speed_kmh（课件示例为 speed）",
        ),
    ]


def check_sink_ddl(settings: FlinkJobSettings, sql: str) -> list[Check]:
    stripped = _strip_comments(sql)
    block = _table_block(stripped, settings.sink_table)
    return [
        Check("sink.jdbc_connector", "'connector' = 'jdbc'" in stripped, "Sink 必须使用 jdbc 连接器"),
        Check(
            "sink.url",
            f"'url' = '{settings.jdbc_url}'" in stripped,
            f"jdbc url 应为 {settings.jdbc_url}",
        ),
        Check(
            "sink.table_name",
            f"'table-name' = '{settings.sink_table}'" in stripped,
            f"table-name 应为 {settings.sink_table}",
        ),
        Check(
            "sink.primary_key",
            "PRIMARY KEY (window_start, camera_id) NOT ENFORCED" in stripped,
            "JDBC upsert 需要 PRIMARY KEY ... NOT ENFORCED",
        ),
        Check(
            "sink.columns",
            _column_names(block)
            == ["window_start", "camera_id", "avg_speed", "max_speed", "vehicle_count"],
            "Sink 列顺序需与作业投影一致",
        ),
    ]


def check_job_sql(settings: FlinkJobSettings, sql: str) -> list[Check]:
    stripped = _strip_comments(sql)
    slide = _interval_text(settings.hop_slide_seconds)
    size = _interval_text(settings.hop_size_seconds)
    having = settings.min_vehicle_count - 1
    return [
        Check(
            "job.insert_into_sink",
            f"INSERT INTO {settings.sink_table}" in stripped,
            f"作业需写入 {settings.sink_table}",
        ),
        Check(
            "job.hop_window",
            re.search(
                r"HOP\(\s*TABLE\s+kafka_traffic_observation,\s*DESCRIPTOR\(event_time\),"
                rf"\s*{re.escape(slide)},\s*{re.escape(size)}\s*\)",
                stripped,
            )
            is not None,
            "需 10 分钟窗口 / 5 分钟滑动步长（TABLE(HOP(...)) TVF 写法）",
        ),
        Check(
            "job.projection_matches_sink",
            _select_columns(stripped)
            == ["window_start", "camera_id", "avg_speed", "max_speed", "vehicle_count"],
            "投影列名与顺序需与 Sink 完全一致，否则 JDBC 按位置写入会错位",
        ),
        Check(
            "job.group_by",
            "GROUP BY window_start, window_end, camera_id" in stripped,
            "TVF 窗口聚合必须按 window_start, window_end, camera_id 分组",
        ),
        Check(
            "job.having",
            f"HAVING COUNT(*) > {having}" in stripped,
            f"过滤样本过少的窗口（HAVING COUNT(*) > {having}）",
        ),
        Check(
            "job.null_speed_filtered",
            "WHERE speed_kmh IS NOT NULL" in stripped,
            "必须剔除 speed_kmh 缺失的记录，否则 AVG 会把 NULL 计入分母",
        ),
        Check(
            "job.no_processing_time_filter",
            "NOW()" not in stripped.upper(),
            "不应按处理时间 NOW() 过滤事件时间（回放历史数据会被滤空）",
        ),
        Check(
            "job.avg_rounded",
            "ROUND(AVG(speed_kmh), 1)" in stripped,
            "平均车速保留 1 位小数，与课件输出示例一致",
        ),
    ]


def check_db_ddl(settings: FlinkJobSettings, sql: str) -> list[Check]:
    stripped = _strip_comments(sql)
    return [
        Check(
            "db.table_exists",
            f"CREATE TABLE IF NOT EXISTS {settings.sink_table}" in stripped,
            f"库侧需预建 {settings.sink_table}（Flink JDBC Sink 不建表）",
        ),
        Check(
            "db.primary_key",
            "PRIMARY KEY (window_start, camera_id)" in stripped,
            "库侧唯一约束是 upsert 覆盖而非重复插入的前提",
        ),
        Check(
            "db.window_start_timestamptz",
            re.search(r"\bwindow_start\s+TIMESTAMPTZ\s+NOT NULL", stripped) is not None,
            "结果列用 TIMESTAMPTZ，配合 table.local-time-zone = UTC 保存窗口起点",
        ),
        Check(
            "db.hypertable",
            re.search(
                rf"create_hypertable\(\s*'{re.escape(settings.sink_table)}',\s*'window_start'",
                stripped,
            )
            is not None,
            "结果表建成 TimescaleDB 超表",
        ),
    ]


def check_flink_conf(settings: FlinkJobSettings, text: str) -> list[Check]:
    def value_of(key: str) -> str:
        match = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", text, re.MULTILINE)
        return match.group(1) if match else ""

    return [
        Check("conf.slots", value_of("taskmanager.numberOfTaskSlots") == "4", "单机 4 slot"),
        Check("conf.parallelism", value_of("parallelism.default") == "1", "默认并行度 1"),
        Check(
            "conf.checkpoint_mode",
            value_of("execution.checkpointing.mode") == "EXACTLY_ONCE",
            "Checkpoint 需 EXACTLY_ONCE",
        ),
        Check(
            "conf.checkpoint_interval",
            value_of("execution.checkpointing.interval").rstrip("sS") == "15",
            "Checkpoint 间隔 15 秒（课件建议 10-30 秒）",
        ),
        Check("conf.rocksdb", value_of("state.backend.type") == "rocksdb", "大状态用 RocksDB Backend"),
        Check(
            "conf.utc_timezone",
            value_of("table.local-time-zone") == "UTC",
            "时区固定 UTC，与事件时间解析保持一致",
        ),
        Check("conf.rest_port", value_of("rest.port") == "8081", "Web UI 端口 8081"),
    ]


def check_no_secrets(settings: FlinkJobSettings) -> list[Check]:
    """只扫真正会被 sql-client 执行的文本。

    submit.sh 里出现 'password' = '${TIMESCALEDB_PASSWORD}' 这类占位是刻意的渲染逻辑，
    不在本项范围内（口令从环境变量注入，不写进 git）。
    """
    offenders: list[str] = []
    for path in (settings.source_ddl, settings.sink_ddl, settings.job_sql, settings.flink_conf, settings.db_ddl):
        if not path.is_file():
            continue
        text = _read(path)
        for pattern in FORBIDDEN_SECRET_PATTERNS:
            if re.search(pattern, text):
                offenders.append(path.name)
    return [
        Check(
            "secrets.none",
            not offenders,
            "SQL/配置交付物里不得出现真实口令或 AK" + (f"（发现 {offenders}）" if offenders else ""),
        )
    ]


def run_sql_checks(settings: FlinkJobSettings) -> dict[str, Any]:
    """返回 {checks, passed, failed, artifacts_missing}。缺文件时只报缺文件，不抛异常。"""
    checks: list[Check] = []
    missing = list(settings.missing_artifacts)

    groups = (
        (settings.source_ddl, lambda text: check_source_ddl(settings, text)),
        (settings.sink_ddl, lambda text: check_sink_ddl(settings, text)),
        (settings.job_sql, lambda text: check_job_sql(settings, text)),
        (settings.db_ddl, lambda text: check_db_ddl(settings, text)),
        (settings.flink_conf, lambda text: check_flink_conf(settings, text)),
    )
    for path, runner in groups:
        if not path.is_file():
            checks.append(Check(f"{path.name}.present", False, "文件缺失"))
            continue
        checks.extend(runner(_read(path)))
    checks.extend(check_no_secrets(settings))

    failed = [check for check in checks if not check.passed]
    return {
        "checks": [
            {"name": check.name, "passed": check.passed, "message": check.message}
            for check in checks
        ],
        "passed": not failed and not missing,
        "failed_count": len(failed),
        "total": len(checks),
        "artifacts_missing": missing,
    }
