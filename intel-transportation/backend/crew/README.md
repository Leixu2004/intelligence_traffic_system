# 多 Agent 应急处置系统（CrewAI）

对应 9/17 课件《多 Agent 协作：CrewAI 框架入门与应急处置实战》。指挥官 + 分析师 + 调度员三个
Agent，按 hierarchical 流程协作，产出一份带数据来源标记的应急处置方案。

## 目录结构（课件提交物）

| 课件要求 | 本目录文件 |
| --- | --- |
| `crew_system.py` | `crew_system.py`（组装 Crew、`run_crew()`、独立命令行） |
| `tools/` | `tools/traffic.py`（流量/峰值/预测/法规）、`tools/route.py`（绕行路线）、`tools/notify.py`（公众通告） |
| `tasks/` | `tasks/analysis.py`、`tasks/command.py`、`tasks/dispatch.py` |
| `config.yaml` | `config.yaml`（模型参数、流程模式、角色画像、任务产出、演示路网、通告渠道） |
| `README.md` | 本文件 |
| 演示入口 | `backend/scripts/crew_smoke.py`（Mock／live 两模式，与 `rag_smoke.py` 同风格） |

支撑文件：`config.py`（环境变量装配）、`service.py`（生命周期与降级）、`router.py`（HTTP 入口）、
`agents.py`、`profile.py`、`prompts.py`、`contracts.py`、`routing.py`、`notice.py`、`tests/`。

## 架构

```
事件输入(EmergencyEvent)
   │
   ├─ 交通数据分析师  ── query_checkpoint_flow / query_peak_period / predict_checkpoint_flow / search_traffic_law
   │        └─ Task1 事件分析报告
   ├─ 应急处置指挥官  ── query_checkpoint_flow / query_peak_period / publish_public_notice   allow_delegation=True
   │        └─ Task2 处置指令清单（context=Task1）
   └─ 资源调度员      ── plan_detour_route / publish_public_notice
            └─ Task3 调度方案（context=Task1+Task2）
                    │
        Crew(process=hierarchical, manager_llm=…) ── CrewRunResult(report + tool_evidence)
```

工具名用 ASCII 小写下划线（中文名写在 description 开头）。CrewAI 会把工具名转成 OpenAI function
name，中文标识符会被清空并在 `kickoff()` 阶段抛 `ValueError: OpenAI function name cannot be empty`
——这一点已由 `tests/test_tools.py` 锁住。

工具不重写数据访问：全部包装 `backend/agent/tools.py` 里的 `TrafficToolbox`，因此 CrewAI 与既有
LangChain Agent 共用同一套只读 SQL 与 ONNX 预测入口（见 `dashboard_api.py` 的 `tool_gateway`）。

课件第 7 页画了第 4 个「通告 Agent」，第 9 页只装配 3 个 Agent。这里按 3 个 Agent 实现，通告做成
工具（`PublishPublicNoticeTool`），指挥官与调度员都能调用。

## 运行

### 1. 装依赖

```bash
# 已装进项目现有 .venv（Python 3.13）
pip install crewai PyYAML
```

课件示例代码里的 `from langchain_openai import ChatOpenAI` 在本项目**不适用**：CrewAI 1.15 自带
`crewai.LLM`，且 `crewai` 不依赖 langchain（当前 venv 未装 `langchain-core`）。

### 2. 配密钥（只走环境变量，不写进 config.yaml）

`.env` 里已有 `TRAFFIC_CREW_*` 段。最小改动：

```
DASHSCOPE_API_KEY=sk-xxxx
TRAFFIC_CREW_ENABLED=true
```

`TRAFFIC_CREW_PROVIDER=aliyun-bailian` 时 `base_url` 与模型默认指向百炼兼容模式
（`https://dashscope.aliyuncs.com/compatible-mode/v1` / `deepseek-v4-flash-0731`）。

### 3. 三种跑法

| 入口 | 命令 |
| --- | --- |
| 调试器 | 运行和调试 → `9/17 · CrewAI 应急处置 CLI`（自动带 `.env`、断点在 `run_crew`） |
| 命令行 | `python -m backend.crew.crew_system --process hierarchical --verbose --output data/crew/last_run.json` |
| 演示脚本 | `python backend/scripts/crew_smoke.py`（无密钥时看效果）／`--live`（真实模型） |
| HTTP | `POST /api/v1/crew/emergency/response`（`X-API-Key`），健康探针 `GET /api/v1/crew/health` |

`backend/scripts/crew_smoke.py` 用本机 Mock 端点顶替大模型，CrewAI 的 Agent、Task context、层级
Manager、工具执行与证据链都走真实代码，约 480 行 verbose 日志能完整展示三 Agent 协作过程；
数据工具仍接真实网关，因此会如实报 `source=unavailable`（TimescaleDB 未启动、RAG 未建库）。
Mock 模式输出的正文带「非模型推理」前缀，**不作为验收 #3/#5 的证据**；那两条要 `--live`。

命令行缺省事件即课件第 11 页的「高速 K128 处多车追尾」。换事件用 `--event-json path.json`，
字段见 `contracts.EmergencyEvent`。未启用/缺密钥时接口返回 200 + `ok=false` + `degradation`，
配置齐全但协作中途失败才返回 502。

## 配置项

