-- 9/21 复杂事件处理 · 预警级别判定与写出（绿/黄/红/紫四级）
-- 执行顺序：source_ddl.sql → alert_sink.sql → congestion_cep.sql → 本文件
--   ./bin/sql-client.sh -f backend/flink/sql/alert_level.sql
--
-- 分级口径完全照课件第 7 页：
--   绿 GREEN  speed ≥ 40            正常通行，无需预警
--   黄 AMBER  20 < speed < 40       轻度拥堵，关注提醒
--   红 RED    speed < 20 持续 10min  严重拥堵，启动响应
--   紫 PURPLE speed < 10 持续 20min  事件升级，应急调度
-- 判定顺序由 CASE WHEN 从上到下保证：先判最严格的紫，再红、黄、绿。

SET 'pipeline.name' = 'traffic-cep-alert';

CREATE VIEW alert_levels AS
SELECT
    camera_id,
    start_time,
    end_time,
    avg_speed,
    min_speed,
    low_cnt,
    duration_min,
    source,
    CASE
        WHEN avg_speed < 10 AND duration_min >= 20 THEN 'PURPLE'
        WHEN avg_speed < 20 AND duration_min >= 10 THEN 'RED'
        WHEN avg_speed < 40                        THEN 'AMBER'
        ELSE 'GREEN'
    END AS alert_level
FROM (
    -- 红/紫来源：CEP 低速段。duration 取段首末事件之差，不用 TIMESTAMPDIFF 的秒级余量，
    -- 因为 WITHIN 已把段长压在 10/20 分钟内，分钟粒度才与课件阈值同量纲。
    SELECT
        camera_id,
        start_time,
        end_time,
        ROUND(avg_speed, 1) AS avg_speed,
        min_speed,
        low_cnt,
        CAST(TIMESTAMPDIFF(MINUTE, start_time, end_time) AS DOUBLE) AS duration_min,
        source
    FROM cep_low_speed
    UNION ALL
    SELECT
        camera_id,
        start_time,
        end_time,
        ROUND(avg_speed, 1) AS avg_speed,
        min_speed,
        low_cnt,
        CAST(TIMESTAMPDIFF(MINUTE, start_time, end_time) AS DOUBLE) AS duration_min,
        source
    FROM cep_critical
    UNION ALL
    -- 黄/绿来源：1 分钟滚动窗口的均速。这里主动滤掉 avg < 20 的窗口——
    -- 那是 CEP 的职责（它才持有「持续」语义），窗口分支只补轻度拥堵与正常两级，
    -- 否则同一时刻会有 RED（窗口）和 RED（CEP）两条口径不同的记录争同一个主键。
    --
    -- 三路 UNION 前必须把 window_start/window_end 的时间属性摘掉（CAST 成普通 TIMESTAMP(3)）：
    -- 窗口 TVF 的 window_start 带 ROWTIME，MATCH_RECOGNIZE 的输出不带，
    -- Flink 直接拒绝「Union fields with time attributes requires same types」。
    SELECT
        camera_id,
        CAST(window_start AS TIMESTAMP(3))          AS start_time,
        CAST(window_end - INTERVAL '1' SECOND AS TIMESTAMP(3)) AS end_time,
        ROUND(AVG(speed_kmh), 1)                    AS avg_speed,
        MIN(speed_kmh)                              AS min_speed,
        COUNT(*)                                    AS low_cnt,
        CAST(TIMESTAMPDIFF(
            SECOND,
            CAST(window_start AS TIMESTAMP(3)),
            CAST(window_end AS TIMESTAMP(3))
        ) AS DOUBLE) / 60.0                                                    AS duration_min,
        'window'                                    AS source
    FROM TABLE(
        TUMBLE(
            TABLE kafka_traffic_observation_cep,
            DESCRIPTOR(event_time),
            INTERVAL '1' MINUTE
        )
    )
    WHERE speed_kmh IS NOT NULL
    GROUP BY window_start, window_end, camera_id
    HAVING AVG(speed_kmh) >= 20
) AS t;

INSERT INTO traffic_alerts
SELECT
    camera_id,
    start_time,
    end_time,
    alert_level,
    avg_speed,
    min_speed,
    low_cnt,
    duration_min,
    source
FROM alert_levels;

-- 课件第 7 页还有一条「连续 5 分钟 speed > 30 自动降一级」的降级规则。
-- 它要读上一条预警的状态才能改写级别，纯 SQL 的 CASE WHEN 是无状态的，
-- 因此本作业不做降级；降级与推送去重放在 backend/flink/alert_consumer.py（见 README 已知限制）。
