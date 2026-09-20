# 9/21 实时计算 · 《Flink SQL 实时作业》

Flink Standalone（单机伪分布式）+ Flink SQL，把既有卡口事件流 `traffic_stream` 做
**5 分钟滑动步长 / 10 分钟窗口**的车速统计，结果 upsert 写入 TimescaleDB `speed_stats`。

本目录只做实时聚合，不改既有落库链路：`traffic_consumer` 仍然负责明细表，
Flink 负责窗口结果表，两者并行、互不覆盖。

## 提交物对照

| 课件要求 | 本目录文件 | 说明 |
| --- | --- | --- |
| `source_ddl.sql` | `sql/source_ddl.sql` | Kafka Source + Watermark（事件时间，乱序容忍 10 秒） |
| `sink_ddl.sql` | `sql/sink_ddl.sql` | JDBC Sink 到 TimescaleDB，含 `PRIMARY KEY ... NOT ENFORCED`；附 print 调试 Sink |
| `speed_stats_job.sql` | `sql/speed_stats_job.sql` | 核心作业：HOP 窗口 + `HAVING COUNT(*) > 5` |
| `flink-conf.yaml` | `conf/flink-conf.yaml` | 4 slot / 并行度 1 / RocksDB / Checkpoint 15s EXACTLY_ONCE / UTC |
| `submit.sh` | `submit.sh` | start / submit / status / cancel / stop / check |
| `README.md` | 本文件 | 部署、验证、字段映射、常见问题 |

支撑文件（课件未要求，但本项目需要）：`../sql/flink_speed_stats.sql`（库侧结果表 DDL）、
`Dockerfile` + `../../flink-lib/`（连接器 jar）、`feed_traffic_stream.py`（演示数据源）、
`config.py` / `sql_checks.py` / `window_semantics.py`（一致性校验与窗口语义复算）、
`tests/`、`../scripts/flink_smoke.py`（演示与自检）。

## 与课件示例的三处有意偏差

| 课件 | 本项目 | 原因 |
| --- | --- | --- |
| topic `camera-data`，字段 `camera_id/plate_number/speed/lane` | topic `traffic_stream`，字段 `camera_id/checkpoint_id/speed_kmh/vehicle_type/...` | 复用项目既有事件契约（`backend/events.py`），不再造一份平行数据源 |
| `GROUP BY HOP(...)` + `HOP_START(...)` | `FROM TABLE(HOP(...))` 窗口 TVF | 旧式 Group Window 已标记废弃；两者结果一致 |
| `WHERE event_time > NOW() - INTERVAL '1' HOUR` | 只保留 `WHERE speed_kmh IS NOT NULL` | `NOW()` 是处理时间，回放/补数时事件时间早于当前时刻，会把整条流滤空 |

字段映射（课件 → 本项目）：`speed → speed_kmh`、`camera_id → camera_id`、
`plate_number → vehicle_id`（本项目车牌在 `plate_recognitions` 流里，不参与车速窗口）、
`lane` 本项目事件中不存在，故不建列。

## 前置条件

1. Docker Desktop 已启动，`docker compose ps` 里 `timescaledb`、`kafka` 健康。
2. 结果表存在（JDBC Sink 不建表）：
   - 新数据卷：compose 已把 `backend/sql/flink_speed_stats.sql` 挂进 `initdb.d`，自动执行；
   - 已有数据卷：`docker compose exec -T timescaledb psql -U postgres -d traffic < backend/sql/flink_speed_stats.sql`
     （该语句只建表/加超表，幂等；执行前请自行确认）。
3. 连接器 jar 放入 `flink-lib/`（清单见 `flink-lib/README.md`），否则建表会报
   `Could not find any factory for identifier 'kafka'`。

## 运行

```bash
# 1) 构建带连接器的镜像并起集群（Web UI: http://localhost:8081）
docker compose --profile flink build flink-jobmanager
bash backend/flink/submit.sh start

# 2) 灌演示车流（车速为模拟值）
docker compose --profile flink up -d flink-traffic-feed
#    或宿主机：.venv/Scripts/python.exe -m backend.flink.feed_traffic_stream --duration 1800

# 3) 提交 SQL（依次执行 source/sink/job 三份 DDL/DML）
bash backend/flink/submit.sh submit

# 4) 监控与验证
bash backend/flink/submit.sh status
docker compose exec -T timescaledb psql -U postgres -d traffic -c \
  "SELECT window_start, camera_id, avg_speed, max_speed, vehicle_count FROM speed_stats ORDER BY window_start DESC, camera_id LIMIT 20;"

# 5) 停止
bash backend/flink/submit.sh cancel <jobId>
bash backend/flink/submit.sh stop
```

