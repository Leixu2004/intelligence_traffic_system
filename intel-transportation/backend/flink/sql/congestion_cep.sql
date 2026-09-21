-- 9/21 复杂事件处理 · 拥堵检测 CEP 作业（PATTERN + WITHIN + DEFINE）
-- 依赖：source_ddl.sql 里的 kafka_traffic_observation_cep，alert_sink.sql 里的 traffic_alerts。
-- 本文件只建视图，写出动作在 alert_level.sql；两份合起来构成课件所说的「CEP 完整预警作业」。
--
-- 与课件示例（PATTERN (LOW_SPEED+) WITHIN INTERVAL '10' MINUTE）的四处差别，
-- 前三条都是 2026-09-21 在 Flink 1.19 集群上实测出来的，不是照抄语法：
--   1. 段尾要有收尾事件：PATTERN (SLOW+ END_SLOW)，END_SLOW 是「速度回到阈值以上」那一条。
--      Flink 的贪心量词不回溯，写成 SLOW{2,} 或 SLOW{1,} SLOW 这类「贪心元素当最后一个」
--      要么直接报错（Greedy quantifiers are not allowed as the last element），
--      要么一条都不匹配（实测 Match 算子读 176 条、输出 0 条）。
--      收尾事件只用来闭合，不进 MEASURES，所以段首段尾仍取 SLOW 的第一个/最后一个事件。
--   2. 因此一段拥堵要等车流恢复才会输出——这正是「拥堵结束」的时刻，但也意味着
--      直到数据流结束都还在拥堵的相机不会出预警（README 已知限制里记着）。
--   3. WITHIN 换成分级无关的状态上限（60 分钟，见 backend/flink/config.py::CEP_MAX_SEGMENT_MINUTES）：
--      段长由车流恢复决定，可以超过 10/20 分钟；「持续多久」由 alert_level.sql 的
--      duration_min 判，不再靠 WITHIN 截断。
--   4. 至少两条低速才算一段：单条就报警会兑现不了课件自己写的「持续」，
--      所以量词用 SLOW+ 之外还在视图层加 WHERE low_cnt >= 2 双保险。
--
-- 另外两处与引擎语义有关，改 SQL 时别顺手删掉：
--   * AFTER MATCH SKIP PAST LAST ROW：默认 SKIP TO NEXT ROW 会让同一段拥堵被不同起点反复命中。
--   * 外层 CAST(... AS TIMESTAMP(3))：FIRST/LAST 取出的行时间列仍带 *ROWTIME* 属性，
--     不摘掉就无法与 alert_level.sql 的窗口分支 UNION ALL。
--
-- 速度缺失（speed_kmh 为 NULL，未做测速标定的记录）既不算低速也不算收尾事件，
-- 会把正在累积的低速段打断，该段不出预警；这是 DEFINE 遇 NULL 按不匹配处理的直接后果。

CREATE VIEW cep_low_speed AS
SELECT
    camera_id,
    CAST(start_time AS TIMESTAMP(3)) AS start_time,
    CAST(end_time AS TIMESTAMP(3))   AS end_time,
    avg_speed,
    min_speed,
    low_cnt,
    source
FROM (
    SELECT
        camera_id,
        start_time,
        end_time,
        avg_speed,
        min_speed,
        low_cnt,
        'cep' AS source
    FROM kafka_traffic_observation_cep
    MATCH_RECOGNIZE(
        PARTITION BY camera_id
        ORDER BY event_time
        MEASURES
            FIRST(SLOW.event_time)  AS start_time,
            LAST(SLOW.event_time)   AS end_time,
            AVG(SLOW.speed_kmh)     AS avg_speed,
            MIN(SLOW.speed_kmh)     AS min_speed,
            COUNT(SLOW.speed_kmh)   AS low_cnt
        ONE ROW PER MATCH
        AFTER MATCH SKIP PAST LAST ROW
        PATTERN (SLOW+ END_SLOW)
        WITHIN INTERVAL '60' MINUTE
        DEFINE SLOW AS SLOW.speed_kmh < 20,
               END_SLOW AS END_SLOW.speed_kmh >= 20
    ) AS t
) AS m
WHERE low_cnt >= 2;

-- 紫级要「speed < 10 持续 20 分钟」，条件更严、段更长，所以单开一个模式：
-- 若并入上面的视图用 SUBSET 表达，红级分支会被紫级的收尾条件一起改掉，级别判定会失真。

CREATE VIEW cep_critical AS
SELECT
    camera_id,
    CAST(start_time AS TIMESTAMP(3)) AS start_time,
    CAST(end_time AS TIMESTAMP(3))   AS end_time,
    avg_speed,
    min_speed,
    low_cnt,
    source
FROM (
    SELECT
        camera_id,
        start_time,
        end_time,
        avg_speed,
        min_speed,
        low_cnt,
        'cep' AS source
    FROM kafka_traffic_observation_cep
    MATCH_RECOGNIZE(
        PARTITION BY camera_id
        ORDER BY event_time
        MEASURES
            FIRST(CRIT.event_time)  AS start_time,
            LAST(CRIT.event_time)   AS end_time,
            AVG(CRIT.speed_kmh)     AS avg_speed,
            MIN(CRIT.speed_kmh)     AS min_speed,
            COUNT(CRIT.speed_kmh)   AS low_cnt
        ONE ROW PER MATCH
        AFTER MATCH SKIP PAST LAST ROW
        PATTERN (CRIT+ END_CRIT)
        WITHIN INTERVAL '60' MINUTE
        DEFINE CRIT AS CRIT.speed_kmh < 10,
               END_CRIT AS END_CRIT.speed_kmh >= 10
    ) AS t
) AS m
WHERE low_cnt >= 2;
