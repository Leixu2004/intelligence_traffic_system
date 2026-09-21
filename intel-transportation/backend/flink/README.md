# 9/21 实时计算 · 《Flink SQL 实时作业》+《Flink-CEP与预警规则》

Flink Standalone（单机伪分布式）+ Flink SQL，把既有卡口事件流 `traffic_stream` 做两件事：

1. **5 分钟滑动步长 / 10 分钟窗口**的车速统计，结果 upsert 写入 TimescaleDB `speed_stats`；
2. **复杂事件处理（CEP）**：`MATCH_RECOGNIZE` 识别「持续低速」段，按绿/黄/红/紫四级
   写入 TimescaleDB `traffic_alerts`（课件《Flink-CEP与预警规则》）。

本目录只做实时计算，不改既有落库链路：`traffic_consumer` 仍然负责明细表，
Flink 负责两张结果表，彼此并行、互不覆盖。

## 提交物对照

| 课件要求 | 本目录文件 | 说明 |
| --- | --- | --- |
| `source_ddl.sql` | `sql/source_ddl.sql` | Kafka Source + Watermark（事件时间，乱序容忍 10 秒） |
| `sink_ddl.sql` | `sql/sink_ddl.sql` | JDBC Sink 到 TimescaleDB，含 `PRIMARY KEY ... NOT ENFORCED`；附 print 调试 Sink |
| `speed_stats_job.sql` | `sql/speed_stats_job.sql` | 核心作业：HOP 窗口 + `HAVING COUNT(*) > 5` |
| `flink-conf.yaml` | `conf/flink-conf.yaml` | 4 slot / 并行度 1 / RocksDB / Checkpoint 15s EXACTLY_ONCE / UTC |
| `submit.sh` | `submit.sh` | start / submit / status / cancel / stop / check |
| `README.md` | 本文件 | 部署、验证、字段映射、常见问题 |
| CEP Source | `sql/source_ddl.sql` | 追加 `kafka_traffic_observation_cep`（同 topic、独立 `group.id`） |
| `congestion_cep.sql` | `sql/congestion_cep.sql` | 两个 `MATCH_RECOGNIZE` 视图：红级段与紫级段 |
| `alert_level.sql` | `sql/alert_level.sql` | 四级判定（紫→红→黄→绿）+ 写出 `traffic_alerts` |
| `alert_sink.sql` | `sql/alert_sink.sql` | 预警 JDBC Sink，主键含 `alert_level`；附 print 调试 Sink |
| CEP 配置 | `conf/cep_config.yaml` | 状态 TTL（>最长 WITHIN）+ 并行度 + Watermark 推进 |
| `test_data.json` | `test_data.json` | 能触发四级的模拟车流（88 条，`simulation` 标注在文件内） |

支撑文件（课件未要求，但本项目需要）：`../sql/flink_speed_stats.sql` 与
`../sql/flink_traffic_alerts.sql`（库侧结果表 DDL）、
`Dockerfile` + `../../flink-lib/`（连接器 jar）、`feed_traffic_stream.py` 与
`feed_cep_events.py`（演示数据源）、
`config.py` / `sql_checks.py` / `window_semantics.py` / `cep_reference.py` /
`alert_consumer.py`（一致性校验、窗口与 CEP 语义复算、消费端升降级）、
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
   本机端口默认让开常驻服务：TimescaleDB 走宿主 **55432**、Flink UI 走宿主 **8181**
   （容器内仍是 5432 / 8081）。要改回标准端口设 `TIMESCALEDB_HOST_PORT` / `FLINK_WEB_UI_HOST_PORT`，
   别直接改端口字面量——撞宿主端口时 `docker compose up` 会在重建本栈容器时报
   `Bind for 0.0.0.0:5432 failed: port is already allocated`（实测踩过，而且它已经先把 JM/TSDB 重建掉了）。
2. 结果表存在（JDBC Sink 不建表）：
   - 新数据卷：compose 已把 `backend/sql/flink_speed_stats.sql` 挂进 `initdb.d`，自动执行；
   - 已有数据卷：`docker compose exec -T timescaledb psql -U postgres -d traffic < backend/sql/flink_speed_stats.sql`
     （该语句只建表/加超表，幂等；执行前请自行确认）。
