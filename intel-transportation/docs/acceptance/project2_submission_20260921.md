# 项目2最终提交件（状态报告）

报告日期：2026-09-21（夜间）　验收日期：2026-09-24　覆盖教学日历：9/17 CrewAI、9/18 Qwen-VL、9/21 Flink SQL + CEP、9/22 vLLM 服务化与车路云一体化集成

所有数字均为本机实测或本地仓库内可复核的证据文件，未采用课件 PPT 里任何未复现的性能指标。

## 一、提交件清单

| 交付物 | 位置 | 状态 |
| --- | --- | --- |
| 源码（四个模块） | `backend/flink/`、`backend/crew/`、`backend/vision/`、`backend/integration/` | 齐，测试见第四节 |
| 大屏页面 | `dashboard/pages/3_视频智能解说.py` + `dashboard/vision_client.py` | 齐，降级路径已浏览器验证 |
| Compose | `docker-compose.yml`（Flink 1.19.1 JM+TM、Kafka、TimescaleDB、`--profile vllm`） | 齐，默认配置可解析并已实跑 |
| Agent 设计文档 | 本文第二节 + `backend/crew/README.md`（架构/配置/证据边界） | 齐 |
| 演示视频 | 未录制 | 素材已就位：`data/vision/traffic.mp4`（4096×2160 / 12fps / 10813 帧 / 15 分钟 / 451 MB），`.env` 的 `VIDEO_SOURCE` 已从不存在的路径改指它。大屏上传上限 200 MB，原片传不进去，已另裁演示用片段 `data/vision/traffic_demo_60s.mp4`（1280×674 / 12fps / 720 帧 / 60 s / 23 MB，抽样亮度 67–74 非黑帧） |
| 集群实测证据 | `data/flink/cluster_evidence.txt`、`db_alerts.tsv`、`expected_alerts.json`、`pushes_from_db.json`、`test_data_with_tail.json` | 齐 |
| Agent 真实运行证据 | `data/crew/crew_runs.jsonl`、`data/crew/notices.jsonl`（通告 `NT-20260921145605466091`） | 齐（22:56 真实模型产物） |
| 视觉链路证据 | `data/vision/live_run_20260921.json` | 当前内容是**欠费降级版**，充值后需重跑覆盖 |

## 二、Agent 设计文档（要点）

三 Agent 层级编排，通告做成工具而不是第 4 个 Agent（课件第 7 页画了 4 个、第 9 页只装配 3 个，按 3 个实现）：

| Agent | 工具 | 任务产物 |
| --- | --- | --- |
| 交通数据分析师 | `query_checkpoint_flow` / `query_peak_period` / `predict_checkpoint_flow` / `search_traffic_law` | Task1 事件分析报告 |
| 应急处置指挥官 | `query_checkpoint_flow` / `query_peak_period` / `publish_public_notice`（`allow_delegation=True`） | Task2 处置指令清单（context=Task1） |
| 资源调度员 | `plan_detour_route` / `publish_public_notice` | Task3 调度方案（context=Task1+Task2） |

编排：`Crew(process=hierarchical, manager_llm=…)`，工具全部包装 `backend/agent/tools.py` 的 `TrafficToolbox`，与既有 LangChain Agent 共用同一套只读 SQL 与 ONNX 入口。工具名必须 ASCII 小写下划线（中文名会被 CrewAI 清空成非法 function name）。

降级设计（答辩时必须一起讲）：缺密钥 / 库无数据 / RAG 未建库 / 无高德密钥时，接口返回 200 + `ok=false` + 可读 `degradation`，路由与预测一律 `verified=false`、`simulation=true`，不产出任何看起来"真"的数字。

真实运行证据（2026-09-21 22:56，`crew_smoke.py --live`，百炼 `qwen-plus`，hierarchical，耗时 154.7 s）：模型自行写出资源数量与到位顺序、绕行路线、公众通告文本，以及 5 条「本方案未验证项」。工具证据 6 条：4 条 `ok=false source=unavailable`（卡口无数据 / RAG 未接入）、`plan_detour_route ok=True verified=false`、`publish_public_notice ok=True`。

