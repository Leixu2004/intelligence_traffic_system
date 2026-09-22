# 项目2最终提交件（状态报告）

报告日期：2026-09-21（夜间），2026-09-22 上午增补「9/22 补充」条目　验收日期：2026-09-24　覆盖教学日历：9/17 CrewAI、9/18 Qwen-VL、9/21 Flink SQL + CEP、9/22 vLLM 服务化与车路云一体化集成（含《大模型服务化与系统集成》补充材料与三合一交互页）

所有数字均为本机实测或本地仓库内可复核的证据文件，未采用课件 PPT 里任何未复现的性能指标。

## 一、提交件清单

| 交付物 | 位置 | 状态 |
| --- | --- | --- |
| 源码（四个模块） | `backend/flink/`、`backend/crew/`、`backend/vision/`、`backend/integration/` | 齐，测试见第四节 |
| 大屏页面 | `dashboard/pages/3_视频智能解说.py` + `dashboard/vision_client.py`；9/22 补充件 `dashboard/pages/4_车路云诱导与取证.py` + `agent_client.plan_route` / `vision_client.recognize_plate_upload` | 第 3 页齐，降级路径已浏览器验证；第 4 页 9/22 上午已在浏览器过一遍（三页签渲染正常，均为**降级态**：Agent 未启用、路线为演示拓扑、车牌缺依赖），真模型态仍待密钥与依赖 |
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
| 9/22 | 五段集成链路 | PASS（Mock vLLM） | 64 项测试；9/22 下午已用本机真实 vLLM 复跑，见下一行 |
| 9/22 | vLLM 真实推理 | PASS（服务可用）/ NOT_VERIFIED（性能达标） | 本机 RTX 4060 8 GB 起 `vllm/vllm-openai`（权重 Qwen2.5-1.5B-Instruct，经 modelscope 下载），`/v1/models` 200；五段链路 `vllm_analysis=ok 5 285 ms`、`origin=vllm`；`/metrics`：2 次请求全部 `finished_reason=stop`，TTFT 合计 0.6224 s（均值 311 ms）、e2e 合计 6.468 s（均值 3.23 s）、prompt 239 / generation 323 token、抢占 0。样本仅 2 次且非 deploy 制品里的 7 B，故不支撑任何吞吐/时延达标结论 |
| 9/22 补充 | 集成链路窗口段读库侧真实结果表 | PASS | `TIMESCALEDB_DSN` 指向 127.0.0.1:55432 后 `flink_window=ok`、`level=PURPLE`、`source=timescaledb`（`speed_stats` 13 行）；与 `SimulationWindowReader` 复算结果一致。`forecast` 段仍 `prediction_failed:PredictionInputMissing`——`speed_stats` 无 `checkpoint_id` 列，读取器回落成 `camera_id`，预测端按卡口取不到历史 |
| 9/22 补充 | 模型端点服务化（不写死厂商） | PASS | `TRAFFIC_AGENT_PROVIDER/BASE_URL/MODEL` 三变量装配，指到 vLLM 的 `/v1` 即切换；`backend/agent/config.py` + 大屏健康卡片显示生效端点 |
| 9/22 补充 | Agent 工具：路径规划 `plan_route` | PASS（单测＋同进程装配）/ BLOCKED（模型侧注册） | `test_route_tool.py` 5 项 + crew 回归 1 项；注册到模型侧需 `langchain_core`（未装） |
| 9/22 补充 | `POST /api/v1/crew/route/plan` | PASS（真实 HTTP 实测） | 天河→白云机场 200：`demonstration_topology`、11.8 km/19.0 min、`verified=false`；越界 422、非数字 422（Pydantic） |
| 9/22 补充 | 车牌取证 `POST /api/v1/vision/plate` | PASS（契约与降级）/ NOT_VERIFIED（真实识别） | `test_plate_service.py` 6 项含 TestClient 200/503；真实后端返回 503 `ModuleNotFoundError: ultralytics`，`/plate/health` 如实报 `available=false` |
| 9/22 补充 | 大屏三合一页（诱导/规划/取证） | PASS（浏览器实测，降级态） | `dashboard/pages/4_车路云诱导与取证.py`：诱导页签显示 `TRAFFIC_AGENT_ENABLED 未启用` 且禁用发送；规划页签出 11.80 km/19.0 min 并标「演示拓扑，未经实时核验」；取证页签显示 503 原因＋8MB 上限说明。真实模型态待密钥与依赖 |
| 9/23 | 《系统集成测试报告》 | 未开始 | 课件 §五 要求的提交物，需端到端 P95/P99、吞吐、首字时延等实测值 |

