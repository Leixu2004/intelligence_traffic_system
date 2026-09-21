# 车路云一体化集成（9/22 课件：模型服务化 vLLM + 集成）

把 9/21 的 Flink 窗口与 CEP 预警、既有的 ONNX/LSTM 预测服务、vLLM 模型服务、
Agent 决策闭环和 Streamlit 大屏串成一条链路（课件第 5 页）：

```
flink_window ──> forecast ──> vllm_analysis ──> agent_plan ──> screen_push
（读窗口+预警） （ONNX 预测）  （vLLM 建议）     （查询→预测→建议） （JSONL + 轮询）
```

## 快速开始

```bash
# 1) 演示与自检（不依赖任何真机，Mock vLLM + 固定窗口夹具，如实标 simulation）
python backend/scripts/integration_smoke.py

# 2) 一键五段 + 生成 Markdown 报告（课件交付物）
python -m backend.integration.system_integration --report backend/integration/integration_report.md

# 3) 测试
pytest backend/integration -q                       # 单元 + 管线 + e2e 共 71 条
python -m backend.integration.e2e_test_suite        # e2e 六条用例 + 结果表
```

HTTP 面：后端启动后自动挂载 `/api/v1/integration/*`（`backend/dashboard_api.py`），
`TRAFFIC_INTEGRATION_ENABLED=true` 之前返回 503；大屏轮询 `GET /api/v1/integration/latest`。

## 环境变量（详见 .env.example 末尾）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `TRAFFIC_INTEGRATION_ENABLED` | false | 总开关，未开启时 /run 返回 503 |
| `TRAFFIC_VLLM_BASE_URL` | http://localhost:8000/v1 | vLLM / OpenAI 兼容端点；compose 起 vLLM 时设 `http://localhost:8001/v1` |
| `TRAFFIC_VLLM_MODEL` | qwen-7b | 与 vLLM `--served-model-name` 一致 |
| `TRAFFIC_VLLM_API_KEY` | （链式回落，最后 EMPTY） | 本地 vLLM 未开鉴权时传 EMPTY 即可 |
| `TRAFFIC_INTEGRATION_FORECAST_STEPS` | 5 | 预测未来 N 步（与模型 bin_seconds 相乘即时间跨度） |
| `TRAFFIC_INTEGRATION_SCREEN_URL` | 空 | 大屏 webhook；不配则只落盘 JSONL，大屏走轮询 |
| `TRAFFIC_INTEGRATION_OUTPUT_DIR` | data/integration | screen_push.jsonl 的目录 |

## 降级矩阵（服务不因缺依赖而 500）

| 缺失依赖 | 表现 | 补救 |
| --- | --- | --- |
| 窗口表空 / 卡口不存在 | 第 1 段 skipped + `pipeline_aborted`，不写大屏 | 起 TimescaleDB + 提交 Flink 作业（9/21） |
| 未配置 TIMESCALEDB_DSN | 窗口段回落 SimulationWindowReader，`query_source=simulation` | 配 DSN 指向 timescaledb |
| 预测服务不可用 | forecast 段 degraded `prediction_unavailable`，提示词明写「预测段本次不可用」 | 起 prediction-api / 放 ONNX 权重 |
| 无卡口历史流量 | `prediction_failed:PredictionInputMissing` | 等窗口数据积累（模型需要 history_steps 个点） |
| vLLM 不可达 | vllm_analysis 段 degraded，建议回落 `RULE_FALLBACK` 模板，origin=rule_fallback | `docker compose --profile vllm up -d vllm` |
| 大屏 webhook 不通 | 落盘照写 + `screen_webhook_unreachable`，大屏改走轮询 | 修 webhook 或直接用 /latest 轮询 |

任何降级都会写进 `degradations` 并把该次运行标 `verified=false`（`simulation=true`），
绝不把 Mock/模板输出伪装成模型结论。

## 部署与监控

- `deploy/vllm_deploy.yaml` —— 独立 vLLM 部署清单（GPU）；仓库根 docker-compose.yml 也有
  `vllm` 服务（`--profile vllm`，宿主端口 8001，避开 prediction-api 的 8000）。
- `deploy/monitor_config.yaml` —— Prometheus 抓取 vLLM `/metrics` + 集成 API 健康面，
  以及 Grafana 面板的指标约定（TTFT/Latency/排队/KV 缓存）。

## 性能数字的边界

课件给出的 1.2s / QPS 62 / 320ms / 680ms 等数字是特定 GPU + 权重下的实测值。
本仓库不复制这些数字：本机无 GPU/权重，未压测。有 GPU 后按
`deploy/monitor_config.yaml` 里的指标名自己压测并在报告中替换，不要引用课件数字冒充实测。

## 大屏推送

- 每次 /run 把一行 JSON 追加到 `screen_push.jsonl`（`push_path`），webhook 失败不丢数据。
- 大屏（Streamlit）轮询 `/api/v1/integration/latest`：进程内返回最近一次运行，
  后端重启后自动回读推送日志最后一条（`source=push_log`，如实标注非实时重放）。
- `GET /api/v1/integration/pushes?limit=20` 可回读最近 N 条。

## 目录

```
backend/integration/
├── config.py            集成层配置（TRAFFIC_* 前缀，默认 enabled=False）
├── contracts.py         pydantic 响应契约（统一信封 {code,message,data,timestamp}）
├── flink_source.py      窗口读取（DatabaseWindowReader / SimulationWindowReader + LATERAL 预警联接）
├── agent_pipeline.py    Agent 三段闭环（查询→预测→建议），RULE_FALLBACK 模板
├── vllm_client.py       OpenAI 兼容客户端（/v1/models 健康探针 + /chat/completions）
├── service.py           五段编排 IntegrationService + 大屏落盘/回读
├── router.py            /api/v1/integration/*（health/run/latest/pushes/vllm/health/report）
├── system_integration.py  一键五段 CLI + Markdown 报告（课件交付物）
├── e2e_test_suite.py    六条 e2e（Mock vLLM 服务器，python -m 直接跑）
├── deploy/              vllm_deploy.yaml + monitor_config.yaml
└── tests/               config / flink_source / pipeline / e2e 共 71 条
```