优先级：环境变量 > `config.yaml`（仅 `llm.*` / `crew.*` 标量）> 内置默认值。

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `TRAFFIC_CREW_ENABLED` | `false` | 总开关 |
| `TRAFFIC_CREW_PROVIDER` | `openai-compatible` | `aliyun-bailian/bailian/dashscope` 时套用百炼默认值 |
| `TRAFFIC_CREW_PROCESS` | `hierarchical` | `hierarchical` / `sequential`（非法值回落 hierarchical） |
| `TRAFFIC_CREW_LLM_MODEL` / `_LLM_BASE_URL` / `_MANAGER_MODEL` | 见 `config.yaml` | 模型与端点；manager 留空则复用主模型 |
| `TRAFFIC_CREW_LLM_API_KEY` | 空 | 回落 `TRAFFIC_AGENT_API_KEY` → `DASHSCOPE_API_KEY`（仅百炼）→ `OPENAI_API_KEY` |
| `TRAFFIC_CREW_TEMPERATURE` / `_TIMEOUT_SECONDS` / `_MAX_RETRIES` / `_MAX_ITER` | `0.3` / `60` / `1` / `6` | 越界会被夹住 |
| `TRAFFIC_CREW_VERBOSE` / `_MEMORY` | `false` | 协作日志 / CrewAI 记忆 |
| `TRAFFIC_CREW_ROUTE_MODE` | `auto` | `auto` 有高德密钥走真实路网、否则演示走廊；`static` 强制离线；`amap` 强制真实 |
| `AMAP_KEY` | 空 | 路线核验；留空则路线 `verified=false` |
| `TRAFFIC_CREW_AUDIT_PATH` / `_NOTIFY_PATH` / `_CONFIG_PATH` | `data/crew/*.jsonl` | 审计、通告落盘、配置文件位置 |

## 课件验收对照

| # | 验收项 | 状态 | 证据 |
| --- | --- | --- | --- |
| 1 | crewai 安装无报错 | 通过 | `crewai 1.15.22`，`import crewai` 正常 |
| 2 | 三个 Agent 角色齐备 | 通过 | `test_three_agents_are_assembled_in_courseware_order` |
| 3 | hierarchical 模式运行 | 编排已验证，推理内容待密钥 | 真实 CrewAI 层级编排离线跑通（见下节 Mock 说明）；`kickoff()` 打到百炼网关，无效密钥返回 401，说明工具 schema、端点、模型路由均被接受 |
| 4 | SQL/预测/路线工具可调用 | 通过 | `tests/test_tools.py`，健康与故障两种网关；路线与通告工具同样写入 `tool_evidence` |
| 5 | 端到端跑通 | 编排已验证，推理内容待密钥 | 降级链路（未启用/缺密钥/协作中途失败）已测；成功路径需 `DASHSCOPE_API_KEY` |
| 6 | verbose 日志输出 | 通过 | `TRAFFIC_CREW_VERBOSE=true` 或 `--verbose` |
| 7 | 工具失败有 fallback | 通过 | 每个工具捕获异常返回 `ok=false` + `source`；数据层不可用时协作继续（`tests/test_service.py`） |

### 无密钥时的离线验证（Mock 模型，不计入 #3/#5）

把 `TRAFFIC_CREW_LLM_BASE_URL` 指向本机一个 OpenAI 兼容 Mock 端点（`/v1/chat/completions`），
Mock 按请求里 `tools` 的顺序逐个返回 `tool_calls`，最后一轮回文本，即可在不消耗密钥的条件下跑
**真实 CrewAI 编排**。已用该方式确认：`crewai.LLM` 带自定义 `base_url` 时不会退回 DeepSeek 官方端点，
模型名原样透传；三个 Agent 收到的工具集与课件分工一致（分析师 4 个、指挥官 3 个、调度员
`plan_detour_route` + `publish_public_notice`），指挥官 `allow_delegation=True` 时 CrewAI 会额外注入
`delegate_work_to_coworker` / `ask_question_to_coworker`；调度员的路线与通告工具真实执行并落盘。
Mock 不产出委派决策，因此这一轮只证明编排与工具闭环，结论正文仍须由真实密钥运行产生。

跑测试与覆盖率：

```bash
python -m pytest backend/crew/tests -q
python -m coverage run -m pytest backend/crew/tests -q && python -m coverage report --include="backend/crew/*"
```

## 证据边界（必须随结论一起交付）

每次返回的 `CrewRunResult` 固定带 `simulation=true` 与 5 条 `limitations`，因为：

1. 无 `AMAP_KEY` 时绕行路线来自 `config.yaml` 的演示走廊，`source=demonstration_topology`、
   `verified=false`，里程与耗时未经真实路网核验。
2. 公众通告只写本地 JSONL，`channel_mode=local_file`、`delivered=false`，**不构成触达证据**；
   课件第 11 页「短信推送 3.2 万用户」是演示数字。
3. 资源数量是建议值，未对接真实应急资源台账。
4. 处置动作不自动下发到现场设备，必须人工审批。
5. 流量预测来自研究候选模型，不代表现场生产精度。

`tool_evidence` 记录每次工具调用的名字、入参摘要、来源与耗时，用于把「模型说了什么」和
「数据里真有什么」分开核对。审计写入 `data/crew/crew_runs.jsonl`（只记事件号、流程、成功与否、
工具调用数、耗时；不含提示词与密钥）。

## 已知限制

- 未装 `psycopg2-binary` / TimescaleDB 未就绪时，流量与预测工具会返回 `ok=false`，协作仍可完成，
  但报告里的数字会标注为缺失。
- 一次真实 `kickoff()` 会触发多次模型调用（层级模式还有 manager 的分配调用），耗时受
  `TRAFFIC_CREW_TIMEOUT_SECONDS`（默认 60s）与 `TRAFFIC_CREW_MAX_RETRIES` 约束。
- 9/18 的 Qwen-VL 多模态、9/21–9/22 的 Flink CEP 尚未接入本目录。