3. 连接器 jar：`flink-lib/` 里必须放 **shaded** 的 `flink-sql-connector-kafka-3.2.0-1.19.jar`
   （非 shaded 的 `flink-connector-kafka-*` 不带 kafka-clients，建表能过、运行时才炸），
   清单见 `flink-lib/README.md`。本机已 `docker compose --profile flink build flink-jobmanager`
   把三个 jar 固化进 `intel-transportation/flink:1.19-connector`，容器重建后无需再手工 `cp`；
   缺 jar 时建表报 `Could not find any factory for identifier 'kafka'`。

## 运行

```bash
# 1) 构建带连接器的镜像并起集群（Web UI: http://localhost:8181；
#    start 里还会顺带 chown checkpoint 卷 + 建 traffic_stream topic）
docker compose --profile flink build flink-jobmanager
export FLINK_WEB_UI_URL=http://localhost:8181
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
.venv/Scripts/python.exe -m unittest discover -s backend/flink/tests -t .
```

`flink_smoke.py` 输出五段：交付物清单、SQL 静态校验（83 项）、窗口语义复算、
CEP 预警复算、集群连通性。第 3、4 段用确定性合成车流（`simulation=true`）经纯 Python
参考实现算出「作业应输出的表」；集群跑通后用 `cep_reference --db <psql 转储>` 逐行比对，
差异即缺陷（本轮结果见「CEP 运行步骤」与两张验收表）。

## CEP 与课件示例的差别（`sql/congestion_cep.sql`）

下表每一行都是本机集群跑出来的，不是推断：前三项都会让作业「提交成功但一条都不输出」。

| 课件示例 | 本项目 | 原因（含集群实测） |
| --- | --- | --- |
| `PATTERN (LOW_SPEED+)` | `PATTERN (SLOW+ END_SLOW)` + `DEFINE END_SLOW AS END_SLOW.speed_kmh >= 20` | 贪婪量词不能放在模式末尾（1.19 直接抛 `TableException`）；改成 `{1,}` + 同条件闭合项后又不回溯，实测「读 176 条、写 0 条」。闭合项必须与 `SLOW` **条件互斥**（速度回升到 ≥ 20），一段拥堵因此在车流恢复时才落一行，语义也更贴近「已确认的拥堵」 |
| `AVERAGE(speed)` | `AVG(SLOW.speed_kmh)` | 引擎里没有 `AVERAGE` 聚合，报 `No match found for function signature AVERAGE(<NUMERIC>)`；静态校验一度断言了错误符号，已改成「有 AVG 且无 AVERAGE」 |
| 直接取 `FIRST(event_time)` | 外面再套一层 `CAST(... AS TIMESTAMP(3))` | MATCH_RECOGNIZE 的 FIRST/LAST 输出带 `*ROWTIME*` 属性，与窗口分支 `UNION ALL` 时报 `Union fields with time attributes requires same types`；`CAST(x AS TIMESTAMP(3) LOCAL)` 在 1.19 是语法错误，只有不带 LOCAL 的写法可行 |
| 未写 `AFTER MATCH`（默认 `SKIP TO NEXT ROW`） | `AFTER MATCH SKIP PAST LAST ROW` | 默认策略下同一段拥堵会被不同起点反复命中，预警表里全是重叠记录 |
| 单一模式 | 红级（`<20`）与紫级（`<10`）各一个视图 | 两者阈值与闭合条件都不同，用 `SUBSET` 合并会让红级分支被紫级的跨度一起拉长 |
| 只讲 CEP | 黄/绿另走 1 分钟 `TUMBLE` 分支，且 `HAVING AVG >= 20` | 「持续」语义归 CEP；窗口分支若也出 <20 的分钟，就会和 RED 争同一主键 |

子句顺序与写法也要记牢：`PATTERN (…) → WITHIN → SUBSET NAME = (A, B) → DEFINE`，
`SUBSET` 用等号不是 `AS`（写反了报 `Encountered "AS"`）。

`WITHIN` 限定的是**一段匹配自身的时间跨度上限**，不是持续时长下限——这是本作业最容易误解的地方。
本项目取 60 分钟，作用是给状态设上限（超过 60 分钟仍未闭合的拥堵段直接作废，级别判定仍看
`RED_MINUTES`/`PURPLE_MINUTES`）。状态 TTL 必须大于它：`conf/*.yaml` 取 7200000 ms（120 分钟），
小于 `WITHIN` 会表现为「低速持续很久却一条紫级都不出」。