## 三、日历日验收矩阵

| 日历日 | 验收项 | 状态 | 依据 |
| --- | --- | --- | --- |
| 9/17 | CrewAI 三 Agent 层级协作 | PASS | live 跑通，报告为模型生成 |
| 9/17 | 工具调用证据链 | PASS | `crew_runs.jsonl` 6 条工具证据 |
| 9/17 | 通告下发 | PASS | `notices.jsonl` `NT-20260921145605466091` |
| 9/18 | Qwen-VL 端点连通 | PASS | 真实视频帧 `qwen-vl-max` HTTP 200 + 合理描述 |
| 9/18 | 整段视频端到端解说 | BLOCKED | 账号欠费，全部调用 400 `Arrearage`（见第七节） |
| 9/18 | 大屏视频解说页 | PASS（降级态） | 浏览器验证过降级提示；真模型态待充值复测 |
| 9/21 | Flink SQL 窗口作业集群实跑 | PASS | 见第五节 |
| 9/21 | CEP 四级预警与参考实现比对 | PASS | 快照 37 行逐字段一致（现库 38 行，见第五节） |
| 9/21 | 预警→推送动作复算 | PASS | 快照 37 行 → 6 条推送动作（`pushes_from_db.json`） |
| 9/22 | 五段集成链路 | PASS（Mock vLLM） | 64 项测试 |
| 9/22 | vLLM 真实推理 | NOT_VERIFIED | 本机无 GPU / 无权重，仅 Mock 端点证明接线 |

## 四、自动化测试（2026-09-21 23:2x 实测）

| 套件 | 结果 |
| --- | --- |
| `backend/flink/tests` | 52 passed |
| `backend/crew/tests` | 68 passed |
| `backend/vision/tests` | 74 passed |
| `backend/integration/tests` | 64 passed |
| `backend/agent/tests` | 13 passed / 2 failed |
| `backend/rag/tests` | 10 passed / 3 failed / 5 skipped |
| 合计 | 281 passed / 5 failed / 5 skipped |

5 项失败全部是共享 `.venv` 缺可选依赖，不是代码回归：`langchain_core`（agent 1 项）、`psycopg2`（agent 1 项，`repository` 的 mock 目标为 None）、`pymilvus` + `faiss-cpu`（rag 3 项，向量后端两条路都不可用）。补齐需要装包，会动到能跑的 venv，答辩前是否安装由用户决定。

本轮另修一处真实缺陷：`backend/integration/tests/test_flink_source.py::test_size_seconds_controls_window_length` 原先用 `duration_min` 判窗口长度，而该字段是联表带出的 CEP 拥堵段时长（本机数据里有一段 25 分钟），与 `size_seconds` 无关；改为用「进窗事件数峰值 + 最早窗口起点」随窗口长度变化来断言。

## 五、Flink 集群实测（9/21）

Flink 1.19.1 standalone（JM + 1 TM，4 slot，并行度 1），RocksDB + 增量 checkpoint，EXACTLY_ONCE，checkpoint 10/10 COMPLETED。

- 89 条模拟事件 → `traffic_alerts`：证据快照 `data/flink/db_alerts.tsv` 为 37 行（GREEN 16 / AMBER 19 / RED 1 / PURPLE 1），与 `cep_reference` 本地复算逐字段一致，`--db` 比对当时仅缺 1 行（流的最后一个窗口，水位线未推进）。**2026-09-21 深夜复核：库里已是 38 行（GREEN 17）**，多出的正是当时挂着的尾窗 `CAM-01 14:55` ——它在后续事件把水位线推过 `window_end` 后闭合，属预期行为，已单独演示并写进 `backend/flink/README.md` 已知限制。
- `speed_stats` 13 行；SQL 静态校验 83 项、失败 0（`flink_smoke.py` 第 2 段实测）。
- 重跑必须先取消作业再清空表：运行中的作业保有水位线，直接重放会被当迟到数据丢弃。

