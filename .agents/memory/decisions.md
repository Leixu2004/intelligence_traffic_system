# 项目架构与技术决策记录 (Architecture Decision Records - ADR)

本文档沉淀项目核心技术选型、架构设计与关键决策的原因与上下文，供所有会话参考。

---

## ADR-001: 车牌检测定位与字符识别流水线解耦
- **状态**: 已采纳 (Accepted)
- **日期**: 2026-09-03
- **背景**: 交通监控环境下车牌存在大角度倾斜、畸变、模糊以及字符粘连问题。
- **决策**:
  - 采用 **YOLOv8 + 4点单应性几何透视校正 (Perspective Warp) + PaddleOCR** 分层架构。
  - 通过透视变换先拉正矩形再送入 OCR，大幅提高字符首字识别率与汉字准确度。
- **关联规范**: `intel-transportation/docs/openspec/lpr_pipeline_spec.md`

---

## ADR-002: 时序数据存储采用 TimescaleDB + 边缘离线分析采用 DuckDB
- **状态**: 已采纳 (Accepted)
- **日期**: 2026-09-04
- **背景**: 海量交通流数据存在高频写入、大屏连续聚合以及边缘离线极速导出的双重需求。
- **决策**:
  - 服务端/云端采用 TimescaleDB Hypertable（1小时时间切片、7天压缩归档、连续聚合视图）。
  - 边缘端/单机分析采用 DuckDB，利用嵌入式列式存储引擎实现免数据库安装的毫秒级 Excel/CSV 报表导出。
- **关联文档**: `数据存储架构设计文档.md`

---

## ADR-003: 跨会话与跨任务持久化共享记忆体系
- **状态**: 已采纳 (Accepted)
- **日期**: 2026-09-07
- **背景**: 用户在 Codex 中会开启多个对话/任务，各对话之间需要互相感知历史讨论、执行进展和技术决策。
- **决策**:
  - 基于 Codex 每次必读 `AGENTS.md` 的机制，建立 **Memory Bank**。
  - 维护根目录 `PROJECT_MEMORY.md`（高频活跃状态）与 `.agents/memory/sessions.md`（历史流水账）。
  - 在 `AGENTS.md` 中强制约束所有智能体在任务开始时读取全局记忆、在任务结束前同步回写本次会话摘要。
- **关联文件**: `AGENTS.md`, `PROJECT_MEMORY.md`, `.agents/memory/sessions.md`

---

## ADR-004: Streamlit 作为独立只读可视化展示层
- **状态**: 已采纳 (Accepted)
- **日期**: 2026-09-10
- **背景**: 当前项目已有 YOLO/OCR 感知、Kafka 消费、TimescaleDB 时序聚合、DuckDB 离线分析和 FastAPI 大屏接口，但没有轻量交互式展示入口。附件方案要求 Streamlit KPI、趋势图和 WebGIS，并要求在外部服务不可用时仍能展示。
- **决策**:
  - 在根目录新增 `dashboard/`，Streamlit 仅作为只读客户端，独立进程启动，不嵌入 `main.py` 或 Kafka 消费进程。
  - 数据按 FastAPI -> 本地 CSV/Parquet -> 内置示例数据三级降级，缓存查询结果 60 秒并支持自动刷新。
  - WebGIS 优先使用高德地图 JS API 2.0，未配置 Key 时使用 Streamlit 本地地图，确保开发环境零 Key 可启动。
  - 首期聚焦 MVP：四项 KPI、趋势与短时预测、车辆构成、卡口地图、抓拍与取证，不同步引入 Vue/Flink/InfluxDB/微前端等附件中的扩展架构。
- **关联文件**: `dashboard/app.py`, `dashboard/data_loader.py`, `dashboard/map_component.html`, `dashboard/README.md`