## 四、自动化测试（2026-09-22 14:5x 复测，六套后端一次跑完，56.9 s）

| 套件 | 结果 |
| --- | --- |
| `backend/flink/tests` | 52 passed |
| `backend/crew/tests` | 69 passed（含 9/22 新增网关装配回归 1 项） |
| `backend/vision/tests` | 80 passed（含 9/22 新增 `test_plate_service.py` 6 项） |
| `backend/integration/tests` | 64 passed |
| `backend/agent/tests` | 19 passed / 1 failed（含 9/22 新增 `test_route_tool.py` 5 项） |
| `backend/rag/tests` | 16 passed / 2 skipped |
| 合计 | 300 passed / 1 failed / 2 skipped（共 303 条） |

唯一失败项 `test_law_tool.py::test_tool_registered_when_gateway_provided` 是 `.venv` 缺 `langchain_core`（`backend/agent/tools.py:464` 主动抛 `RuntimeError`），2 条跳过是 `test_vector_store.py` 缺 `milvus-lite`，都不是代码回归。

**口径更正**：本节 10:0x 那次记录的是「293 passed / 5 failed / 5 skipped」，同一批 303 条用例，差异来自 `.venv` 的可选依赖——当时 `faiss-cpu` 尚未以 `--no-deps` 补装，依赖补齐后 RAG 的 3 项失败与 3 项跳过全部转为通过，agent 也少一项失败。具体哪一条用例对应哪个缺失包没有留当时的环境快照，不再追述。结论：报测试数字必须同时报解释器与依赖快照。

本轮另修一处测试隔离缺陷：`backend/vision/tests/test_vl_analyzer.py::test_client_error_is_recorded_for_health`
单独跑通过、与 `backend/crew/tests` 同进程跑就失败——crew 用例 import `crew_system` 时 `load_dotenv(.env)`
把密钥泄进进程环境，破坏了「无密钥」这一前提。现在该测试类在 `setUp` 显式剥掉四个密钥变量，
套件顺序不再影响结论。

上一轮还修过一处真实缺陷：`backend/integration/tests/test_flink_source.py::test_size_seconds_controls_window_length` 原先用 `duration_min` 判窗口长度，而该字段是联表带出的 CEP 拥堵段时长（本机数据里有一段 25 分钟），与 `size_seconds` 无关；改为用「进窗事件数峰值 + 最早窗口起点」随窗口长度变化来断言。

### 9/22 上午：两个只有跑起来才会暴露的缺陷

1. **网关引用被复制走了**：`dashboard_api` 原先在 `CrewService.build()` 之后用 `dataclasses.replace`
   造了个带 planner 的新网关给 Agent，但 `TrafficToolbox` 持有的是**构造时**那份旧网关（`route_planner=None`），
   于是 `/api/v1/crew/route/plan` 一上线就 503「路径规划器未接入」——单测全绿，因为它直接构造 toolbox。
   现在 planner 在 `CrewService.build` 内回填进网关，Agent 与 Crew 共用同一份对象，
   并补了 `test_build_attaches_planner_to_the_toolbox_gateway` 锁住这条装配顺序。
2. **Material 图标名不在白名单**：`streamlit.material_icon_names.ALL_MATERIAL_ICONS` 里没有
   `alternate_route`（也没有 `chat_paste`），`page_icon` 直接抛 `StreamlitAPIException` 让整页打不开，
   tab 标签里少写闭合冒号则会把 shortcode 当普通文字显示。已换成 `navigation` / `route` /
   `question_answer` / `license`，四个名字都在白名单内。这类问题只能靠真实起服务＋过一遍 DOM 发现。

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
3. vLLM 已在本机真实推理成功（1.5 B、8 GB 显存、2 次请求），但这只是「服务可用」的证据；吞吐与时延的达标口径仍未验证，且模型规模不是 `deploy/vllm_deploy.yaml` 里的 7 B，两者不可互相代表。
4. 绕行路线来自 `demonstration_topology`，`verified=false`；`AMAP_WEB_SERVICE_KEY` 为空。注意大屏地图用的
   `AMAP_KEY` 是「Web端(JS API)」Key，与 v5 路径规划要的「Web服务」Key 不是一类，配了前者也不会让路线转真。