## 六、资源占用与风险（本机实测）

| 项 | 实测 |
| --- | --- |
| C 盘剩余 | 约 7.2 GB（97% 已用）——Docker 镜像共 11.3 GB 在 C 盘虚拟磁盘里，**这是当前最主要的风险** |
| 集群内存 | 整套约 2.8 GiB |
| 集群磁盘增量 | 约 55 MB（checkpoint 卷） |
| 端口 | 本项目 Flink UI **8181**、TimescaleDB **55432**；本机另有别人的 flink(8081)/timescaledb(5432)/milvus，指错会读到别人集群 |

建议答辩期间不要再构建镜像、不要 `docker system prune`（会连带影响另一套栈），演示结束即 `docker compose stop` 释放内存。

## 七、事实边界（不允许对外声称的）

1. 端到端时延未测。课件 PPT 里的 1.2 s / QPS 62 / 320 ms / 680 ms 一律不采用，本机没有对应证据。
2. 视觉识别准确率没有人工真值，`traffic.mp4` 是真实影像但未经标注核对。
3. vLLM 真实推理未跑（无 GPU/权重），只有 Mock 端点证明链路接线。
4. 绕行路线来自 `demonstration_topology`，`verified=false`；`AMAP_KEY` 为空。
5. 2026-09-21 23:0x 起百炼账号对所有模型返回 `HTTP 400 {"type":"Arrearage"}`（可用额度 ¥0.00）。此前 22:56 的 CrewAI 报告是真实模型产物，仍然有效；之后的视觉整段视频跑不出真结果。
6. `TRAFFIC_CREW_LLM_MODEL` 的仓库默认值 `deepseek-v4-flash-0731` 从未在本机验证过能否被百炼解析，实测走的是 `qwen-plus`。

## 八、复现命令

```bash
# 0) 集群预备（建 checkpoint 目录属主 + 预建 Kafka topic），已在 submit.sh start 里
MSYS_NO_PATHCONV=1 docker compose up -d
bash backend/flink/submit.sh start

# 1) 灌 CEP 事件并对账（Windows Git Bash 需要 MSYS_NO_PATHCONV 前缀的容器内路径命令）
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe backend/flink/feed_cep_events.py --file data/flink/test_data_with_tail.json
MSYS_NO_PATHCONV=1 docker compose exec -T timescaledb psql -U postgres -d traffic -A -t -F"|" \
  -c "select camera_id,start_time,end_time,alert_level,avg_speed,min_speed,low_cnt,duration_min,source from traffic_alerts" \
  > data/flink/db_alerts.tsv
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe backend/flink/cep_reference.py --db data/flink/db_alerts.tsv

# 2) CrewAI 真实端到端（需密钥 + 账号有余额）
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe backend/scripts/crew_smoke.py --live --quiet

# 3) Qwen-VL 真实视频（4K 素材，先压帧数）
TRAFFIC_VISION_MAX_FRAMES=12 PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe \
  backend/scripts/vision_smoke.py --live --source data/vision/traffic.mp4 --json data/vision/live_run_20260921.json

# 4) 测试
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest \
  backend/flink/tests backend/crew/tests backend/vision/tests backend/integration/tests -q
```

## 九、9/24 前还需要用户给输入

1. 阿里云充值（金额由你定），之后我立刻重跑第 3 条命令覆盖降级版证据。
2. 演示录屏：大屏三页 + CrewAI CLI 输出 + Flink UI(8181) 作业页，答辩视频只能你录。
3. 决定是否安装 `langchain-core` / `psycopg2` / `pymilvus` / `faiss-cpu` 以清掉那 5 项失败（会动 venv）。
4. 是否要 `AMAP_KEY`（有则绕行路线可转真实路网，`verified` 才可能为真）。
5. `backend/sql/migrate_existing_pipeline.sql` 仍未执行，需要你对目标库、备份状态和维护窗口明确确认后才动。
