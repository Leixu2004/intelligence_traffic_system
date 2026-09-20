-- 9/21 实时计算 · 核心作业：5 分钟滑动步长 / 10 分钟窗口车速统计
-- 执行顺序：source_ddl.sql → sink_ddl.sql → 本文件
--   ./bin/sql-client.sh -f backend/flink/sql/speed_stats_job.sql
--
-- 窗口语义：size=10 分钟、slide=5 分钟 → 相邻窗口重叠一半，
-- 一条过车记录同时归属 2 个窗口，因此每 5 分钟就刷新一次最近 10 分钟的车速。
-- HAVING COUNT(*) > 5 过滤掉样本过少、均值没有统计意义的窗口。
--
-- 与课件示例的差别（有意为之，原因记录在 backend/flink/README.md）：
--   1. 课件用旧式 GROUP BY HOP(...) + HOP_START(...)；这里用窗口 TVF TABLE(HOP(...))，
--      两者结果一致，但 TVF 是 Flink 1.13+ 推荐写法，旧式 Group Window 已标记废弃。
--   2. 不写 WHERE event_time > NOW() - INTERVAL '1' HOUR：NOW() 是处理时间，
--      回放/补数（earliest-offset）时事件时间往往早于当前时间，这个谓词会把整条流滤空。
--      只保留 speed_kmh IS NOT NULL，因为 traffic_stream 允许测速字段缺失。

INSERT INTO speed_stats
SELECT
    window_start,
    camera_id,
    ROUND(AVG(speed_kmh), 1) AS avg_speed,
    MAX(speed_kmh) AS max_speed,
    COUNT(*) AS vehicle_count
FROM TABLE(
    HOP(
        TABLE kafka_traffic_observation,
        DESCRIPTOR(event_time),
        INTERVAL '5' MINUTE,
        INTERVAL '10' MINUTE
    )
)
WHERE speed_kmh IS NOT NULL
GROUP BY window_start, window_end, camera_id
HAVING COUNT(*) > 5;
