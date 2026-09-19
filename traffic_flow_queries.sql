-- 1. 每小时各路口车流量
SELECT time_bucket('1 hour', time) AS hr, camera_id, COUNT(*) AS flow, COUNT(DISTINCT plate) AS uniq FROM traffic_data WHERE time >= CURRENT_DATE GROUP BY hr, camera_id ORDER BY hr DESC, flow DESC;
-- 2. 高峰时段识别(TOP 5)
SELECT bucket, SUM(cnt) AS total FROM traffic_5min WHERE bucket >= CURRENT_DATE GROUP BY bucket ORDER BY total DESC LIMIT 5;
-- 3. 车型占比统计
SELECT vehicle_type, COUNT(*)*100.0/SUM(COUNT(*)) OVER() AS pct FROM traffic_data WHERE time >= CURRENT_DATE GROUP BY vehicle_type;