## 四级预警口径（`sql/alert_level.sql`）

| 级别 | 条件 | 响应 |
| --- | --- | --- |
| 绿 GREEN | `avg_speed >= 40` | 正常通行 |
| 黄 AMBER | `20 <= avg_speed < 40` | 轻度拥堵，关注提醒 |
| 红 RED | `avg_speed < 20` 且持续 ≥ 10 分钟 | 严重拥堵，启动响应 |
| 紫 PURPLE | `avg_speed < 10` 且持续 ≥ 20 分钟 | 事件升级，应急调度 |

判定顺序由 `CASE WHEN` 自上而下保证（先判最严的紫）。阈值单一定义在 `config.py`
（`RED_SPEED_KMH` 等），SQL 文本、静态校验、本地复算三处同源，改一处不一致就会被测试拦下。

库侧主键取 `(camera_id, start_time, alert_level)`：同一段拥堵可能同时出 RED 与 PURPLE 两行，
两行都要留下，因此级别进主键。

## 消费端升降级（`alert_consumer.py`）

课件第 7 页的「连续 5 分钟 `speed > 30` 自动降一级」**没有**写在 SQL 里：SQL 的 `CASE WHEN`
是无状态的，看不到「当前已推送到几级」，硬编进去只会得到一条永不成立的分支。
该规则由 `alert_consumer.py` 在消费端实现，输入是 `traffic_alerts` 按 `(camera_id, start_time)` 排序的行：

- 更严重 → 推送 `escalate`，并记录 `previous_level` 形成链路；
- 同级且与当前段重叠 → 视为同一段拥堵的延续，**不重复推送**（对应课件「预警去重」）；
- 同级但不重叠 → 新的一段拥堵，推送 `recur`；
- 连续 5 个窗口分钟均速 > 30 → `downgrade` 一级，一次只降一级（紫→红→黄→绿）。

推送动作数在**本地复算**与**集群实际落库**两条路径上均为 6 条（4 escalate / 2 downgrade），
输入分别是 39 条期望预警与 37 条库中行（差的是同一批尾窗，见「已知限制 5」）：

```bash
# 从 psql 导出的真实结果复算（列序见 cep_reference.DB_COLUMNS）
docker compose exec -T timescaledb psql -U postgres -d traffic -A -t -F"|" -c \
  "SELECT camera_id, to_char(start_time AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'), \
          to_char(end_time AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'), \
          alert_level, avg_speed, min_speed, low_cnt, duration_min, source FROM traffic_alerts;" \
  > data/flink/db_alerts.tsv
.venv/Scripts/python.exe -m backend.flink.alert_consumer --db data/flink/db_alerts.tsv
```

这里的口径是**模拟车流**（`simulation=true`）：动作数只说明规则链自洽，不代表真实路况下的推送量。

## CEP 运行步骤

```bash
# 0) 起集群（submit.sh start 已包含两件预备：checkpoint 卷 chown 成 flink、建 traffic_stream topic；
#    手工执行的话就是下面这三条）
bash backend/flink/submit.sh start
docker compose exec -T flink-jobmanager chown -R flink:flink /opt/flink/checkpoints
docker compose exec -T flink-taskmanager chown -R flink:flink /opt/flink/checkpoints
# topic 不会自动建（auto-create 已关）
docker compose exec -T kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 \
  --create --if-not-exists --topic traffic_stream --partitions 4 --replication-factor 1

# 1) 提交作业（SQL_FILES 已含 alert_sink / congestion_cep / alert_level）
#    Git Bash 下要先 cd 到项目根，脚本内部已对 cp/exec 关掉 MSYS 路径转换
export FLINK_WEB_UI_URL=http://localhost:8181
bash backend/flink/submit.sh submit

# 2) 灌模拟车流（必须先提交作业：Source 是 latest-offset，回放前写入的读不到）
.venv/Scripts/python.exe -m backend.flink.feed_cep_events --broker localhost:9092

# 3) 看结果
docker compose exec -T timescaledb psql -U postgres -d traffic -c \
  "SELECT camera_id, start_time, alert_level, avg_speed, duration_min, source
     FROM traffic_alerts ORDER BY start_time DESC, camera_id LIMIT 30;"

# 4) 与本地参考实现逐行对拍（差异即缺陷）
.venv/Scripts/python.exe -m backend.flink.cep_reference --db data/flink/db_alerts.tsv
```

