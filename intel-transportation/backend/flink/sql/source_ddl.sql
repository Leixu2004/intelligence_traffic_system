-- 9/21 实时计算 · Kafka Source 表定义
-- 数据源复用本项目已有的 traffic_stream topic（契约见 backend/events.py::TrafficObservation），
-- 而不是课件示例里的 camera_data topic；两者的字段映射记录在 backend/flink/README.md。
--
-- 时区约定：事件时间按 UTC 解析（生产者写入的是 ISO-8601 UTC 时间戳），
-- 因此同时固定 table.local-time-zone = UTC，保证写入 TimescaleDB 的 TIMESTAMPTZ 不发生偏移。

SET 'table.local-time-zone' = 'UTC';
SET 'pipeline.name' = 'traffic-speed-stats-hop';

CREATE TABLE kafka_traffic_observation (
    event_type    STRING,
    `time`        STRING COMMENT 'ISO-8601，例如 2026-09-21T06:30:00.123+00:00',
    vehicle_id    STRING,
    checkpoint_id STRING,
    camera_id     STRING,
    gps_lng       DOUBLE,
    gps_lat       DOUBLE,
    speed_kmh     DOUBLE,
    vehicle_type  STRING,
    confidence    DOUBLE,
    -- 取前 19 个字符（yyyy-MM-ddTHH:mm:ss），丢弃小数秒与时区后缀后按 UTC 解析。
    -- 第二个分支兼容 backend/simulate_edge_traffic.py 写入的空格分隔格式。
    event_time AS COALESCE(
        TO_TIMESTAMP(SUBSTRING(`time` FROM 1 FOR 19), 'yyyy-MM-dd''T''HH:mm:ss'),
        TO_TIMESTAMP(`time`, 'yyyy-MM-dd HH:mm:ss')
    ),
    WATERMARK FOR event_time AS event_time - INTERVAL '10' SECOND
) WITH (
    'connector' = 'kafka',
    'topic' = 'traffic_stream',
    'properties.bootstrap.servers' = 'kafka:29092',
    'properties.group.id' = 'flink-speed-stats-v1',
    'format' = 'json',
    'json.ignore-parse-errors' = 'true',
    'json.fail-on-missing-field' = 'false',
    'scan.startup.mode' = 'latest-offset'
);

-- 调试：回放历史数据时把 scan.startup.mode 改成 'earliest-offset'，
-- 或在作业里用 Hint：SELECT /*+ OPTIONS('scan.startup.mode' = 'earliest-offset') */ ...
-- 事件时间列 event_time 为 TIMESTAMP(3)（naive UTC），乱序容忍 10 秒。