## ADR-005: 统一 FastAPI 入口承载大屏数据与 ONNX 预测服务
- **状态**: 已采纳 (Accepted)
- **日期**: 2026-09-10
- **背景**: Streamlit 大屏原先依赖一个未启动且数据库密码为占位值的 `dashboard_api.py`；location1 Demo 已具备可复用的交通流量预测逻辑，但没有远程 API、ONNX 模型或统一启动入口。
- **决策**:
  - 保留 `GET /api/traffic_trend` 作为大屏兼容接口，并按 TimescaleDB -> 本地 CSV 降级，避免数据库未启动时 8000 端口不可用。
  - 将 location1 的 60 秒窗口、移动平均和线性外推逻辑封装为 `TrendForecastModel`，以 opset 14 和动态 batch/time 轴导出 `location1_trend_v1.onnx`。
  - 通过 FastAPI lifespan 预加载并复用 ONNX Runtime Session，提供 `/predict`、`/api/v1/predict/traffic-flow`、`/health`、`/model/info` 和 `/docs`。
  - 预测服务接受单序列与动态 batch，统一返回 `flow/forecast` 兼容字段和 `flows/forecasts` 批量字段；ONNX Runtime 不可用时保留 NumPy 兼容回退。
- **关联文件**: `intel-transportation/backend/dashboard_api.py`, `intel-transportation/backend/prediction/`, `dashboard/data_loader.py`, `dashboard/app.py`, `intel-transportation/backend/Dockerfile`, `intel-transportation/docker-compose.yml`

## ADR-006: 高德地图凭据按使用场景分别编码与读取
- **状态**: 已采纳 (Accepted)
- **日期**: 2026-09-10
- **背景**: 高德 JS API Key 位于 HTML `<script src>` 的 URL 查询参数中，而安全密钥位于脚本加载前的 JavaScript 配置对象中；两者的编码语境不同，统一使用 JSON 字符串序列化会破坏 URL 属性。
- **决策**:
  - 对嵌入 URL 的 Key 使用 `urllib.parse.quote`，保证生成的 `key` 参数不含嵌套引号。
  - 对 `securityJsCode` 使用 JavaScript 字符串 JSON 序列化，并在远程 API 脚本之前设置 `window._AMapSecurityConfig`。
  - 凭据读取优先级为环境变量、Streamlit secrets、`dashboard/.streamlit/secrets.toml` 本地文件兜底，适配不同启动目录；真实凭据不进入源码、测试或日志。
- **验证**: Streamlit 实际渲染高德底图、控件、版权信息和 7 个监测卡口。
- **关联文件**: `dashboard/map_component.py`, `dashboard/map_component.html`, `dashboard/app.py`, `dashboard/.streamlit/secrets.toml`

## ADR-007: 展示层采用无侧边栏首屏的宽屏态势布局
- **状态**: 已采纳 (Accepted)
- **日期**: 2026-09-10
- **背景**: 用户提供的目标效果图强调 1920×1080 暗色大屏中的标题状态栏、KPI 行、图表矩阵和全宽地图；原页面的默认侧边栏和普通 Streamlit 标题会占用首屏空间，视觉层级与目标不一致。
- **决策**:
  - 首屏隐藏默认侧边栏，运行设置、监测卡口和取证记录改为折叠交互区，保证监控内容优先呈现。
  - 使用四项 KPI + 三列图表 + 全宽 WebGIS 的稳定布局，Plotly 图表采用透明背景、离散状态色和无工具栏展示，兼顾大屏观看与鼠标悬停分析。
  - 保留 FastAPI/ONNX 预测、数据降级、自动刷新和高德地图安全配置；教学示例的默认弱口令与硬编码模拟数据不进入当前业务页面。
- **验证**: 1920 宽屏浏览器实际渲染通过，FastAPI 和 Streamlit 健康检查通过。
- **关联文件**: `dashboard/app.py`, `dashboard/README.md`, `dashboard/data_loader.py`, `dashboard/map_component.py`

---

## ADR-008: 感知事件采用统一版本化契约并按业务类型分表
- **状态**: 已采纳 (Accepted)
- **日期**: 2026-09-11
- **背景**: 视觉主程序发送 `traffic_violations`，存储消费者只监听字段不同的 `traffic_stream`，真实识别结果无法进入 TimescaleDB，流量大屏长期依赖模拟器和静态 CSV。
- **决策**:
  - 使用 `TrafficObservation` 与 `ViolationEvent` 两类版本化 Kafka 契约，统一 UTC 时间、卡口、摄像头、车型、置信度和边界框字段。
  - 每个新跟踪车辆发布一次 `traffic_stream`，有效违章同时发布 `traffic_violations`；Kafka 不可用时将原始 Topic 与 Payload 写入项目内 JSONL。
  - 消费者在同一消费组中读取两个 Topic，分别写入 `traffic_gps` 与 `traffic_violations` 超表；分钟连续聚合继续作为预测与大屏的流量来源。
  - FastAPI 提供流量、违章识别记录和按卡口自动构造分钟级特征的预测接口，Streamlit 保持只读职责。
