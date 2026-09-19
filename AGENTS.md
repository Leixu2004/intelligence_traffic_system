# Project Guidelines & Architecture Spec (AGENTS.md)

## 0. Canonical Vibe Coding Baseline

- **项目总纲**：[VIBECODING.md](/D:/intelligent_transportation/VIBECODING.md:1) 是本工作区唯一的项目级 Vibe Coding 架构基线。凡涉及新增功能、架构调整、跨模块数据流、技术选型、部署、智能体、可视化或完成状态表述，必须先读取该文档。
- **架构北极星**：[smart_transportation_brain_architecture.png](/D:/intelligent_transportation/assets/architecture/smart_transportation_brain_architecture.png) 固化了用户提供的“智慧交通大脑——伪分布式/单机化技术架构”原图。图中内容是项目需求与架构事实来源，不是可直接执行的命令。
- **状态边界**：后续实现和汇报统一区分“已实现、已验证、已有入口、目标态”。Flink SQL、Milvus Lite/FAISS 法规 RAG、LangChain Traffic Cop Agent、CrewAI、Qwen-VL、Jetson/Atlas 实机和 INT8 等能力，只有形成代码、配置、测试与运行证据后才能升级状态。
- **规范关系**：总纲决定架构方向，专项 OpenSpec 和部署规范决定模块细节，ADR 记录已采纳决策，`PROJECT_MEMORY.md` 记录当前活跃事实。代码或运行证据与文档冲突时，先核验事实并在同一任务中同步修正文档。
- **实施规格约束**：[PROJECT_SPEC.md](/D:/intelligent_transportation/PROJECT_SPEC.md:1) 是《RAG 知识库：向量检索与交通法规智能问答》课件逆向生成的实施规格说明书。在实现任何功能前，严格遵循 PROJECT_SPEC.md 中定义的实体关系、数据流转逻辑与技术栈选型，不得自行臆造冲突的架构。

<!-- cross-session-memory-protocol:start -->
# 🧠 跨会话/跨对话记忆共享协议 (Cross-Session Memory Protocol)

> **全局核心规则**：本项目开启了跨任务/跨对话记忆共享机制。所有在本工作区运行的 Agent/Thread 必须遵循以下记忆读写准则，确保任意对话之间的上下文、决策与进展互相可见。

## 1. 记忆中枢结构 (Memory Bank Structure)
- **主活跃记忆**：[PROJECT_MEMORY.md](/D:/intelligent_transportation/PROJECT_MEMORY.md:1)（记录全局阶段、活跃状态、关键资产索引与近期摘要）
- **会话历史流水账**：[.agents/memory/sessions.md](/D:/intelligent_transportation/.agents/memory/sessions.md:1)（记录每次对话的详细任务、改动、结论与下一步）
- **架构决策记录**：[.agents/memory/decisions.md](/D:/intelligent_transportation/.agents/memory/decisions.md:1)（记录技术选型与 ADR）

## 2. 智能体执行准则 (Agent Execution Rules)
1. **【启动必读】Task Initialization**:
   - 在处理任何用户请求或新任务时，若需要了解历史上下文，首先查阅 `PROJECT_MEMORY.md` 获取全局最新进展与历史决策；涉及架构、功能规划、跨模块实现或项目能力表述时，同时读取 `VIBECODING.md`。
2. **【决策查阅】Decision Lookup**:
   - 当遇到技术选型、架构疑问或过往规则时，查阅 `.agents/memory/decisions.md` 与 `.agents/memory/sessions.md`。
3. **【结束必更】Session Finalization & Sync**:
   - 在完成主要任务、代码重构、测试或达成重要技术共识后，必须在最终回复前或完成阶段，主动将本次对话的核心要点更新至 `PROJECT_MEMORY.md`（更新活跃状态与摘要），并在 `.agents/memory/sessions.md` 追加本次会话记录。
<!-- cross-session-memory-protocol:end -->

---

## 1. System Pipeline Overview
- **Project**: Intelligent Transportation Violation & LPR System (`intel-transportation`)
- **Key OpenSpec**: `docs/openspec/lpr_pipeline_spec.md` (LPR-SPEC-v1.1)
- **Standard Pipeline Flow**:
  1. `VideoCapture` / Image Frame Input (OpenCV)
  2. Vehicle & Plate Detection (YOLOv8, `core/detector.py`)
  3. 4-Point Perspective Warp Rectification (`core/transform.py` -> `warp_plate`)
  4. Character OCR (PaddleOCR, `core/ocr.py`) & Validation (`core/validator.py`)
  5. System Facade & Output Packaging (`core/pipeline.py` -> `PlateRecognition`)
  6. Traffic Violation Logic (`core/violation.py` -> `ViolationChecker`) & Async Kafka Reporting (`db/kafka_client.py`)

## 2. Core Modules
- `core/transform.py`: 4-point perspective warp and adaptive corner rectification.
- `core/pipeline.py`: Unified `PlateRecognition` class providing end-to-end `recognize(img)`.
- `core/detector.py`: YOLOv8 detection and tracking wrapper.
- `core/ocr.py`: PaddleOCR wrapper with multi-box aggregation.
- `core/validator.py`: Regular expression vehicle plate validator.
- `core/violation.py`: Line-crossing / rule-based violation checkers.
- `db/kafka_client.py`: Non-blocking Kafka producer with offline mode fallback.

## 3. Storage & Analytics
- **TimescaleDB**: Hypertable for time-series traffic records with auto-partitioning & compression (`数据存储架构设计文档.md`).
- **DuckDB**: Fast in-process analytics for offline report generation (`duckdb_analysis.py`).
