-- 9/21 实时计算 · Flink 结果表（TimescaleDB / PostgreSQL 侧）
-- 由 docker-compose 挂载到 /docker-entrypoint-initdb.d/ 在**新数据卷**上自动执行；
-- 已有数据卷时手动执行一次：
--   docker compose exec -T timescaledb psql -U postgres -d traffic < backend/sql/flink_speed_stats.sql
-- 本文件只建表，不改动既有 traffic_gps / plate_recognitions / traffic_violations。

CREATE TABLE IF NOT EXISTS speed_stats (
    window_start  TIMESTAMPTZ NOT NULL,
    camera_id     VARCHAR(64) NOT NULL,
    avg_speed     DOUBLE PRECISION,
    max_speed     DOUBLE PRECISION,
    vehicle_count BIGINT,
    PRIMARY KEY (window_start, camera_id)
);

-- 与 Flink JDBC Sink 的 PRIMARY KEY (window_start, camera_id) 对应：
-- 没有这个唯一约束，窗口结果的重算（retract/upsert）会变成重复插入而不是覆盖。

SELECT create_hypertable(
    'speed_stats', 'window_start',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_speed_stats_camera_time
    ON speed_stats (camera_id, window_start DESC);

DO $$
BEGIN
    PERFORM add_retention_policy('speed_stats', INTERVAL '180 days');
EXCEPTION WHEN duplicate_object THEN
    NULL;
END $$;