- **关联文件**: `intel-transportation/backend/events.py`, `intel-transportation/main.py`, `intel-transportation/backend/traffic_consumer.py`, `intel-transportation/backend/sql/init_pipeline.sql`, `intel-transportation/backend/dashboard_api.py`, `dashboard/data_loader.py`

---

## ADR-009: 预测模型按数据质量门槛晋级
- **状态**: 已采纳 (Accepted)
- **日期**: 2026-09-11
- **背景**: 流程图要求 PyTorch LSTM 训练，但现有两份轨迹数据按 60 秒聚合后只有 36 个时间点。直接把小样本训练结果替换线上趋势模型会造成不可验证的精度回退。
- **决策**:
  - 实现 LSTM 的时序窗口、标准化、时间顺序验证、梯度裁剪、checkpoint、metadata 与 ONNX 导出完整链路。
  - 训练产物通过 `TRAFFIC_MODEL_PATH` 显式选择，服务依据相邻 JSON metadata 自动应用 scaler 和预测步数限制。
  - `traffic_lstm_demo_v1` 仅作为流程验证资产；现有数据形成 20 个训练窗口且归一化验证 MSE 为 2.669，默认服务继续使用 `location1_trend_v1`。
  - 生产晋级需要更长的连续多时段数据、独立验证集以及与趋势基线一致的误差和稳定性验收。
- **关联文件**: `intel-transportation/backend/prediction/lstm.py`, `intel-transportation/backend/prediction/train_lstm.py`, `intel-transportation/backend/prediction/service.py`, `intel-transportation/backend/prediction/models/traffic_lstm_demo_v1.json`

---

## ADR-010: 中国公开收费站模型采用版本化日历并保持候选状态
- **状态**: 已采纳 (Accepted)
- **日期**: 2026-09-11
- **背景**: 美国教学数据无法代表中国道路时区和节假日规律；可立即获取的 KDD Cup 2017 数据来自中国匿名高速收费站，但天池协议限制其用于非营利学术研究，且不能代表项目现场卡口。
- **决策**:
  - 使用 KDD Cup 2017 逐车过站事件构造 20 分钟流量，按 `tollgate_id + direction` 保持五条独立序列；无事件时间桶按缺失处理并拆分连续片段，不擅自补零，自行聚合结果与第三方聚合镜像逐行核验。
  - 新增 `china_kdd2017` profile，输入包含流量、小时、分钟、星期、法定假日和调休工作日，时间统一为 `Asia/Shanghai`。
  - 中国日历使用本地固定版本和官方来源，不依赖在线接口；超出日历有效期必须报错，不能把未知日期静默视为普通工作日或周末。
  - `traffic_lstm_china_kdd2017_candidate_v1` 仅作为中国公开数据预训练与研究候选，不自动替换 `location1_trend_v1`。商业生产晋级必须补充有明确授权的现场卡口数据、上线年份日历和滚动回测。
- **验证**: 独立测试 R² 0.8644，高于 last-value 基线 0.8271；核心、prediction、大屏测试及 ONNX/FastAPI 验证通过。
- **关联文件**: `intel-transportation/backend/prediction/prepare_kdd2017.py`, `intel-transportation/backend/prediction/holiday_calendar.py`, `intel-transportation/backend/prediction/data.py`, `intel-transportation/backend/prediction/train_lstm.py`, `intel-transportation/backend/prediction/service.py`, `intel-transportation/backend/dashboard_api.py`, `dashboard/data_loader.py`

---