**重跑必须先重提作业**，只 `TRUNCATE` 表是不够的：作业里的水位不会退回，第二次回放的早期事件
会被判为迟到数据整批丢弃（实测截断重放后两张表各只剩 1 行，且那一行的 `low_cnt`/`vehicle_count`
是两次回放叠加后的值）。干净循环是：REST 取消两个作业 → `TRUNCATE traffic_alerts, speed_stats`
→ `submit.sh submit` → 回放 → 等 20 秒读库。

`data/flink/`（已 gitignore）留存了本轮的证据文件：`rendered.sql`、`db_alerts.tsv`、
`expected_alerts.json`、`pushes_from_db.json`、`cluster_evidence.txt`、
`test_data_with_tail.json`（= `test_data.json` + 1 条 14:55 的尾窗验证事件）。

## 资源占用（本机实测）

单机 4 槽位跑这一套足够，`docker stats --no-stream` 的本轮读数：

| 容器 | 内存 | 备注 |
| --- | --- | --- |
| flink-jobmanager | 866 MiB | JM 自身堆，与下面 TM 的配额分开算 |
| flink-taskmanager | 789 MiB | `taskmanager.memory.process.size: 1728m` 是上限，RocksDB 状态在里面 |
| kafka | 995 MiB | 本栈里最重的一个（JVM 默认堆） |
| timescaledb | 148 MiB | 数据目录实测 67.5 MB |

合计约 2.8 GiB（VM 总内存 15.48 GiB）。磁盘侧，本轮从「起集群 → 三次回放 → 19 次 checkpoint」
全过程在 `docker system df` 上的增量是：镜像 0、容器可写层 +53 MB（拷进容器的连接器 jar 与日志）、
卷 +2 MB（`/opt/flink/checkpoints` 实测 2.1 MB）、构建缓存 0——实测本身几乎不占盘，
占盘的是镜像：`docker system df` 报镜像 11.33 GB（可回收 257 MB）、构建缓存 587.6 MB。

两点提醒：

1. 收尾用 `bash backend/flink/submit.sh stop`（等价 `docker compose stop`，保留卷与镜像）；
   连数据一起清是 `docker compose down -v`；`docker system prune` 可回收悬空镜像与构建缓存。
2. 本机已把三个连接器 jar 固化进镜像（`docker compose --profile flink build flink-jobmanager`），
   那次重建在 `docker system df` 上的代价是：镜像 11.33 → 11.35 GB、构建缓存 587.6 → 619.7 MB，
   卷不变。当时 C 盘只剩 7.2 GB（97% 已用），要再重建建议先 `docker system prune`，
   或在 Docker Desktop → Resources 里把 WSL 数据盘挪到 D 盘。

## 已知限制

1. **CEP 只认「低速」，不认事件类型**：本项目 `event_type` 里有 `incident` 等事件，
   课件的紫级语义更接近「事故升级」，此处仍按车速阈值判定，未接入事件类型。
2. **降级在消费端**：SQL 侧不做降级（见上节），因此 `traffic_alerts` 是事实表，
   「当前处于几级」要由消费端状态或查询侧推断。
3. **并行度受 Kafka 分区数限制**：CEP 按 `camera_id` 分区、分区内必须串行，
   `parallelism.default` 超过 `traffic_stream` 分区数只会让多余 subtask 空转。
4. **模拟数据**：`test_data.json` 与 `feed_cep_events.py` 输出均为 `simulation=true`，
   只用于验证规则自洽，不作为真实车速、预警准确率或响应时效的证据。
5. **未闭合的拥堵段不输出**：`PATTERN (SLOW+ END_SLOW)` 要求速度回升才落行，
   因此「到数据结束仍在拥堵」的段在 SQL 与本地复算里都不产出预警（两者口径一致，不是偏差）。
   课件那种「超过 10/20 分钟被切成多段」的现象在本实现中不存在——`WITHIN` 只当状态上限用。
