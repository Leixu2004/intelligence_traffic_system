-- 9/21 实时计算 · TimescaleDB Sink 表定义
-- 目标表 speed_stats 的库侧 DDL 见 backend/sql/flink_speed_stats.sql（JDBC Sink 不做 DDL，
-- 表与主键必须先存在，否则 upsert 语义无法生效）。
--
-- 口令处理：这里写的是 docker-compose 的默认口令，仅限本机实训环境。
-- 真实环境请改这份文件或用 backend/flink/submit.sh 渲染，绝不把线上口令提交进 git。

CREATE TABLE speed_stats (
    window_start  TIMESTAMP(3),
    camera_id     STRING,
    avg_speed     DOUBLE,
    max_speed     DOUBLE,
    vehicle_count BIGINT,
    PRIMARY KEY (window_start, camera_id) NOT ENFORCED
) WITH (
    'connector' = 'jdbc',
    'url' = 'jdbc:postgresql://timescaledb:5432/traffic',
    'table-name' = 'speed_stats',
    'driver' = 'org.postgresql.Driver',
    'username' = 'postgres',
    'password' = 'postgres',
    'sink.buffer-flush.max-rows' = '200',
    'sink.buffer-flush.interval' = '2s',
    'sink.max-retries' = '3'
);

-- 调试用 Console Sink（不落库，直接在 TaskManager 日志里看窗口结果）：
-- 把作业里的 INSERT INTO speed_stats 换成 INSERT INTO speed_stats_console 即可。
--
-- CREATE TABLE speed_stats_console (
--     window_start  TIMESTAMP(3),
--     camera_id     STRING,
--     avg_speed     DOUBLE,
--     max_speed     DOUBLE,
--     vehicle_count BIGINT
-- ) WITH (
--     'connector' = 'print'
-- );