5. 2026-09-21 23:0x 起百炼账号对所有模型返回 `HTTP 400 {"type":"Arrearage"}`（可用额度 ¥0.00）。此前 22:56 的 CrewAI 报告是真实模型产物，仍然有效；之后的视觉整段视频跑不出真结果。
6. `TRAFFIC_CREW_LLM_MODEL` 的仓库默认值 `deepseek-v4-flash-0731` 从未在本机验证过能否被百炼解析，实测走的是 `qwen-plus`。
7. 车牌取证接口只做到**契约与降级验证**：`.venv` 未装 `ultralytics` / `paddleocr`，`/api/v1/vision/plate`
   实际返回 503，`/plate/health` 返回 `available=false`。`core/pipeline.PlateRecognition` 的识别正确率
   没有人工真值集，任何情况下不得对外声称整牌准确率。
8. 诱导问答（`/api/v1/assistant/query`）依赖 `langchain` / `langgraph` / `langchain-openai`，本机 `.venv`
   未安装，因此 9/22 补充页的第一个页签在本地只会显示不可用；`plan_route` 工具本身已按 5 个单测覆盖，
   但在缺 `langchain_core` 的环境下 `as_langchain_tools()` 注册不到模型侧。

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

# 5) 9/22 补充：路径规划与车牌取证（先起后端，未配 PREDICTION_API_KEY 时免鉴权）
.venv/Scripts/python.exe -m uvicorn backend.dashboard_api:app --app-dir . --host 127.0.0.1 --port 8010
curl -X POST http://127.0.0.1:8010/api/v1/crew/route/plan -H "Content-Type: application/json" \
  -d '{"origin_gps":[113.361,23.129],"destination_gps":[113.307,23.387]}'   # 200，source=demonstration_topology
curl -X POST http://127.0.0.1:8010/api/v1/crew/route/plan -H "Content-Type: application/json" \
  -d '{"origin_gps":[2.5,48.9],"destination_gps":[113.3,23.3]}'              # 422，起点超出中国范围
curl http://127.0.0.1:8010/api/v1/vision/plate/health                          # 200，available 反映真实装载状态
curl -X POST http://127.0.0.1:8010/api/v1/vision/plate -F "file=@data/violations/压线违章_浙B3S65J_20260921_230524.jpg"
  # 未装 ultralytics/paddleocr 时 503 + 原因；装好后返回号牌文本与框

# 6) 大屏第 4 页（后端不在 8000 时用 DASHBOARD_API_URL 指过去）
DASHBOARD_API_URL=http://127.0.0.1:8010 .venv/Scripts/python.exe -m streamlit run \
  ../dashboard/app.py --server.port 8501 --server.headless true
```

## 九、9/24 前还需要用户给输入

1. 阿里云充值（金额由你定），之后我立刻重跑第 3 条命令覆盖降级版证据。
2. 演示录屏：大屏三页 + CrewAI CLI 输出 + Flink UI(8181) 作业页，答辩视频只能你录。第 4 页我已在浏览器
   跑通降级态（第八节第 6 条命令可复现），但录屏里要好看就需要：Agent 启用（充值）＋高德 Web服务 Key＋
   车牌依赖，三者都齐才能录到「真结果」而不是降级提示。
3. 决定是否安装可选依赖（会动到能跑的 venv，装什么由你点头）：
   - `langchain-core` / `langgraph` / `langchain-openai`：LangChain Agent 与 `plan_route` 注册到模型侧，
     也是第 4 页第一个页签能用起来的唯一前提；
   - `psycopg2-binary`：清掉 agent 那 1 项 mock 失败；
   - `pymilvus` / `faiss-cpu`：清掉 rag 那 3 项；
   - `ultralytics` / `paddleocr`：车牌取证真实推理（权重 `models/exp-7.pt` 与 OCR 缓存已在仓库内）。
4. 是否要高德**「Web服务」Key**（填 `AMAP_WEB_SERVICE_KEY`）：有则路线 `verified` 才可能为真。
   大屏地图那个 `AMAP_KEY` 是「Web端(JS API)」Key，两者不通用，只配 JS Key 路线仍会退回演示走廊。
5. 《系统集成测试报告》（9/23 课件 §五 的硬提交物）还没写：它要求端到端 P95/P99、吞吐与首字时延、
   事件检测准确率与误报率、方案可执行率、大屏刷新时延、可用性——每项都要目标值＋实测值＋达标结论。
   本机 vLLM 跑不了真实推理，这类指标只能出「Mock 端点下的链路时延分布」并明确标注不代表生产性能；
   要不要按这个口径出报告，需要你先定。
6. `backend/sql/migrate_existing_pipeline.sql` 仍未执行，需要你对目标库、备份状态和维护窗口明确确认后才动。