## ADR-011: 本地联调复用现有 Kafka/TimescaleDB 并采用幂等原位迁移
- **状态**: 已采纳 (Accepted)
- **日期**: 2026-09-11
- **背景**: Docker Hub 当前不可达，且本机已有健康的 Kafka、ZooKeeper 和 TimescaleDB 容器占用 9092/5432。现有 TimescaleDB 保留 25 条交通记录，但表结构早于统一事件契约。
- **决策**:
  - 按用户明确确认复用现有容器，不另起隔离基础设施，也不删除原有记录。
  - 通过幂等 SQL 原位补齐 `traffic_gps` 字段和 `(time, vehicle_id)` 唯一索引，新建 `traffic_violations` hypertable；迁移必须可重复执行。
  - 消费者支持配置 `KAFKA_AUTO_OFFSET_RESET`。首次接入已有主题时使用新消费组和 `latest`，避免无意重放未知历史消息；正式灾难恢复仍可显式使用 `earliest`。
  - FastAPI 连接现有 TimescaleDB，大屏继续只读 API；中国 LSTM 研究候选可在数据库没有对应演示卡口时读取明确标识的本地 KDD 演示序列。
- **验证**: 两类 Kafka 事件均成功入库，稳定消费组 lag 为 0；连续聚合刷新成功；FastAPI 的流量与识别来源均为 TimescaleDB，LSTM 卡口预测和 Streamlit 页面返回 200。
- **关联文件**: `intel-transportation/backend/sql/migrate_existing_pipeline.sql`, `intel-transportation/backend/traffic_consumer.py`, `intel-transportation/backend/dashboard_api.py`, `dashboard/data_loader.py`

---

## ADR-012: 边缘部署采用通用 ONNX 与设备派生制品分层验收
- **状态**: 已采纳 (Accepted)
- **日期**: 2026-09-16
- **背景**: 边缘部署课件同时包含 ONNX、FP16/INT8、Jetson TensorRT、Atlas CANN 和示例性能数据。项目已有 FP32 ONNX 与导出入口，但尚无 TensorRT engine、Atlas OM、量化产物和目标设备基准。若把教学示例或脚本参数直接描述成已完成能力，会使后续开发和答辩口径偏离实际状态。
- **决策**:
  - ONNX 作为跨运行时的通用模型制品；TensorRT `.engine` 和 Atlas `.om` 作为绑定硬件、驱动和运行时版本的派生制品。
  - 部署状态统一分为“已实现、已验证、已有入口、未实现”，导出脚本存在不等于目标设备部署完成。
  - FP32 是基线，Jetson 优先验证 FP16；INT8 必须使用代表性校准数据，并通过检测、OCR 与完整 LPR 链路的精度回归后才可启用。
  - 性能报告必须区分模型级与端到端延迟，记录 avg/p50/p95/FPS、Execution Provider、硬件、软件版本、内存、温度和功耗。
  - 课件中的体积、速度、mAP 与 `FPS >= 15` 仅作为教学参考，不作为项目已达成结果或生产 SLA。
- **关联文件**: `intel-transportation/docs/deployment/edge_inference.md`, `intel-transportation/edge/README.md`, `intel-transportation/edge/export_models.py`

---

## ADR-013: 以单机伪分布式三层架构作为项目 Vibe Coding 总纲
- **状态**: 已采纳 (Accepted)
- **日期**: 2026-09-16
- **背景**: 用户提供“智慧交通大脑——伪分布式/单机化技术架构”总图，要求后续 Vibe Coding 不偏离统一方向。现有项目已经形成感知、Kafka、TimescaleDB、预测服务和大屏主链，但 Flink、法规 RAG、多智能体与目标设备部署仍处于不同完成阶段，需要同时保存目标架构与真实状态边界。
- **决策**:
  - 根目录 `VIBECODING.md` 作为唯一项目级架构总纲，`AGENTS.md` 将其设为架构、功能规划、部署和能力表述任务的必读文件。
  - 总体架构固定为应用层、数据与计算层、感知与部署层；以 Docker Compose 单机伪分布式、Python/SQL 主栈、版本化事件和统一 API/模型契约作为演进基础。
  - 当前主链保持 `OpenCV/YOLO/PaddleOCR -> Kafka -> Python consumer -> TimescaleDB -> FastAPI/ONNX -> Streamlit`；Flink SQL、Milvus Lite/FAISS、LangChain Agent、CrewAI、Qwen-VL、Jetson/Atlas 与 INT8 只有形成代码、配置、测试和运行证据后才能升级状态。
  - 智能体工具默认只读并保留引用与审计；真实交通控制、外部通知和其他高风险动作必须经过权限校验与人工审批。
  - 实施顺序遵循 P0 数据与部署基线、P1 Flink SQL、P2 多源感知与长时预测、P3 Traffic Cop RAG 与应急研判、P4 可视化和交付验收。
