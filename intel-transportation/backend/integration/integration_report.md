# 车路云一体化集成报告

- 生成时间：2026-09-21T09:20:31Z
- 集成开关：`TRAFFIC_INTEGRATION_ENABLED=false`
- vLLM 端点：`http://localhost:8000/v1` / 服务模型名 `qwen-7b`
- 结果是否 verified：`false`（false 表示存在降级，见下）

## 五段链路

| # | 段 | 状态 | 耗时(ms) | 说明 |
| --- | --- | --- | --- | --- |
| 1 | flink_window | ok | 98 | level=PURPLE source=simulation |
| 2 | forecast | degraded | 0 | prediction_failed:PredictionInputMissing |
| 3 | vllm_analysis | degraded | 0 | origin=rule_fallback |
| 4 | agent_plan | degraded | 8835 | query_source=simulation（非库侧真实数据）; prediction_failed:PredictionInputMissing; advise_failed:VllmUnavailableError，回落规则模板; recommendation=rule_fallback（模板文案，非模型生成） |
| 5 | screen_push | ok | 2 | path=D:\CODE\TRAFFIC\intelligent_transportation-main\intelligent_transportation-main\intel-transportation\data\integration\screen_push.jsonl |

## 数据流转

1. Flink 窗口：相机 `CAM-02` / 卡口 `CP-SOUTH-02` @ `2026-09-21T14:20:00`，均速 14.4 km/h，车辆数 9，级别 `PURPLE`（来源 `simulation`）
2. 预测：本次未产出（见下降级明细）
3. 大模型：vLLM 端点 `/chat/completions`，建议来源 `rule_fallback`，模型 ``
4. Agent：query_traffic → predict_flow → generate_plan
5. 大屏：`D:\CODE\TRAFFIC\intelligent_transportation-main\intelligent_transportation-main\intel-transportation\data\integration\screen_push.jsonl`（写入 `true`），轮询 `/api/v1/integration/latest`

### 处置建议

紫级：封闭/分流并行 —— 联动交警现场管控，上游匝道管控，推送绕行方案，通知应急资源到位。

## 降级与边界

- query_source=simulation（非库侧真实数据）
- prediction_failed:PredictionInputMissing
- advise_failed:VllmUnavailableError，回落规则模板
- recommendation=rule_fallback（模板文案，非模型生成）
- vllm_unreachable:ConnectError（http://localhost:8000/v1）
- window_source=simulation（未连库侧 Flink 结果表）

### 补齐方式

- `query_source=simulation（非库侧真实数据）` → 同上：窗口结果来自库侧时 `query.source` 才是 `timescaledb`
- `prediction_failed:PredictionInputMissing` → 起 TimescaleDB 并配 `TIMESCALEDB_DSN`（预测要读卡口历史流量）
- `recommendation=rule_fallback（模板文案，非模型生成）` → vLLM 端点可达后重跑，建议段即由模型生成（当前是规则模板）
- `vllm_unreachable:ConnectError（http://localhost:8000/v1）` → 起 vLLM：`docker compose --profile vllm up -d vllm`，或把 `TRAFFIC_VLLM_BASE_URL` 指向已有的 OpenAI 兼容端点
- `window_source=simulation（未连库侧 Flink 结果表）` → 起 Flink 集群并提交 9/21 两个作业，让 `speed_stats`/`traffic_alerts` 有数据

### 未测项

- vLLM 吞吐/QPS/首 token 延迟：未测（本机无 GPU/权重，未压测）
- 端到端 320ms 推送时延：需要集群 + 真模型 + 大屏三方同时在线，本次未测。
- 车路云协同（RSU/V2X 上行）：本项目无路侧设备，只做到「路侧数据 → 云端 → 应用」这一段。

> 报告中的每个数字都来自本次运行的真实调用或本仓库的本地复算；标 `simulation` 的来源不表示集群已跑通，也不构成准确率或性能结论。
