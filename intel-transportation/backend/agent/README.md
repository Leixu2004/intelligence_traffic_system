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