6. **流的最后一个窗口必然挂住**：窗口要等水位越过 `window_end` 才输出，有限数据集的尾窗
   没有后继事件可推水位，实测 `CAM-01 14:49` 那一行在补入一条 14:55 事件后立即出现。
   `table.exec.source.idle-timeout = 3s` 已同时写在 `conf/flink-conf.yaml` 与 `source_ddl.sql`
   的会话 `SET` 里，但本机 1.19 实测**停止灌数后水位仍停在最后一条事件 −10s**（没有推进到 MAX），
   所以「靠 idle-timeout 自动闭尾窗」这条在本集群不成立，验收时按上面的尾窗口径解释差异。
7. **Kafka 分区哈希在本机不分散**：`traffic_stream` 有 4 个分区，而 3 个 `camera_id` 的 key
   全部落到 partition 3（`kafka-get-offsets.sh` 复核：p3=442，其余为 0）。带 key 是并行度 > 1 时的
   正确写法，但本机单并行下它不是 CEP 出数的决定因素，别再拿「不 key 就零输出」当结论。
8. **槽位只有 4 个**：反复提交而不取消旧作业，新作业会以 `NoResourceAvailableException` 失败；
   探针作业尤其要记得收尾。

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
| 窗口不触发 | Web UI 顶点 `currentInputWatermark` 是否越过 `window_end` | 尾窗要等后继事件推水位（`idle-timeout` 在本机实测不生效，见「已知限制 6」）；补一条更晚的事件即可复现闭合 |
| 重放同一批数据后只剩个位数行 | 作业是否重提过（水位与状态跨 `TRUNCATE` 保留） | 取消作业 → 重提 → 再回放 |
| CEP 顶点「读 N 条写 0 条」 | `PATTERN` 末尾是不是贪婪量词、闭合项与 `SLOW` 是否同条件 | 改成互斥闭合条件 `END_SLOW.speed_kmh >= 20` |
| 提交即 `ClassNotFoundException: org.apache.kafka.clients…` | `flink-lib/` 里放的是不是非 shaded 的 `flink-connector-kafka` | 换 `flink-sql-connector-kafka-3.2.0-1.19.jar`（自带 kafka-clients） |
| checkpoint 全失败：`Failed to create directory for shared state` | `/opt/flink/checkpoints` 卷属主 | `chown -R flink:flink`（`docker compose exec` 是 root，服务进程是 flink） |
| `UnknownTopicOrPartitionException: traffic_stream` | topic 是否建过（auto-create 已关） | 用 `kafka-topics.sh --create --if-not-exists` |
| 新作业 `NoResourceAvailableException` | 4 个槽位是否被旧作业占满 | `submit.sh cancel <jobId>` 或 REST `PATCH /jobs/{jid}?mode=cancel` |
| 结果没落库 | 库侧表/主键是否存在、JDBC 驱动是否在 classpath | 见「前置条件 2/3」 |
| 同样的窗口重复出行 | 库侧缺唯一约束，退化成 append | 必须带 `PRIMARY KEY (window_start, camera_id)` |
| 时间整体偏移 8 小时 | Sink 列为 `TIMESTAMPTZ` 而 `table.local-time-zone` 非 UTC | 已在 conf 与 source DDL 里固定 UTC |
| 背压 / Sink 写入慢 | Web UI Backpressure 面板 | 调 `sink.buffer-flush.*`、加并行度（并行度上限=Kafka 分区数）；本轮实测两个作业累计背压 0 ms |

## 验收清单与当前状态

2026-09-21 本机 Docker 集群实测（Flink 1.19.1 单机 JM+TM、4 槽位、并行度 1，输入 89 条模拟事件）。
指标来自 Web UI / REST 与 `psql` 计数，见 `data/flink/cluster_evidence.txt`；
数据本身是 `simulation=true`，所以这些数字证明的是**链路通与语义对**，不是真实路况指标。

