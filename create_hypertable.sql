-- 创建普通表
CREATE TABLE traffic_data (
  time          TIMESTAMPTZ NOT NULL,
  camera_id     TEXT NOT NULL,
  plate         TEXT,
  vehicle_type  TEXT,
  confidence    REAL,
  bbox_x1       INT,
  bbox_y1       INT,
  bbox_x2       INT,
  bbox_y2       INT
);

-- 转为超表(按time自动分区，1小时一分)
SELECT create_hypertable('traffic_data', 'time', chunk_time_interval => INTERVAL '1 hour');

-- 启用压缩 (PPT 第6页要求)
ALTER TABLE traffic_data SET (
  timescaledb.compress,
  timescaledb.compress_segmentby = 'camera_id'
);
SELECT add_compression_policy('traffic_data', INTERVAL '7 days');

-- 创建连续聚合: traffic_5min (PPT 第6页要求)
CREATE MATERIALIZED VIEW traffic_5min
WITH (timescaledb.continuous) AS
SELECT
  time_bucket('5 min', time) AS bucket,
  camera_id,
  COUNT(*) AS cnt,
  AVG(confidence) AS avg_conf
FROM traffic_data
GROUP BY bucket, camera_id
WITH NO DATA;

-- 自动刷新策略
SELECT add_continuous_aggregate_policy('traffic_5min',
  start_offset => INTERVAL '1 day',
  end_offset   => INTERVAL '5 min',
  schedule_interval => INTERVAL '1 min');

-- 自动删除30天前数据 (保留策略)
SELECT add_retention_policy('traffic_data', drop_after => INTERVAL '30 days');
