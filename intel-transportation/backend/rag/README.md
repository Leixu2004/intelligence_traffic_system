# 法规 RAG 知识库（backend/rag）

依据根目录 [PROJECT_SPEC.md](../../PROJECT_SPEC.md) 实现的向量检索与交通法规问答子系统。与 `backend/agent/`、`backend/prediction/` 平级，默认关闭（`TRAFFIC_RAG_ENABLED=false`），初始化失败不影响预测主链。

## 模块结构

| 文件 | 职责 |
|------|------|
| `config.py` | `TRAFFIC_RAG_*` 环境配置；LLM 密钥回退链 `TRAFFIC_RAG_LLM_API_KEY → TRAFFIC_AGENT_API_KEY → DASHSCOPE_API_KEY → OPENAI_API_KEY` |
| `contracts.py` | 查询/响应/引用 Pydantic 契约 |
| `document_io.py` | 无依赖文档读取（txt/md/docx；docx 经 zipfile+XML 提取） |
| `chunker.py` | 法条正则切分（第X条，含"之一"）→ 章节感知切分 → 句界窗口（500/50），条号与内容哈希进元数据 |
| `embedding.py` | `bge-local`（sentence-transformers）/ `openai-compatible`（百炼 text-embedding-v4 等）/ `hashing-test`（仅测试，响应强制标注 simulation） |
| `vector_store.py` | `MilvusLiteStore`（首选，HNSW+COSINE M=16/efC=256，单文件持久化）与 `FaissStore`（兜底）；`auto` 按此顺序显式降级并记录原因 |
| `registry.py` | `law_documents.json` 文档版本登记（版本/SHA-256/有效期/状态，支持 superseded/expired 拒答过滤） |
| `build_kb.py` | 幂等摄取：同 `doc_id` 先删旧向量再插入；同哈希跳过 |
| `retriever.py` | 查询向量化 → Top-K 检索 → 仅保留 active 版本 → 分数阈值过滤 |
| `qa.py` | 拒答契约 Prompt（无依据必答"根据现有法规资料，无法回答此问题"）、temperature=0.1、LLM 失败降级为"仅检索原文"并显式标注 |
| `service.py` | 生命周期/健康/查询/重建；`search_only` 为 Agent 只读工具入口 |
| `router.py` | `GET /api/v1/rag/health`、`POST /api/v1/rag/query`、`POST /api/v1/rag/rebuild`（后两者需 X-API-Key） |

## 快速使用

```bash
# 1. 配置（.env）
TRAFFIC_RAG_ENABLED=true
TRAFFIC_RAG_EMBEDDING_PROVIDER=openai-compatible
DASHSCOPE_API_KEY=<百炼密钥>          # 不落代码

# 2. 摄取 data/rag/laws/ 下的法规文档并检索验证
python backend/scripts/rag_smoke.py
```

已内置知识源：`data/rag/laws/交通法规知识库.docx`（超速·第90条 / 酒驾醉驾·第91条 / 闯红灯·第95条处罚标准）。新增法规将 txt/md/docx 放入该目录后调用 `POST /api/v1/rag/rebuild` 或重跑 smoke 脚本即可。

## 状态边界（2026-09-17）

- **已验证（真实链路）**：百炼 `text-embedding-v4` Embedding + Milvus Lite 索引 + `deepseek-v4-flash-0731` 生成的端到端问答；酒驾/超速问题正确引用条号、来源与版本；越界问题（刑法条款、知识库未覆盖问题）正确拒答。
- **已实现未实测**：`bge-local` 生产路径（需下载 BGE 权重）、`hashing-test` 仅限软件模拟验收。
- **待做**：自建测试问题集的 Top-3 命中率 >85% 验收、RAGAS/Faithfulness 评估、法规 PDF 原文接入、BGE 本地维度契约演练。
