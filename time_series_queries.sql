-- 1. 时间范围内车辆检测记录
SELECT * FROM traffic_data WHERE time BETWEEN '2026-08-23 08:00' AND '2026-08-23 10:00' ORDER BY time DESC;
-- 2. 按时间分组统计流量(time_bucket)
SELECT time_bucket('5 min', time) AS bucket, camera_id, COUNT(*) AS vehicle_count FROM traffic_data WHERE time >= NOW() - INTERVAL '1 hour' GROUP BY bucket, camera_id ORDER BY bucket DESC;
-- 3. 车型分布统计
SELECT vehicle_type, COUNT(*) FROM traffic_data WHERE time >= CURRENT_DATE GROUP BY vehicle_type;
