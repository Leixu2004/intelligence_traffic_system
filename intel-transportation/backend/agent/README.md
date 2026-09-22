# 交通指挥 Agent

该模块把培训代码中的 Prompt、多模型调用和多轮对话意图接入现有项目。运行时只暴露项目自有的只读交通工具，不执行模型生成的任意 SQL，也不使用 ZIP 中的城市模拟数据。

## 环境变量

```text
TRAFFIC_AGENT_ENABLED=true
TRAFFIC_AGENT_PROVIDER=aliyun-bailian
TRAFFIC_AGENT_MODEL=deepseek-v4-flash-0731
DASHSCOPE_API_KEY=...
TRAFFIC_AGENT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
TRAFFIC_AGENT_TEMPERATURE=0.1
TRAFFIC_AGENT_TIMEOUT_SECONDS=30
TRAFFIC_AGENT_MAX_RETRIES=2
TRAFFIC_AGENT_MAX_ITERATIONS=6
TRAFFIC_AGENT_AUDIT_PATH=data/agent/agent_runs.jsonl
```

支持 OpenAI 以及提供 OpenAI 兼容 Chat Completions 接口的模型服务。百炼模式会读取 `DASHSCOPE_API_KEY`，并在未显式配置时使用中国内地共享兼容端点和 `deepseek-v4-flash-0731`；生产部署应将 base URL 替换为 API Key 所属业务空间的专属地址。未配置密钥或显式关闭时，预测 API 和大屏继续运行，Agent 健康接口会返回不可用原因。

**端点不写死在代码里**：`base_url` 与 `model` 全部来自环境变量，因此把 `TRAFFIC_AGENT_BASE_URL` 指向
本机 vLLM 的 `--api-server` 地址（同样是 `/v1/chat/completions`）就能让问答链路走自托管模型，
无需改一行代码；大屏健康卡片上显示的就是当前生效的 provider 与 model。

## 只读工具

`TrafficToolGateway` 声明工具，`TrafficToolbox` 实现，每次调用写一条 `ToolEvidence`（工具名、入参摘要、
来源、耗时），用于把「模型说了什么」和「数据里真有什么」分开核对。当前：
`query_checkpoint_flow` / `query_peak_period` / `predict_checkpoint_flow` / `search_traffic_law` /
`plan_route`。

`plan_route`（路径规划）包装 `backend/crew/routing.py` 的 `RoutePlanner`，由 `dashboard_api.py` 的
`lifespan` 把 Crew 侧已装配好的 planner 注入 `TrafficToolGateway(route_planner=…)`，因此 Agent 工具与
`POST /api/v1/crew/route/plan` 走同一份坐标校验（中国范围 `73–136 / 3–54`）、同一份高德调用和同一套
证据口径，不存在两套实现漂移。结果保留 `source`（`amap_v5` / `demonstration_topology`）与 `verified`：
无 `AMAP_WEB_SERVICE_KEY` 时返回演示走廊并显式标注「未经实时核验」，提示词第 4 条要求模型把该标注
原样转述，不许把走廊当成实时路况结论。planner 未注入或调用抛错时工具返回 `ok=false` + 原因，
不会把异常冒到模型侧。

## API

```text
GET  /api/v1/agent/health
POST /api/v1/assistant/query
```

问答接口与预测接口共用 `X-API-Key`。请求示例：

```json
{
  "question": "CP-NORTH-01 最近流量如何，并预测下一时间桶？",
  "thread_id": "demo-session-1",
  "checkpoint_id": "CP-NORTH-01"
}
```

审计日志只保存问题、工具参数、工具结果摘要、模型信息和最终回答，不保存私有推理过程或密钥。
