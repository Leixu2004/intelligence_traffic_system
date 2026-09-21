-- 9/21 复杂事件处理 · 预警结果表（TimescaleDB / PostgreSQL 侧）
-- 与 speed_stats 同理：Flink JDBC Sink 不建表，表与主键必须先存在，
-- 否则 upsert 语义退化成重复插入。由 docker-compose 挂载到
-- /docker-entrypoint-initdb.d/ 在新数据卷上自动执行；已有数据卷时手动跑一次：
--   docker compose exec -T timescaledb psql -U postgres -d traffic < backend/sql/flink_traffic_alerts.sql
--
-- 主键取 (camera_id, start_time, alert_level)：同一段拥堵可能被 RED 和 PURPLE 各判一次，
-- 级别不同的两条记录都要留下；同级别重算（retract/upsert）则覆盖而不是追加。

CREATE TABLE IF NOT EXISTS traffic_alerts (
    camera_id    VARCHAR(64)  NOT NULL,
    start_time   TIMESTAMPTZ  NOT NULL,
    end_time     TIMESTAMPTZ,
    alert_level  VARCHAR(16)  NOT NULL,
    avg_speed    DOUBLE PRECISION,
    min_speed    DOUBLE PRECISION,
    low_cnt      BIGINT,
    duration_min DOUBLE PRECISION,
    source       VARCHAR(16)  NOT NULL DEFAULT 'cep',
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (camera_id, start_time, alert_level)
);

SELECT create_hypertable(
    'traffic_alerts', 'start_time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_traffic_alerts_camera_time
    ON traffic_alerts (camera_id, start_time DESC);

CREATE INDEX IF NOT EXISTS idx_traffic_alerts_level
    ON traffic_alerts (alert_level, start_time DESC);

DO $$
BEGIN
    PERFORM add_retention_policy('traffic_alerts', INTERVAL '180 days');
EXCEPTION WHEN duplicate_object THEN
    NULL;
END $$;
