-- 创建按分钟聚合的物化视图（统计车流量与平均速度）
CREATE MATERIALIZED VIEW IF NOT EXISTS checkpoint_traffic_1m
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket(INTERVAL '1 minute', time) AS bucket,
    checkpoint_id,
    COUNT(vehicle_id) AS total_vehicles,
    AVG(speed_kmh)    AS avg_speed
FROM traffic_gps
GROUP BY bucket, checkpoint_id;

-- 添加自动刷新策略，每分钟自动后台计算更新
SELECT add_continuous_aggregate_policy('checkpoint_traffic_1m',
    start_offset => INTERVAL '3 hours',
    end_offset   => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute'
);

-- 添加数据保留策略（自动清理30天前的原始明细数据以节省边缘存储）
SELECT add_retention_policy('traffic_gps', INTERVAL '30 days');