裸机（非容器）部署：`cp backend/flink/conf/flink-conf.yaml $FLINK_HOME/conf/`，
`cp flink-lib/*.jar $FLINK_HOME/lib/`，`./bin/start-cluster.sh`，
然后 `FLINK_HOME=/opt/flink bash backend/flink/submit.sh submit`。
把 `source_ddl.sql` / `sink_ddl.sql` 里的 `kafka:29092`、`timescaledb:5432` 换成 `localhost:9092`、`localhost:5432`。

## 不启动集群时的自检

```bash
.venv/Scripts/python.exe backend/scripts/flink_smoke.py          # 或 bash backend/flink/submit.sh check
.venv/Scripts/python.exe -m unittest backend.flink.tests.test_sql_artifacts backend.flink.tests.test_window_semantics
```

`flink_smoke.py` 输出四段：交付物清单、SQL 静态校验（33 项）、窗口语义复算、集群连通性。
第 3 段用确定性合成车流（`simulation=true`）经纯 Python 参考实现算出「作业应输出的表」，
供集群跑通后逐行比对。

## 窗口语义（复算依据）

- 窗口左闭右开 `[start, start+10min)`，起点与 Unix 纪元起对齐（Flink HOP 默认 offset），
  因此 10 分钟窗口 / 5 分钟步长下**每条数据恒归属 2 个窗口**，每 5 分钟刷新一次最近 10 分钟车速。
- `HAVING COUNT(*) > 5`：过车太少的 (窗口, 相机) 分组不输出，避免 1~2 条样本算出的均值误导。
- `speed_kmh IS NULL` 的记录先剔除再聚合（既有事件契约允许测速字段缺失）。
- `ROUND(AVG(speed_kmh), 1)` 四舍五入取 1 位小数，本地复算用 `Decimal` 半进位保持一致。

## 常见问题对照（课件第 10 页）

| 现象 | 先查 | 处理 |
| --- | --- | --- |
| Watermark 不推进 | Kafka 有没有新数据、事件 `time` 是否是 ISO-8601 | 起 `flink-traffic-feed`；生产者统一用 `datetime.now(timezone.utc).isoformat()` |
| 窗口不触发 | Watermark 未越过 `window_end`；`table.exec.source.idle-timeout` | 停止灌数据后再等 10 秒（乱序容忍）+ 尾窗触发；已配 idle-timeout 3s |
| 结果没落库 | 库侧表/主键是否存在、JDBC 驱动是否在 classpath | 见「前置条件 2/3」 |
| 同样的窗口重复出行 | 库侧缺唯一约束，退化成 append | 必须带 `PRIMARY KEY (window_start, camera_id)` |
| 时间整体偏移 8 小时 | Sink 列为 `TIMESTAMPTZ` 而 `table.local-time-zone` 非 UTC | 已在 conf 与 source DDL 里固定 UTC |
| 背压 / Sink 写入慢 | Web UI Backpressure 面板 | 调 `sink.buffer-flush.*`、加并行度（并行度上限=Kafka 分区数） |

## 验收清单与当前状态

| # | 课件验收项 | 本机状态 |
| --- | --- | --- |
| 1 | Flink 集群正常启动，Web UI 8081 可访问 | 待办：需要 Docker Desktop（compose 服务已就位） |
| 2 | Kafka Source 连通、数据可消费 | 待办：随集群启动验证；`feed_traffic_stream.py --dry-run` 已验证事件契约 |
| 3 | SQL 语法正确（DDL + DML） | 静态一致性 33 项通过；`sql-client` 语法校验待集群 |
| 4 | 窗口聚合正确（5 分钟滑动） | 已通过：`window_semantics` 复算 + 12 项单测 |
| 5 | Sink 写入成功（TimescaleDB 有数据） | 待办：随集群验证 |
| 6 | Watermark 正常推进 | 待办：随集群验证（`EXPLAIN`/Web UI 观察） |
| 7 | Checkpoint 开启、EXACTLY_ONCE | 已配置：`conf/flink-conf.yaml` |
| 8 | Web UI 可监控吞吐/延迟/背压 | 待办：随集群验证 |

「待办」项在集群起来后按上面「运行」一节逐条执行即可，本目录不含任何模拟的集群指标。