| # | 课件验收项 | 本机状态 |
| --- | --- | --- |
| 1 | Flink 集群正常启动，Web UI 8081 可访问 | 已通过：JM + TM 均 RUNNING；本机把 UI 映射到 **8181**（8081 被机器上另一套 flink:1.18.1 占用，别搞混） |
| 2 | Kafka Source 连通、数据可消费 | 已通过：回放 89 条后 Source 顶点 `write-records=177`（下游 3 个分支各一份），Match 顶点 `read-records=89`；`kafka-get-offsets` p3=442 |
| 3 | SQL 语法正确（DDL + DML） | 已通过：静态 83 项 + `sql-client.sh -f` 提交渲染后的 6 份 SQL（11 条语句）全部 `Execute statement succeed`，两个作业进入 RUNNING。集群实跑暴露并修掉 3 处静态校验没抓住的真错：`AVERAGE()`、贪婪量词收尾、FIRST/LAST 的 `*ROWTIME*` 与 UNION 冲突 |
| 4 | 窗口聚合正确（5 分钟滑动） | 已通过：`speed_stats` 13 行、预警的 window 分支 35 行，与本地复算逐行一致（含 `HAVING COUNT(*) > 5` 与 NULL 测速剔除） |
| 5 | Sink 写入成功（TimescaleDB 有数据） | 已通过：`traffic_alerts` 37 行；主键 `(camera_id, start_time, alert_level)` 的 upsert 生效，无重复行 |
| 6 | Watermark 正常推进 | 已通过：两个作业的终点顶点 `currentInputWatermark = 1790002490000` = 14:54:50，即「最后一条事件 14:55:00 − 10s 乱序」；补入 14:55 事件后原先挂住的 14:49 尾窗立即闭合（尾窗机制见「已知限制 6」） |
| 7 | Checkpoint 开启、EXACTLY_ONCE | 已通过：模式 EXACTLY_ONCE，两作业各 10 次 checkpoint **全部 COMPLETED、0 失败**，状态 170,702 B（CEP）/ 25,355 B（窗口），单次端到端 22 ms / 21 ms（含 15s 间隔，数字只反映这一台机器） |
| 8 | Web UI 可监控吞吐/延迟/背压 | 已通过：顶点级 read/write 记录数、水位、checkpoint 历史、背压面板均可读，本轮累计背压 0 ms |

## CEP 验收清单与当前状态（课件《Flink-CEP与预警规则》）

| # | 课件验收项 | 本机状态 |
| --- | --- | --- |
| 1 | CEP 规则定义正确（PATTERN + WITHIN + DEFINE） | 已通过：静态校验 15 项 + 单测锁语义，且集群按此定义真实出数（`PATTERN (SLOW+ END_SLOW)`，`WITHIN 60min` 作状态上限） |
| 2 | 作业提交运行无报错 | 已通过：`traffic-cep-alert`（job `7ba05a22…`）与 `traffic-speed-stats-hop`（`a1576c15…`）RUNNING，`/exceptions` 的 `root-exception` 为 null，checkpoint 10/10 完成 |
| 3 | 拥堵事件被正确识别 | 已通过（集群对拍）：库里 RED = `CAM-01 14:15→14:40 avg 16.4 min 13 cnt 26 dur 25`、PURPLE = `CAM-02 14:00→14:25 avg 7.6 min 5 cnt 26 dur 25`，与 `cep_reference` 逐字段相同 |
| 4 | 四级预警输出正确 | 已通过（集群）：37 行落库 = GREEN 16 / AMBER 19 / RED 1 / PURPLE 1，四级齐全；`--db` 对拍差异仅 1 条尾窗（见「已知限制 6」） |
| 5 | 预警推送及时（无重叠匹配） | 已通过（离线复算 + 库中行两条路径一致）：`AFTER MATCH SKIP PAST LAST ROW` 下无重叠段，37 条 → 6 条推送动作（4 escalate / 2 downgrade）。**端到端时延未测**：事件时间是 2026-09-21 14:xx 的历史时间，回放是一瞬间灌完，任何「毫秒级时延」数字都会是编的 |
| 6 | 降级规则生效 | 已通过（集群行作输入）：`CAM-01 RED→AMBER`、`CAM-03 AMBER→GREEN` 各一次，实现于 `alert_consumer.py`（SQL 无状态，见「消费端升降级」） |
| 7 | 状态 TTL 与并行度配置 | 已配置并实测：`table.exec.state.ttl = 7200000` > `WITHIN 60min`，RocksDB + 增量 checkpoint，并行度 1（4 槽位）；CEP 作业状态 170,702 B |
| 8 | 结果落库 TimescaleDB | 已通过：超表 `traffic_alerts` 37 行、`speed_stats` 13 行，`start_time TIMESTAMPTZ` 无时区偏移 |

第 3–6 项的「已通过」证明的是**SQL 作业与本目录的参考实现在模拟车流上语义一致**，
不构成真实识别率、推送时效或路况结论的证据；`test_data.json` 全是 `simulation=true` 的合成数据。