- **关联文件**: `VIBECODING.md`, `AGENTS.md`, `assets/architecture/smart_transportation_brain_architecture.png`

## ADR-014: 车牌识别事件独立建模并分层管理验收证据
- **状态**: 已采纳 (Accepted)
- **日期**: 2026-09-16
- **背景**: 项目1验收要求证明车辆检测、车牌识别、存储、预测和大屏的完整链路。原实现主要在违章条件下执行 LPR，常规车牌识别缺少独立事件；硬件和人工真值缺失时，软件模拟结果容易被误写为真实精度或性能通过。
- **决策**:
  - `TrafficObservation`、`PlateRecognitionEvent` 与 `ViolationEvent` 作为三类独立版本化事件；常规车牌识别写入 `plate_recognitions`，只有真实违章写入 `traffic_violations` 并保存证据图片。
  - 感知进程支持 `RECOGNITION_MODE=all|violation_only`，默认 `violation_only`；验收所有车辆识别事件时显式使用 `all`。
  - 软件模拟事件必须包含 `simulation=true` 和 `simulation_scope=software_contract_and_wiring_only`，模拟链只证明 Kafka、consumer、TimescaleDB、API 和模型服务契约，不证明 YOLO/OCR 精度、真实端到端延迟或硬件性能。
  - 验收状态统一采用 `PASS`、`FAIL`、`BLOCKED` 和 `NOT_VERIFIED`；源码存在、模型可加载、模拟事件或随机张量基准均不能自动升级真实能力状态。
  - 生产 API 使用显式 CORS 来源、非空 API key 和默认隐藏内部异常详情；Dashboard 通过环境变量传递同一 API key。
- **验证**: 三类事件路由、识别模式、API 安全和 Dashboard 密钥传递均有单元测试；Compose acceptance profile 静态解析通过，实际数据库运行验收待用户确认结构操作后执行。
- **关联文件**: `intel-transportation/backend/events.py`, `intel-transportation/main.py`, `intel-transportation/backend/traffic_consumer.py`, `intel-transportation/backend/sql/init_pipeline.sql`, `intel-transportation/backend/dashboard_api.py`, `dashboard/data_loader.py`, `intel-transportation/acceptance/simulate_pipeline_events.py`

---

## ADR-015: 以六段式演示闭环作为项目最终答辩验收路径
- **状态**: 已采纳 (Accepted)
- **日期**: 2026-09-16
- **背景**: 用户提供的项目答辩与功能模块设计图要求展示“Docker 一键启动 -> 实时感知 -> AI 预测 -> Agent 决策”完整闭环，并指定 Docker 环境、违章抓拍、Agent 交互、RAG 问答、实时预警和多 Agent 应急六项必演内容。该材料定义目标场景和评分关注点，但不能替代仓库实现与运行证据。
- **决策**:
  - 六项演示统一纳入 `VIBECODING.md` 的最终交付基线，不另建第二套架构；功能实现仍沿用三层架构、Kafka 事件契约、TimescaleDB、FastAPI/ONNX 和 Streamlit/WebGIS 主链。
  - Docker 演示同时验证编排状态、业务健康和持久化；Milvus Lite 按嵌入式 RAG 服务加载索引、持久化目录和健康检索验收，不要求或虚构独立 Milvus 集群。
  - 违章抓拍保留真值、事件 ID、Kafka 消息、数据库记录和分阶段/端到端延迟；Agent 与 RAG 保留工具参数、返回值、模型版本、法规版本、引用和拒答记录。
  - Flink/CEP 实时预警的 `<5s` 从事件进入项目 Kafka 或约定系统入口开始，到告警持久化并可由 API 查询结束；计时起止点、时钟与统计方法必须在验收前固定。
  - CrewAI/Qwen-VL 输出属于候选研判和处置建议，必须传递结构化证据并保留人工审批；信号控制、分流、限行或外部通知不得自动执行。
  - 除截图明确的 `<5s` 外，不从图片自行推导准确率或启动时限阈值；其他门槛由专项验收规范定义。模拟事件和页面原型只证明契约或交互，不证明真实精度与生产性能。
- **关联文件**: `VIBECODING.md`, `assets/architecture/project_demo_defense_requirements.png`, `assets/architecture/project_function_module_design.png`
