"""交付物静态校验：在没有 Flink 集群时，也能确认 SQL 作业自身是一致的。

这里只做文本层面的交叉核对（连接器参数、Watermark、窗口参数、列顺序、结果表 DDL、
配置键、口令泄漏），不解析 Flink 语法，因此**不能**替代 sql-client 的语法检查，
更不构成「集群已跑通」的证据。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .config import (
    ALERT_LEVELS,
    AMBER_SPEED_KMH,
    CEP_MAX_SEGMENT_MINUTES,
    PURPLE_MINUTES,
    PURPLE_SPEED_KMH,
    RED_MINUTES,
    RED_SPEED_KMH,
    FlinkJobSettings,
)

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
    return _projection_names(match.group(1))


def _projection_names(body: str) -> list[str]:
    columns: list[str] = []
    depth = 0
    buffer = ""
    for char in body:
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


def _insert_projection(job_sql: str, table: str) -> list[str]:
    """预警作业的 INSERT ... SELECT 后面跟的是视图而非 TABLE(TUMBLE(...))，取到第一个 FROM 为止。"""
    match = re.search(
        rf"INSERT\s+INTO\s+{re.escape(table)}\s+SELECT(.*?)\bFROM\b",
        job_sql,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return []
    return _projection_names(match.group(1))


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


def check_cep_source_ddl(settings: FlinkJobSettings, sql: str) -> list[Check]:
    stripped = _strip_comments(sql)
    block = _table_block(stripped, "kafka_traffic_observation_cep")
    return [
        Check(
            "cep_source.table_declared",
            "CREATE TABLE kafka_traffic_observation_cep" in stripped,
            "CEP 作业需要独立的 Source 表 kafka_traffic_observation_cep",
        ),
        Check(
            "cep_source.group_id",
            f"'properties.group.id' = '{settings.cep_kafka_group_id}'" in stripped,
            f"CEP 需用自己的 group.id（{settings.cep_kafka_group_id}）；"
            "与车速作业共用组会互相抢 traffic_stream 的分区，两边都吃不到完整数据",
        ),
        Check(
            "cep_source.group_id_differs_from_speed_job",
            settings.cep_kafka_group_id != settings.kafka_group_id,
            "CEP 与车速作业的 group.id 必须不同",
        ),
        Check(
            "cep_source.topic",
            stripped.count(f"'topic' = '{settings.kafka_topic}'") == 2,
            f"复用本项目 topic {settings.kafka_topic}（课件示例为 camera-data）",
        ),
        Check(
            "cep_source.watermark",
            "WATERMARK FOR event_time AS event_time - INTERVAL" in block,
            "WITHIN 靠事件时间 Watermark 推进，CEP Source 必须声明 Watermark",
        ),
    ]


def check_alert_sink_ddl(settings: FlinkJobSettings, sql: str) -> list[Check]:
    stripped = _strip_comments(sql)
    block = _table_block(stripped, settings.alert_sink_table)
    return [
        Check("alert_sink.jdbc_connector", "'connector' = 'jdbc'" in stripped, "预警 Sink 必须使用 jdbc 连接器"),
        Check(
            "alert_sink.table_name",
            f"'table-name' = '{settings.alert_sink_table}'" in stripped,
            f"table-name 应为 {settings.alert_sink_table}",
        ),
        Check(
            "alert_sink.primary_key",
            "PRIMARY KEY (camera_id, start_time, alert_level) NOT ENFORCED" in stripped,
            "JDBC upsert 需要主键声明（NOT ENFORCED）",
        ),
        Check(
            "alert_sink.columns",
            _column_names(block)
            == [
                "camera_id",
                "start_time",
                "end_time",
                "alert_level",
                "avg_speed",
                "min_speed",
                "low_cnt",
                "duration_min",
                "source",
            ],
            "Sink 列顺序需与预警作业投影一致，否则按位置写入会错位",
        ),
    ]


def check_cep_job_sql(settings: FlinkJobSettings, sql: str) -> list[Check]:
    stripped = _strip_comments(sql)
    within_cap = _interval_text(int(CEP_MAX_SEGMENT_MINUTES * 60))
    return [
        Check(
            "cep.two_views",
            "CREATE VIEW cep_low_speed AS" in stripped and "CREATE VIEW cep_critical AS" in stripped,
            "红级（<20 持续 10min）与紫级（<10 持续 20min）各自一个 MATCH_RECOGNIZE 视图",
        ),
        Check(
            "cep.match_recognize_used",
            stripped.count("MATCH_RECOGNIZE(") == 2,
            "两个模式都应走 MATCH_RECOGNIZE（真 CEP），而不是用窗口近似",
        ),
        Check(
            "cep.partition_by_camera",
            stripped.count("PARTITION BY camera_id") == 2 and stripped.count("ORDER BY event_time") == 2,
            "必须按 camera_id 分区、按事件时间排序，跨摄像头的记录不能连成同一段低速",
        ),
        Check(
            "cep.one_row_per_match",
            stripped.count("ONE ROW PER MATCH") == 2,
            "每次匹配输出一行，避免生成历史子序列",
        ),
        Check(
            "cep.skip_past_last_row",
            stripped.count("AFTER MATCH SKIP PAST LAST ROW") == 2,
            "默认 SKIP TO NEXT ROW 会让同一段拥堵被不同起点反复命中，预警表里全是重叠记录",
        ),
        Check(
            "cep.within_is_state_cap",
            stripped.count(f"WITHIN {within_cap}") == 2,
            f"两个模式都用同一个 WITHIN（{within_cap}）：它只是状态上限，"
            f"「持续 {RED_MINUTES:g}/{PURPLE_MINUTES:g} 分钟」由 alert_level.sql 的 duration_min 判",
        ),
        Check(
            "cep.low_speed_threshold",
            f"DEFINE SLOW AS SLOW.speed_kmh < {RED_SPEED_KMH:g}" in stripped,
            f"红级低速条件应为 speed < {RED_SPEED_KMH:g}",
        ),
        Check(
            "cep.critical_threshold",
            f"DEFINE CRIT AS CRIT.speed_kmh < {PURPLE_SPEED_KMH:g}" in stripped,
            f"紫级条件应为 speed < {PURPLE_SPEED_KMH:g}",
        ),
        Check(
            "cep.pattern_clause",
            "PATTERN (SLOW+ END_SLOW)" in stripped and "PATTERN (CRIT+ END_CRIT)" in stripped,
            "MATCH_RECOGNIZE 必须有 PATTERN 子句（缺了 sql-client 会直接报解析错误）",
        ),
        Check(
            "cep.closer_uses_opposite_condition",
            "END_SLOW AS END_SLOW.speed_kmh >= 20" in stripped
            and "END_CRIT AS END_CRIT.speed_kmh >= 10" in stripped,
            "收尾事件的条件必须与段内条件互斥（>=阈值）：Flink 的贪心量词不回溯，"
            "收尾变量若写同样的 <阈值，一段都匹配不出来（实测 Match 读 176 条输出 0 条）",
        ),
        Check(
            "cep.greedy_not_last_element",
            re.search(r"PATTERN \([^()]*(?:[A-Z_]+(?:\{[0-9]+,\}+|\+) *(?:\)|$))", stripped) is None,
            "贪心量词（+ 或 {n,}）不能做模式最后一个元素：集群实测报 "
            '"Greedy quantifiers are not allowed as the last element of a Pattern yet"',
        ),
        Check(
            "cep.measures_exclude_closer",
            all(
                token in stripped
                for token in (
                    "FIRST(SLOW.event_time)",
                    "LAST(SLOW.event_time)",
                    "AVG(SLOW.speed_kmh)",
                    "MIN(SLOW.speed_kmh)",
                    "COUNT(SLOW.speed_kmh)",
                )
            )
            # MATCH_RECOGNIZE 的 MEASURES 只认 Calcite 注册的聚合名，AVERAGE 会报
            # "No match found for function signature AVERAGE"——集群实测踩过，锁在静态校验里。
            and "AVERAGE(" not in stripped
            and "END_SLOW.event_time" not in stripped,
            "MEASURES 只统计段内低速事件（收尾那条不低速，算进去会把均速抬高），且均速写 AVG 而非 AVERAGE",
        ),
        Check(
            "cep.rowtime_stripped_for_union",
            "CAST(start_time AS TIMESTAMP(3))" in stripped
            and "CAST(end_time AS TIMESTAMP(3))" in stripped,
            "FIRST/LAST 输出带 *ROWTIME*，不 CAST 成普通 TIMESTAMP 就无法与窗口分支 UNION ALL",
        ),
        Check(
            "cep.reads_cep_source",
            stripped.count("FROM kafka_traffic_observation_cep") == 2,
            "两个模式都读 CEP 专用 Source",
        ),
        Check(
            "cep.null_speed_excluded",
            "WHERE low_cnt >= 2" in stripped,
            "视图层再滤掉单条匹配，与 {2,} 形成双保险",
        ),
    ]


def check_alert_level_sql(settings: FlinkJobSettings, sql: str) -> list[Check]:
    stripped = _strip_comments(sql)
    return [
        Check(
            "alert_level.case_order_purple_first",
            re.search(
                rf"CASE\s+WHEN avg_speed < {PURPLE_SPEED_KMH:g} AND duration_min >= {PURPLE_MINUTES:g}\s+THEN 'PURPLE'\s+"
                rf"WHEN avg_speed < {RED_SPEED_KMH:g} AND duration_min >= {RED_MINUTES:g}\s+THEN 'RED'\s+"
                rf"WHEN avg_speed < {AMBER_SPEED_KMH:g}\s+THEN 'AMBER'\s+"
                r"ELSE 'GREEN'",
                stripped,
            )
            is not None,
            "四级判定顺序必须紫→红→黄→绿（CASE 自上而下短路，顺序错会让紫级被判成红级）",
        ),
        Check(
            "alert_level.levels_complete",
            all(f"'{level}'" in stripped for level in ALERT_LEVELS),
            f"四级 {ALERT_LEVELS} 都要在 SQL 里出现",
        ),
        Check(
            "alert_level.pipeline_name",
            "SET 'pipeline.name' = 'traffic-cep-alert'" in stripped,
            "在 Web UI 上要能把 CEP 作业和车速作业区分开",
        ),
        Check(
            "alert_level.union_all_three_branches",
            stripped.count("UNION ALL") == 2
            and "FROM cep_low_speed" in stripped
            and "FROM cep_critical" in stripped,
            "红/紫来自 CEP 视图，黄/绿来自滚动窗口，需 UNION ALL 汇总",
        ),
        Check(
            "alert_level.window_branch_excludes_severe",
            "HAVING AVG(speed_kmh) >= %g" % RED_SPEED_KMH in stripped,
            f"窗口分支要滤掉均速 < {RED_SPEED_KMH:g} 的分钟：持续语义归 CEP，"
            "否则同一时刻会有两条口径不同的记录争同一个主键",
        ),
        Check(
            "alert_level.tumble_one_minute",
            "TUMBLE(" in stripped and "INTERVAL '1' MINUTE" in stripped,
            "黄/绿用 1 分钟滚动窗口",
        ),
        Check(
            "alert_level.null_speed_filtered",
            "WHERE speed_kmh IS NOT NULL" in stripped,
            "窗口分支必须剔除缺失测速，否则 AVG 把 NULL 计入分母",
        ),
        Check(
            "alert_level.insert_into_sink",
            f"INSERT INTO {settings.alert_sink_table}" in stripped,
            f"需写入 {settings.alert_sink_table}",
        ),
        Check(
            "alert_level.projection_matches_sink",
            _insert_projection(stripped, settings.alert_sink_table)
            == [
                "camera_id",
                "start_time",
                "end_time",
                "alert_level",
                "avg_speed",
                "min_speed",
                "low_cnt",
                "duration_min",
                "source",
            ],
            "投影列名与顺序需与 Sink 完全一致",
        ),
        Check(
            "alert_level.no_processing_time_filter",
            "NOW()" not in stripped.upper(),
            "不应按处理时间过滤事件时间（回放历史数据会被滤空）",
        ),
    ]


def check_alerts_db_ddl(settings: FlinkJobSettings, sql: str) -> list[Check]:
    stripped = _strip_comments(sql)
    return [
        Check(
            "alert_db.table_exists",
            f"CREATE TABLE IF NOT EXISTS {settings.alert_sink_table}" in stripped,
            f"库侧需预建 {settings.alert_sink_table}（Flink JDBC Sink 不建表）",
        ),
        Check(
            "alert_db.primary_key",
            "PRIMARY KEY (camera_id, start_time, alert_level)" in stripped,
            "同一段拥堵可能同时出 RED 与 PURPLE，主键要含 alert_level 才能都留下",
        ),
        Check(
            "alert_db.hypertable",
            re.search(
                rf"create_hypertable\(\s*'{re.escape(settings.alert_sink_table)}',\s*'start_time'",
                stripped,
            )
            is not None,
            "预警表按 start_time 建超表",
        ),
        Check(
            "alert_db.start_time_timestamptz",
            re.search(r"\bstart_time\s+TIMESTAMPTZ\s+NOT NULL", stripped) is not None,
            "start_time 用 TIMESTAMPTZ NOT NULL（超表分区列必须非空）",
        ),
    ]


def check_cep_conf(settings: FlinkJobSettings, text: str) -> list[Check]:
    def value_of(key: str) -> str:
        match = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", text, re.MULTILINE)
        return match.group(1) if match else ""

    ttl_ms = _millis(value_of("table.exec.state.ttl"))
    longest_within_ms = int(CEP_MAX_SEGMENT_MINUTES * 60_000)
    return [
        Check(
            "cep_conf.state_ttl_gt_within",
            ttl_ms > longest_within_ms,
            f"table.exec.state.ttl 必须大于最长的 WITHIN（{longest_within_ms // 60000} 分钟），"
            "否则未闭合的低速段会被提前清掉，紫级永远不出",
        ),
        Check("cep_conf.rocksdb", value_of("state.backend.type") == "rocksdb", "CEP 状态大，用 RocksDB"),
        Check(
            "cep_conf.incremental_checkpoints",
            value_of("state.backend.incremental") == "true",
            "RocksDB 配合增量 checkpoint",
        ),
        Check(
            "cep_conf.parallelism_within_slots",
            int(value_of("parallelism.default") or 0) <= int(value_of("taskmanager.numberOfTaskSlots") or 0),
            "并行度不得超过 slot 总数，否则作业卡在 CREATED",
        ),
        Check(
            "cep_conf.checkpoint_mode",
            value_of("execution.checkpointing.mode") == "EXACTLY_ONCE",
            "预警漏报/重报都不可接受，需 EXACTLY_ONCE",
        ),
        Check(
            "cep_conf.idle_timeout",
            value_of("table.exec.source.idle-timeout").rstrip("sS") == "3",
            "空闲分区不推进 Watermark 会让低速段一直等待闭合，需配 idle-timeout",
        ),
        Check(
            "cep_conf.utc_timezone",
            value_of("table.local-time-zone") == "UTC",
            "与车速作业保持同一时区口径，写 TIMESTAMPTZ 不偏移",
        ),
    ]


def _millis(raw: str) -> int:
    match = re.match(r"^(\d+(?:\.\d+)?)\s*(ms|s|min|h)?$", raw.strip().lower())
    if not match:
        return -1
    value = float(match.group(1))
    unit = match.group(2) or "ms"
    factor = {"ms": 1, "s": 1000, "min": 60_000, "h": 3_600_000}[unit]
    return int(value * factor)


def check_cep_test_data(settings: FlinkJobSettings) -> list[Check]:
    """test_data.json 是课件列出的交付物：一份能触发各级预警的模拟输入。"""
    path = settings.cep_test_data
    if not path.is_file():
        return [Check("cep_test_data.present", False, "文件缺失")]
    try:
        payload = json.loads(_read(path))
    except json.JSONDecodeError as error:
        return [Check("cep_test_data.valid_json", False, f"不是合法 JSON：{error}")]

    events = payload.get("events") if isinstance(payload, dict) else payload
    events = events or []
    cameras = {event.get("camera_id") for event in events if isinstance(event, dict)}
    speeds = [event.get("speed_kmh") for event in events if isinstance(event, dict)]
    return [
        Check("cep_test_data.valid_json", isinstance(payload, dict) and bool(events), "需包含 events 数组"),
        Check(
            "cep_test_data.labelled_simulation",
            payload.get("simulation") is True or payload.get("source") == "simulation",
            "模拟数据必须显式标注，不得被当成真实测速或检测精度证据",
        ),
        Check("cep_test_data.cameras", len(cameras) >= 3, "至少覆盖 3 个摄像头，能演示 PARTITION BY 的隔离效果"),
        Check(
            "cep_test_data.spans_levels",
            any(isinstance(v, (int, float)) and v < PURPLE_SPEED_KMH for v in speeds)
            and any(isinstance(v, (int, float)) and PURPLE_SPEED_KMH <= v < RED_SPEED_KMH for v in speeds)
            and any(isinstance(v, (int, float)) and RED_SPEED_KMH <= v < AMBER_SPEED_KMH for v in speeds)
            and any(isinstance(v, (int, float)) and v >= AMBER_SPEED_KMH for v in speeds),
            f"需覆盖 {PURPLE_SPEED_KMH:g} 以下 / {RED_SPEED_KMH:g} / {AMBER_SPEED_KMH:g} 以上四段，能触发四级",
        ),
        Check("cep_test_data.has_null_speed", any(v is None for v in speeds), "需含一条缺失测速的记录，验证 NULL 过滤"),
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
    for path in (
        settings.source_ddl,
        settings.sink_ddl,
        settings.job_sql,
        settings.flink_conf,
        settings.db_ddl,
        settings.alert_sink_ddl,
        settings.cep_job_sql,
        settings.alert_level_sql,
        settings.cep_conf,
        settings.alerts_db_ddl,
    ):
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
        (settings.source_ddl, lambda text: check_source_ddl(settings, text) + check_cep_source_ddl(settings, text)),
        (settings.sink_ddl, lambda text: check_sink_ddl(settings, text)),
        (settings.job_sql, lambda text: check_job_sql(settings, text)),
        (settings.db_ddl, lambda text: check_db_ddl(settings, text)),
        (settings.flink_conf, lambda text: check_flink_conf(settings, text)),
        (settings.alert_sink_ddl, lambda text: check_alert_sink_ddl(settings, text)),
        (settings.cep_job_sql, lambda text: check_cep_job_sql(settings, text)),
        (settings.alert_level_sql, lambda text: check_alert_level_sql(settings, text)),
        (settings.alerts_db_ddl, lambda text: check_alerts_db_ddl(settings, text)),
        (settings.cep_conf, lambda text: check_cep_conf(settings, text)),
    )
    for path, runner in groups:
        if not path.is_file():
            checks.append(Check(f"{path.name}.present", False, "文件缺失"))
            continue
        checks.extend(runner(_read(path)))
    checks.extend(check_cep_test_data(settings))
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
