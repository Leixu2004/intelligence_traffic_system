-- 9/21 复杂事件处理 · 预警 Sink 表定义（Flink 侧）
-- 库侧 DDL 见 backend/sql/flink_traffic_alerts.sql；执行顺序：
--   source_ddl.sql → alert_sink.sql → congestion_cep.sql → alert_level.sql
--
-- 口令处理与 sink_ddl.sql 一致：这里只写 docker-compose 的本机默认值，
-- 真实环境由 backend/flink/submit.sh 渲染临时副本，绝不把线上口令提交进 git。

CREATE TABLE traffic_alerts (
    camera_id    STRING,
    start_time   TIMESTAMP(3),
    end_time     TIMESTAMP(3),
    alert_level  STRING,
    avg_speed    DOUBLE,
    min_speed    DOUBLE,
    low_cnt      BIGINT,
    duration_min DOUBLE,
    source       STRING,
    PRIMARY KEY (camera_id, start_time, alert_level) NOT ENFORCED
) WITH (
    'connector' = 'jdbc',
    'url' = 'jdbc:postgresql://timescaledb:5432/traffic',
    'table-name' = 'traffic_alerts',
    'driver' = 'org.postgresql.Driver',
    'username' = 'postgres',
    'password' = 'postgres',
    'sink.buffer-flush.max-rows' = '100',
    'sink.buffer-flush.interval' = '2s',
    'sink.max-retries' = '3'
);

-- 调试用 Console Sink（不落库，直接在 TaskManager 日志里看预警）：
--
-- CREATE TABLE traffic_alerts_console (
--     camera_id    STRING,
--     start_time   TIMESTAMP(3),
--     end_time     TIMESTAMP(3),
--     alert_level  STRING,
--     avg_speed    DOUBLE,
--     min_speed    DOUBLE,
--     low_cnt      BIGINT,
--     duration_min DOUBLE,
--     source       STRING
-- ) WITH (
--     'connector' = 'print'
-- );
