# 会话与任务历史流水账 (Session History Ledger)

本文档记录在 `D:\intelligent_transportation` 工作区中进行的所有智能体会话、用户交互及任务执行记录。新会话需在结束前追加记录。

---

### [Session #20260911-001] 交通流量可视化大屏独立 Demo 构建与高德 WebGIS 接入
- **时间**: 2026-09-11 00:45 (Asia/Shanghai)
- **目标**: 依据《交通流量可视化大屏开发实战讲解_监测大屏Demo.docx》和《程序代码.docx》，在不侵入主项目的前提下独立完成 Streamlit 交通流量大屏 Demo，并接入真实高德地图 WebGIS Key 与安全密钥。
- **核心操作与决策**:
  1. 架构隔离决策：创建独立工程目录 traffic_dashboard/，保持与现有主项目 intel-transportation 完全解耦，后续可按需进行组件合并。
  2. 鉴权与配置实现：采用 streamlit-authenticator 配合 Bcrypt 安全加密哈希重构 config.yaml 与 app.py，避免明文凭证风险。
  3. 高德 WebGIS v2.0 正确集成：将用户提供的 Key 与安全密钥通过本地敏感配置接入，记录中不保留具体值；严格按照先注入 window._AMapSecurityConfig 后加载 API 脚本的规范接入，并叠加深色底图 (amap://styles/dark)、实时路况图层 (AMap.TileLayer.Traffic) 及交互式标记点。
  4. 完整大屏页面交付：在 pages/1_🚦交通流量大屏.py 中实现了 4 项核心 KPI、24小时双峰车流面积图、车型分布环形图、实时交通异常告警列表以及一键联动广播模拟。
  5. 验证与服务拉起：在 .venv-dashboard 中补齐相关依赖，测试编译全部通过，以后台静默方式启动 Streamlit 服务并通过 http://localhost:8501/_stcore/health 健康检查（状态码 200）。
- **关键输出/产物**:
  - traffic_dashboard/app.py：主入口与鉴权路由
  - traffic_dashboard/pages/1_🚦交通流量大屏.py：大屏可视化与 WebGIS 页面
  - traffic_dashboard/config.yaml：加盐加密账号配置
  - traffic_dashboard/requirements.txt：独立运行依赖清单
- **遗留事项/下一步建议**:
  - 本地服务当前在 http://localhost:8501 稳定运行，默认登录凭证为 admin / admin。后期若需合并入主项目，可将地图组件与时序图抽象为可复用组件对接主系统的 FastAPI 数据流。


## 📝 会话记录模板 (Session Template)

```markdown
## [Session ID: #YYYYMMDD-HHMM] 主题 / 任务简述
- **时间**: YYYY-MM-DD HH:MM (Asia/Shanghai)
- **目标**: 简述用户提出的主要诉求或任务目标
- **核心操作与决策**:
  - 执行了哪些文件修改 / 测试 / 分析
  - 达成了什么技术决策或修复了什么问题
- **关键输出/产物**: 相关文件路径、生成的文档或报表
- **遗留事项/下一步建议**: 待后续会话跟进的事项
```

---

## 📜 历史会话记录 (Session Logs)

### [Session #20260911-1055] 修复交通流量预测脚本 GUI 不弹出问题
- **时间**: 2026-09-11 10:55 (Asia/Shanghai)
- **目标**: 排查并修复在桌面专项工程中执行 `python traffic_prediction.py` 后没有弹出 Tkinter 图形界面的问题。
- **核心操作与决策**:
  1. 复现确认脚本以退出码 0 立即结束；根因是无参数分支只有“略去 GUI 部分代码”的注释和 `pass`，并非 Tkinter、Matplotlib 后端或依赖故障。
  2. 在 `traffic_prediction.py` 中补齐登录页、轨迹文件选择、时间窗/预测步数/平滑窗口设置、异步数据读取、Matplotlib 图表嵌入、PNG 与 Excel 导出和错误提示。
  3. 将模式判断改为仅 `--headless` 进入无界面流程，使 `--file` 可作为 GUI 的预选文件；预测时间步长同步采用用户设置的时间窗，不再固定为 60 秒。
  4. 根据实际 GUI 截图调整按钮配色、顶部数据说明字号和趋势图标题/图例位置，解决文字不可见、信息截断和图例重叠。
- **关键输出/产物**:
  - `C:\Users\37535\Desktop\实训留痕迹\基本直线绕城高速重庆\traffic_prediction.py`
- **验证结果**:
  - `python -m py_compile traffic_prediction.py` 通过。
  - `--headless` 使用 `1-1_trajectory.xlsx` 成功生成大屏图，原命令行模式未回归。
  - Windows 实际窗口验收通过：登录页可见，`admin/admin` 可进入主界面；分析得到 336,031 条有效记录、20 个完整时间窗，KPI、历史流量、移动平均、预测曲线及 PNG/Excel 导出按钮正常。
  - 自定义 120 秒时间窗预测步长测试通过，预测横坐标按 120 秒递增。
- **遗留事项/下一步建议**:
  - 用户重新执行 `python traffic_prediction.py` 即可使用修复后的 GUI。

### [Session #20260910-预测服务化] ONNX 与 FastAPI 预测服务集成
- **时间**: 2026-09-10 (Asia/Shanghai)
- **目标**: 阅读 `20260909预测服务化_ONNX与FastAPI.pptx` 与 `ONNX预测服务化技术讲解文档.docx`，将 location1 交通流量预测 Demo 合并到当前项目，并修复大屏依赖 FastAPI 未启动的问题。
- **核心操作与决策**:
  1. 区分教学示例与项目事实：location1 Demo 没有可直接导出的深度学习 checkpoint，实际算法是 60 秒窗口统计、5 窗口移动平均和线性趋势外推，因此保留该算法并封装为可导出的 ONNX 趋势预测核，没有虚构 LSTM 模型。
  2. 新增 `backend/prediction/model.py`、`service.py`、`contracts.py` 和 `export_onnx.py`，使用 FastAPI lifespan 在服务启动时加载并复用 ONNX Runtime Session，配置 CPU provider、线程数和预热推理；没有 ONNX Runtime 时保留 NumPy 兼容回退。
  3. 重构 `backend/dashboard_api.py` 为统一服务入口，提供 `/health`、`/model/info`、`/predict`、`/api/v1/predict/traffic-flow`、`/api/traffic_trend` 和自动生成的 `/docs`；`/api/traffic_trend` 在 TimescaleDB 未配置或不可用时读取本地 CSV。
  4. 更新 Streamlit `dashboard/data_loader.py` 与 `dashboard/app.py`，预测曲线优先调用远程 FastAPI，远程服务失败时继续本地计算；数据源状态准确显示为 `FastAPI / local-csv`。
  5. 增加后端 requirements、Dockerfile、docker-compose 和预测服务单元测试，导出 `location1_trend_v1.onnx`，支持单序列、T×1、1×T 以及动态 batch 输入。
- **关键输出/产物**:
  - [统一 FastAPI 服务](/D:/intelligent_transportation/intel-transportation/backend/dashboard_api.py:1)
  - [预测服务包](/D:/intelligent_transportation/intel-transportation/backend/prediction/service.py:1)
  - [ONNX 模型](/D:/intelligent_transportation/intel-transportation/backend/prediction/models/location1_trend_v1.onnx:1)
  - [Dockerfile](/D:/intelligent_transportation/intel-transportation/intel-transportation/backend/Dockerfile:1)
- **验证结果**:
  - ONNX checker 通过，opset 14；PyTorch 与 ONNX Runtime 最大误差 `0.00000000`。
  - 后端预测单元测试 4 项通过，dashboard 数据层测试 2 项通过。
  - HTTP 验收通过：`/health`、`/model/info`、`/api/traffic_trend`、`/docs` 均返回 200；单序列与双序列 batch `POST /predict` 均返回 200。
  - Streamlit 页面显示 `数据状态：FastAPI / local-csv`，KPI、趋势图、车辆构成和取证表正常渲染。
- **遗留事项/下一步建议**:
  - 配置真实 `TIMESCALEDB_DSN` 并启动 TimescaleDB 后，API 会优先读取 `checkpoint_traffic_1m`。
  - 高德地图 iframe 当前提示 JS API 未加载，需要在高德控制台补充本地来源白名单或检查 Key/Security Code 配置。

### [Session #20260910-2005] 9月10日可视化大屏任务总结与报告留痕
- **时间**: 2026-09-10 20:05 (Asia/Shanghai)
- **目标**: 将当天完成的 Streamlit + WebGIS 可视化大屏集成工作整理为正式实训记录，并把对应图片插入报告。
- **核心操作与决策**:
  1. 依据已完成的 `dashboard/` 模块整理任务目标、资料分析、独立只读展示层设计、数据降级顺序、页面功能、WebGIS Key 注入方式和验收结果。
  2. 生成 `20260910_streamlit_dashboard_runtime.png`，记录 KPI、车流趋势与预测、车辆构成和卡口态势；生成 `20260910_streamlit_dashboard_dataflow.png`，记录 FastAPI/TimescaleDB、CSV/Parquet、`data_loader` 缓存、Streamlit 和 WebGIS 的链路。
  3. 使用 `dashboard/update_report_20260910.py` 将内容追加到 `人工智能智慧交通实训报告_新.docx`，形成第 9 节“Streamlit 与 WebGIS 可视化大屏集成（9月10日）”，并插入图 9-1、图 9-2。
  4. 使用 `dashboard/fix_report_layout_20260910.py` 将最终总结章节设置为新页开始；通过 Word 转 PDF、Poppler 转 PNG 逐页检查，确认新增图片、图题、中文文本和分页没有重叠或裁切。
- **关键输出/产物**:
  - 更新后的 [人工智能智慧交通实训报告_新.docx](/D:/intelligent_transportation/人工智能智慧交通实训报告_新.docx:1)
  - [20260910_streamlit_dashboard_runtime.png](/D:/intelligent_transportation/assets/dashboard/20260910_streamlit_dashboard_runtime.png:1)
  - [20260910_streamlit_dashboard_dataflow.png](/D:/intelligent_transportation/assets/dashboard/20260910_streamlit_dashboard_dataflow.png:1)
- **验证结果**:
  - Word 转 PDF 成功，最终报告渲染为 14 页。
  - 新增第 9 节正文完整，图片和图题均相邻可读。
  - 由于当前 bundled runtime 未提供 `soffice.exe`，文档渲染验收使用已安装 Microsoft Word 的只读 PDF 导出配合 Poppler PNG 检查完成。
- **遗留事项/下一步建议**:
  - 后续若补充真实高德 Key，可再增加一张高德暗色底图的实际运行截图，并在报告中作为 WebGIS 配置验证图。

### [Session #20260910-高德地图错误排查] 高德 JS API 加载失败修复
- **时间**: 2026-09-10 (Asia/Shanghai)
- **目标**: 继续排查大屏中高德地图显示“JS API 未加载”的问题，并完成实际页面验收。
- **诊断**: 独立检查确认高德 CDN 可访问；生成的 HTML 中 `json.dumps(api_key)` 被直接放入 `<script src>` URL，造成 `key="..."` 的嵌套双引号，属于前端参数拼接错误。
- **修改**: 在 `dashboard/map_component.py` 中使用 URL 编码替换 Key；在 `dashboard/app.py` 中增加 `dashboard/.streamlit/secrets.toml` 的启动目录兜底读取；新增 `dashboard/tests/test_map_component.py` 回归断言；同步更新 `dashboard/README.md`。
- **验证**: Streamlit 页面刷新后实际显示高德底图、缩放控件、AutoNavi 版权和 7 个监测卡口；`py_compile` 通过，`unittest discover -s dashboard/tests -v` 的 3 项测试全部通过。当前 `.venv-dashboard` 未安装 pytest，未执行 pytest 命令。
- **安全留痕**: 真实 Key 与安全密钥仅保留在本地 secrets 配置中，未写入日志、测试文件或项目记忆；后续仍需在高德控制台维护本地来源白名单。

### [Session #20260910-大屏宽屏改造] 交通流量可视化大屏重构与端到端联调
- **时间**: 2026-09-10 (Asia/Shanghai)
- **目标**: 根据《程序代码.docx》和用户提供的高德接入、交通流量大屏、布局设计、数据流更新参考图，改造当前 Streamlit 大屏并跑通完整流程。
- **实现**: 重写 `dashboard/app.py` 页面编排与样式，加入暗色宽屏头部、实时钟、数据源状态、四项彩色 KPI、流量趋势与 ONNX 预测、车型环图、路口时段热力图、高德地图和折叠式取证区域；保留 API/CSV/Parquet/示例数据降级、缓存、自动刷新、卡口筛选和地图安全配置。
- **文档边界**: 教学文档中的硬编码账号、模拟数组和明文凭据未复制进业务代码；继续使用项目已有 FastAPI、ONNX 和本地资产。
- **验证**: 浏览器新标签页成功显示所有模块及 7 个监测卡口；FastAPI 健康、流量、预测接口分别返回 200；Streamlit 健康检查返回 200；3 项 `unittest` 全部通过。
- **运行**: FastAPI 使用 `intel-transportation/.venv` 的 uvicorn 进程监听 8000，Streamlit 使用 `.venv-dashboard` 监听 8501。

### [Session #20260910-1913] 将9月9日交通流量预测任务回填实训报告
- **时间**: 2026-09-10 19:13 (Asia/Shanghai)
- **目标**: 将 9 月 9 日重庆绕城高速交通流量预测与可视化任务总结留痕到实训报告，并补充对应结果图片。
- **核心操作与决策**:
  1. 在既有报告第 7 节之后新增第 8 节“重庆绕城高速交通流量预测与大屏可视化（9月9日）”，保持原文档章节层级、段落风格和版式。
  2. 记录模块关系分析结论：该模块与智能交通项目在交通数据分析和展示层面相关，但属于离线预测专项，采用独立工程运行更利于与 YOLO/OCR/Kafka/TimescaleDB 主链路解耦。
  3. 记录数据处理流程：读取 CQSkyEyeX 轨迹数据，过滤末尾残缺的 0.72 秒时间窗，将 336,166 条原始记录清洗为 336,031 条有效记录，并按 60 秒时间窗及车辆 ID 去重统计 20 个完整时间窗。
  4. 记录分析与修正方法：通过时间轴对齐消除 1200 秒处的断裂/断崖，使用 5 窗口移动平均平滑历史曲线，并用 `LinearRegression` 生成未来 15 步预测。
  5. 将实际完成的大屏功能写入报告：KPI、历史流量、移动平均、预测曲线及 GUI/`--headless` 输出方式；未虚构车型环图或车道柱状图等未实现功能。
- **关键输出/产物**:
  - 更新后的 [人工智能智慧交通实训报告_新.docx](/D:/intelligent_transportation/人工智能智慧交通实训报告_新.docx:1)
  - 内嵌结果图：`C:\Users\37535\Desktop\实训留痕迹\基本直线绕城高速重庆\traffic_prediction_dashboard.png`
  - Word COM 导出并检查的 PDF：`D:\intelligent_transportation\qa_report_sep9_word\report_sep9.pdf`
- **验证结果**:
  - `python-docx` 结构检查通过：109 段落、1 个表格、8 张内嵌图片；第 8 节、图题与图片顺序正确。
  - Word 导出 PDF 后渲染检查通过：共 12 页，无图片裁剪、溢出、标题/正文重叠，图 8-1 与题注位置正常。
- **遗留事项/下一步建议**:
  - 如需提交或打印，直接使用更新后的实训报告；后续新增任务继续沿用第 8 节之后的章节编号。

### [Session #20260910-1825] Streamlit + WebGIS 可视化大屏集成
- **时间**: 2026-09-10 18:25 (Asia/Shanghai)
- **目标**: 根据 `20260910可视化大屏_Streamlit与WebGIS.pptx` 及配套 DOCX，分析并实现当前智能交通项目的可视化大屏模块。
- **核心操作与决策**:
  1. 区分附件中的教学扩展方案与当前项目真正可落地部分，采用独立只读 Streamlit 展示层，不阻塞 YOLO/OCR/Kafka/TimescaleDB 写入链路。
  2. 新增 `dashboard/data_loader.py`，按 FastAPI 实时接口、本地 CSV、Parquet、内置确定性示例数据顺序降级，避免数据库/Kafka 未启动时页面白屏。
  3. 新增 `dashboard/app.py`、多页面入口、Plotly 图表、预测曲线、四项 KPI、违章取证展示和 `map_component.html` 高德地图组件；加入暗色主题、自动刷新、运行说明和专属依赖清单。
  4. 在 `.venv-dashboard` 安装大屏依赖并启动 Streamlit，验证首页、多页面 URL、HTTP health、浏览器渲染和地图模板替换。
- **关键输出/产物**:
  - [dashboard/app.py](/D:/intelligent_transportation/dashboard/app.py:1)
  - [dashboard/data_loader.py](/D:/intelligent_transportation/dashboard/data_loader.py:1)
  - [dashboard/map_component.html](/D:/intelligent_transportation/dashboard/map_component.html:1)
  - [dashboard/README.md](/D:/intelligent_transportation/dashboard/README.md:1)
  - Streamlit 预览：`http://127.0.0.1:8501`
- **验证结果**:
  - `compileall` 通过。
  - `unittest discover -s dashboard/tests -v`：2 项通过。
  - `/_stcore/health` 返回 `200 ok`，首页与多页面均可渲染。
- **遗留事项/下一步建议**:
  - 配置 `AMAP_KEY` 与 `AMAP_SECURITY_CODE` 后验证高德真实底图。
  - 生产化时将现有 API 的数据库密码占位符改为环境变量，并为高频查询增加连接池/缓存策略。

### [Session #20260907-1510] 实训报告文档安全备份
- **时间**: 2026-09-07 15:10 (Asia/Shanghai)
- **目标**: 为当前已内嵌全部图表与最新实验成果的实训报告创建完整镜像备份。
- **核心操作与决策**:
  1. 完整性复核：确认源文件 \人工智能智慧交通实训报告_新.docx\（101段落、7插图、985KB）状态完好。
  2. 创建备份镜像：生成 \人工智能智慧交通实训报告_新_20260907_备份.docx\。
  3. 校验验证：通过 python-docx 对备份文件进行段落与图片形状校验，确保字节级无损复制。
- **关键输出/产物**:
  - 备份文件：[人工智能智慧交通实训报告_新_20260907_备份.docx](/D:/intelligent_transportation/人工智能智慧交通实训报告_新_20260907_备份.docx:1)
- **遗留事项/下一步建议**:
  - 源文档与备份镜像均已就绪。

### [Session #20260907-1530] 零侵入实施TimescaleDB实验、生成截图并回填报告
- **时间**: 2026-09-07 15:30 (Asia/Shanghai)
- **目标**: 在不影响项目源代码的前提下，实施 Notebook 时序实验，生成关键执行结果截图/图表并自动回填至实训报告 Word 文档。
- **核心操作与决策**:
  1. 容器环境就绪：启动 Docker Desktop 并激活底层 TimescaleDB 容器（PostgreSQL 14 + TimescaleDB 2.19.3）。
  2. 零侵入隔离执行：编写独立测试脚本 \
un_day1_experiment.py\，在独立表 \	raffic_flow\ 与视图 \low_hourly\ 上全流程跑通练习 0 至练习 4，不触碰业务代码。
  3. 资产渲染生成：产出 3 组 300DPI 高清图像（超表写入终端卡片、time_bucket聚合终端卡片、24小时时序流量与速度双面板趋势图）。
  4. 报告图文回填：将 3 幅图表与标准题注精准插入 \人工智能智慧交通实训报告_新.docx\ 第 7 小节（9月5日），并在 WPS 中完成无损热加载。
- **关键输出/产物**:
  - 独立实验脚本：\
un_day1_experiment.py\
  - 截图与图表资产：\ssets/exp_day1/exp_fig1_hypertable_insert.png\, \exp_fig2_timebucket_cagg.png\, \exp_fig3_traffic_trends.png\
  - 更新后的实训报告：[人工智能智慧交通实训报告_新.docx](/D:/intelligent_transportation/人工智能智慧交通实训报告_新.docx:1)
- **遗留事项/下一步建议**:
  - 用户可随时通过 \python run_day1_experiment.py\ 一键复现实验并重新渲染结果。

### [Session #20260907-1500] 写入9月5日TimescaleDB时序数据实验分析至实训报告
- **时间**: 2026-09-07 15:00 (Asia/Shanghai)
- **目标**: 将 Day1 TimescaleDB 时序数据实验分析成果规范写入实训报告，日期标记为 9 月 5 日，并与文档既有格式完全对齐。
- **核心操作与决策**:
  1. 格式对齐与分析：严格匹配既有文档的 \Heading 2\ 标题阶梯与 \Normal\ 原生样式（\• <子任务>：<描述>\ 格式）。
  2. 报告内容写入：在 \二、 实训内容与项目实践\ 下新增 \7. TimescaleDB时序数据实验与车流量存储分析（9月5日）\，系统性录入环境自检、超表分区、批量时序构造、time_bucket 降采样、连续聚合视图及工程化对接等 6 项核心要点。
  3. 日期与元数据同步：将文档前置签名日期同步更新为 \2026.09.05\，并在 WPS 中完成无损热加载重载。
- **关键输出/产物**:
  - 更新后的 [人工智能智慧交通实训报告_新.docx](/D:/intelligent_transportation/人工智能智慧交通实训报告_新.docx:1)
- **遗留事项/下一步建议**:
  - 文档已完成格式与内容校验，段落结构完整（92段落、1表格、4插图）。

### [Session #20260907-1440] 答疑与实训报告文件回滚
- **时间**: 2026-09-07 14:40 (Asia/Shanghai)
- **目标**: 解析 Day1 TimescaleDB 时序数据实验 Notebook 与项目的相关性，并应用户要求撤销实训报告修改、恢复至原始版本。
- **核心操作与决策**:
  1. 完成分析：解析 Day1_TimescaleDB时序数据实验.ipynb 的 5 大实验模块与项目时序存储（TimescaleDB）设计的映射关系。
  2. 执行撤销回滚：将 人工智能智慧交通实训报告_新.docx 还原回修改前的原始基线文档 人工智能智慧交通实训报告.docx（恢复为 85 段落标准结构，移除了后置合并的扩展章节）。
- **关键输出/产物**:
  - 恢复后的 [人工智能智慧交通实训报告_新.docx](/D:/intelligent_transportation/人工智能智慧交通实训报告_新.docx:1)
- **遗留事项/下一步建议**:
  - 文档已完全恢复至修改前状态。

### [Session #20260907-001] 部署跨会话/跨对话记忆共享系统
- **时间**: 2026-09-07 13:10 (Asia/Shanghai)
- **目标**: 实现不同任务与对话之间的记忆互相可见、状态连续继承。
- **核心操作与决策**:
  1. 诊断现状：确认 Codex 默认会话隔离，但每次会话启动均强制加载 `AGENTS.md`。
  2. 建立记忆中枢：创建 `PROJECT_MEMORY.md`、`.agents/memory/sessions.md` 和 `.agents/memory/decisions.md`。
  3. 升级项目指令：在 `AGENTS.md` 中写入《跨会话记忆共享协议》（Cross-Session Memory Protocol），定义会话启动必读、会话结束必写规则。
  4. 沉淀历史资产：梳理项目已有的车牌识别流水线、时序数据架构及实训报告，作为全局基线记忆。
- **关键输出/产物**:
  - `PROJECT_MEMORY.md`
  - `.agents/memory/sessions.md`
  - `.agents/memory/decisions.md`
  - 更新后的 `AGENTS.md`
- **遗留事项/下一步建议**:
  - 后续开启的任何新任务均会自动加载并更新记忆文件。

### [Session #20260904-Baseline] 历史任务基线记录（自动化梳理）
- **时间**: 2026-09-04 17:00 (Asia/Shanghai)
- **目标**: 完成智慧交通系统数据存储架构设计与实训报告编写。
- **核心操作与决策**:
  1. 完成 TimescaleDB 超表配置 (`create_hypertable.sql`、`setup_timescale_improvements.sql`)。
  2. 编写 DuckDB 离线分析脚本 (`duckdb_analysis.py`) 并验证导出性能。
  3. 完成实训报告文档自动化合并与生成 (`人工智能智慧交通实训报告_新.docx`)。
- **关键输出/产物**:
  - `数据存储架构设计文档.md`
  - `人工智能智慧交通实训报告_新.docx`
  - `duckdb_analysis.py` / `write_to_timescaledb.py`

### [Session #20260911-003] 九阶段总流程审计与真实数据链补全
- **时间**: 2026-09-11 (Asia/Shanghai)
- **目标**: 依据“项目总结与全流程回顾”九阶段运行图，结合项目记忆审计环境、视觉、OCR、边缘部署、存储、特征、预测、服务与大屏的真实完成度，并直接补齐可在当前仓库实现的断链。
- **核心结论**: 九阶段均存在代码或资产，但原始系统没有形成真实闭环。视觉主程序只发送 `traffic_violations`，消费者只接收结构不同的 `traffic_stream`；Compose 仅包含预测 API；Timescale 聚合字段在大屏标准化时被丢弃；预测模块是规则趋势模型而非 LSTM；YOLO 感知模型没有 ONNX/TensorRT 产物。
- **核心实现**:
  1. 新增 `TrafficObservation` 与 `ViolationEvent` 事件契约，主程序为新跟踪车辆发布流量事件，为有效车牌发布违章事件；Kafka 失败时写入 `data/offline_events.jsonl`。
  2. 使用有界 OCR 线程池、in-flight 去重与 PaddleOCR 推理锁替代无限线程；新增 `PlateRecognition.recognize()`，模型路径、视频源、卡口元数据和 Topic 改为环境变量配置。
  3. 重写 Kafka 消费者并新增 Timescale 初始化脚本，建立 `traffic_gps`、`traffic_violations` 超表及 `checkpoint_traffic_1m` 连续聚合；Compose 加入 TimescaleDB、Kafka、消费者、FastAPI 和 Streamlit。
  4. 修复 `vehicle_count=50` 被大屏误算成 1 的聚合契约错误；FastAPI 新增 `/api/detections` 与 `/api/v1/predict/checkpoint`，大屏可读取真实违章字段和对应证据图片路径。
  5. 新增 YOLO ONNX/TensorRT 导出工具，实际生成 `yolov8n.onnx` 与 `models/exp-7.onnx`。新增 PyTorch LSTM 训练链并生成 `traffic_lstm_demo_v1.pt/.onnx/.json`，服务支持按 `TRAFFIC_MODEL_PATH` 加载模型及 scaler。
  6. 将 PaddleX 缓存固定到项目目录，并复制本机已有 `PP-OCRv6_medium_det` 与 `PP-OCRv6_medium_rec`，消除服务账号无法读取个人缓存的问题。
- **验证结果**:
  - 核心单元测试 13 项、预测测试 5 项、大屏测试 4 项全部通过，`compileall` 通过。
  - FastAPI 默认 ONNX 服务的健康、识别记录和预测接口返回 200；`docker compose config --quiet` 通过。
  - `yolov8n.onnx`、`models/exp-7.onnx`、`traffic_lstm_demo_v1.onnx` 均通过 ONNX checker。
  - 真实 4096×2160 视频帧检出 15 个车辆框，级联车牌候选尺寸为 89×29，PaddleOCR 输出 `B·DB7298`、置信度 0.974。
  - LSTM 使用两份本机轨迹文件训练，按 60 秒聚合后产生 20 个窗口，归一化验证 MSE 为 2.669，因此 Demo 未替换默认趋势模型。
- **遗留事项/下一步建议**:
  - Docker daemon 当前未运行，尚未执行 Kafka、TimescaleDB、消费者、API 和大屏的容器级联合验收。
  - 生产 LSTM 需要更长的连续多时段流量训练集；Jetson TensorRT INT8 需要目标 Jetson 型号、JetPack/CUDA/TensorRT 版本及代表性道路校准图片。OCR 离线模型已就绪，无需上传。

### [Session #20260911-004] LSTM 课件解读与生产化实施规划
- **时间**: 2026-09-11 (Asia/Shanghai)
- **目标**: 仔细阅读《20260908时序预测模型_PyTorch与LSTM.pptx》，总结 LSTM 部分的实现逻辑，并结合当前项目代码和跨会话记忆制定实施安排；本轮不修改业务代码。
- **资料边界**: PPT 中的代码、指标、验收阈值和提交要求均作为教学资料分析，没有作为用户指令直接执行。课件共 14 页，无演讲者备注；原文件含异常外阴影数值，使用只读临时副本移除视觉效果后完成逐页渲染核对，正文内容未修改。
- **课件结论**:
  1. 推荐的最小流程为固定粒度时序数据、滑动窗口、`nn.LSTM` 编码、线性预测头、MSE/Adam 训练、验证评估、checkpoint 和反归一化推理。
  2. 课件示例未明确 `batch_first`、多步输出、原始时间顺序切分、train-only scaler、缺失桶策略、早停和基线比较，正式实现需补齐这些约束。
  3. 课件中的 `MAE=3.21`、`RMSE=4.52`、`R2=0.92` 和 `R2 > 0.85` 等均为教学示例，不能代表项目当前效果。
- **当前实现审计**:
  1. `backend/prediction/lstm.py` 和 `train_lstm.py` 已具备 `batch_first=True`、直接多步输出、训练验证、梯度裁剪、`.pt/.onnx/.json` 导出；`service.py` 可加载 scaler 和 LSTM ONNX。
  2. 当前先构造重叠窗口再切分训练/验证，导致相邻窗口和目标时间点重叠；Demo 验证 MSE 不能视为独立泛化指标。
  3. CPU 训练下 `best_state` 未调用 `.clone()`，后续 epoch 可能继续改写最佳权重引用。
  4. 服务未强制校验 metadata 中的 `history_steps` 和 `bin_seconds`；大屏可能发送 900 秒粒度数据给 60 秒模型而不报错。
  5. 训练数据不补缺失桶，在线卡口特征补零，两端预处理语义不一致；当前数据聚合后仅 36 个时间点和 20 个窗口，只能作为 Demo。
- **规划结论**:
  1. P0 建立统一数据模块，按原始序列先切分 train/validation/test，再分别构窗并设置 purge gap；统一时区、60 秒粒度、缺失值和异常值策略。
  2. P1 增加 rolling-origin 回测、MAE/RMSE/WAPE/R2、逐 horizon 指标和 last-value/移动平均/趋势/seasonal-naive 基线，只有独立测试集稳定优于当前趋势模型才晋级。
  3. P2 建立严格 artifact manifest，校验模型类型、特征顺序、历史窗口、采样粒度、预测范围、ONNX 形状和校验和；同步收紧 FastAPI、卡口查询和大屏请求契约。
  4. P3 完成候选/生产模型切换、回滚、线上预测误差回填和漂移监控；数据充分后再评估多卡口全局模型及时间、速度、天气等协变量。
- **数据需求**: 正式训练需补充按卡口连续采集的数据，至少包含 `time`、`checkpoint_id`、`direction`、`vehicle_count`；建议覆盖多个完整工作日与周末周期，生产评估最好保留数周数据中的最后若干完整日期作为不可触碰测试集。
- **验证状态**: 本轮只读审计；子代理复核了现有 LSTM 数据与预测测试均通过，并实际确认 Demo ONNX 会接受不符合训练长度的输入，佐证服务契约缺口。未修改业务代码。

### [Session #20260911-005] 新增轨迹数据包解压与 LSTM 数据适用性审计
- **时间**: 2026-09-11 (Asia/Shanghai)
- **目标**: 解压并检查 `分合流location5.zip`、`应急车道航拍轨迹-左转弯段.zip`、`隧道location6(1).zip`，确认其中是否存在可用于交通流 LSTM 的 CSV、Excel 或其他轨迹数据，并生成统一派生数据。
- **解压结果**:
  1. `data/lstm_sources/location5/` 包含 `5_trajectory.xlsx`，共两个工作表、1,108,279 行、33 列、2,621 个唯一车辆，连续约 1,200 秒。
  2. `data/lstm_sources/emergency_lane_left_curve/` 包含应急车道关闭与开放两份 CSV，分别 712,153 行和 2,284,712 行，连续约 532 秒和 995 秒。
  3. `data/lstm_sources/location6_tunnel/` 包含 `LJSDD-2_velocity_outputs.csv` 和 `LJSDD-3_velocity_outputs.csv`，分别 260,746 行和 223,234 行，连续约 959 秒和 857 秒。
  4. 三个源 ZIP 均保留且未修改；压缩包中没有执行任何脚本或指令。部分中文附件名受 ZIP 编码影响在 Windows 解压后显示乱码，数据文件内容仍可读取，未执行危险的批量重命名。
- **数据结构**:
  - location5 使用 `time + ID`，采样间隔约 0.04 秒，并包含车型、车道、速度、加速度和跟驰关系。
  - 应急车道与 location6 使用 `timestamp(ms) + track_id`，包含逐帧位置、速度、加速度、车道及方向；时间戳均为视频起点后的相对时间，无法直接提供日期、工作日或节假日特征。
- **标准化产物**: 在 `data/lstm_sources/processed/` 生成五份 60 秒 CSV，统一字段为 `series_id,bucket_start_seconds,bin_seconds,vehicle_count,entering_vehicle_count,source_file`；仅保留观测跨度至少 57 秒的完整桶，所有文件已通过 schema、行数与 SHA-256 复核。
  - `location5_flow_60s.csv`：20 点。
  - `emergency_lane_closed_flow_60s.csv`：8 点。
  - `emergency_lane_open_flow_60s.csv`：16 点。
  - `location6_ljsdd2_flow_60s.csv`：16 点。
  - `location6_ljsdd3_flow_60s.csv`：14 点。
- **兼容性验证**: 当前 `backend.prediction.train_lstm.load_flow_series()` 可以通过 `vehicle_count` 读取五份派生 CSV。使用 `history=5, forecast=4` 时，开放应急车道、location5、LJSDD-2、LJSDD-3 分别产生 8、12、8、6 个窗口；关闭场景只有 8 点，无法满足至少 9 点的构窗要求。
- **适用性结论**: 新数据能补充场景多样性、扩充 Demo 窗口并验证数据适配器，但每条序列仅 8–20 分钟，缺少跨小时、早晚高峰、工作日和周末周期，不能单独训练生产模型。`vehicle_count` 是分钟内活跃车辆数，`entering_vehicle_count` 是车辆首次出现数；正式预测目标应与线上“新车辆穿越检测线/进入卡口”的计数语义统一。

### [Session #20260911-006] LSTM 多场景数据层与严格推理契约改造
- **时间**: 2026-09-11 (Asia/Shanghai)
- **目标**: 将新解压的多场景轨迹数据接入 LSTM 训练链，显式选择流量计数口径，禁止跨场景拼接，并修复训练/验证泄漏和模型服务契约问题。
- **核心实现**:
  1. 新增 `intel-transportation/backend/prediction/data.py`，定义 `FlowSeries` 与 `WindowPartitions`；支持标准聚合 CSV、`timestamp(ms)+track_id`、`Time+ID`、`time+ID`、`time+vehicle_id` 等 schema。
  2. CSV 轨迹采用分块读取，Excel 按工作表读取必要字段；按文件路径、`series_id` 和连续时间片保持场景隔离，时间缺口自动拆成独立序列，重复时间桶和不一致粒度会明确报错。
  3. `train_lstm.py` 保留 `load_flow_series()` 和 `build_windows()` 旧导入路径，新增 `--target-column`、`--validation-ratio`、`--purge-steps`、`--minimum-training-windows`；短序列跳过并写入 metadata。
  4. 多场景训练使用整条场景留出验证，单场景使用互不重叠的时间段；scaler 只由训练数据拟合。最佳权重使用 `.detach().cpu().clone()`，checkpoint 增加最佳 epoch、验证损失和优化器状态。
  5. ONNX 保持动态 batch 并固定训练历史长度。`PredictionService` 从 metadata 加载 `history_steps`、`bin_seconds`、`target_column`，FastAPI 请求增加目标列并在推理前校验历史长度、粒度和口径。
  6. 新增 `backend/prediction/README.md` 与 `requirements-training.txt`，记录标准 schema、训练命令、依赖和候选模型边界。
- **模型产物**: 使用 5 份 processed CSV 和 `entering_vehicle_count` 训练 `traffic_lstm_multiscene_demo_v2.pt/.onnx/.json`；配置为 60 秒粒度、5 点历史、4 步预测、100 epoch。无泄漏切分后训练窗口 28、验证窗口 6，验证场景为 location6 LJSDD-3，关闭应急车道序列因只有 8 点被跳过。归一化验证 MSE 为 0.03165，仅用于技术链验证，不能视为生产泛化结论。
- **验证结果**:
  - `unittest discover -s tests -v`：20 项通过。
  - `unittest discover -s backend/prediction/tests -v`：6 项通过。
  - `unittest discover -s dashboard/tests -v`：4 项通过。
  - `compileall`、ONNX checker、ONNX Runtime 单 batch 与双 batch推理均通过；模型输入形状为 `['batch', 5, 1]`。
- **生产状态**: 默认 `TRAFFIC_MODEL_PATH` 仍指向 `location1_trend_v1.onnx`。新模型保留为 Demo；正式晋级仍需跨日期连续数据、独立测试集、滚动回测和与趋势/季节朴素基线的稳定比较。

### [Session #20260911-007] 老师 PyTorch/LSTM 文本要求对照与项目进度规划
- **时间**: 2026-09-11 (Asia/Shanghai)
- **目标**: 阅读老师提供的 PyTorch 与 LSTM 文本资料，对照当前项目识别缺失部分，并规划教学验收与项目生产化的后续进度。本轮只读分析，不修改业务代码。
- **资料解读**:
  1. 文本覆盖 PyTorch 张量、索引、NumPy 互转、自动求导、`nn.Module`、DataLoader、模型保存，以及 LSTM/Transformer 基础示例。
  2. 交通流方案使用 `volume + hour + day_of_week + is_holiday` 四特征、历史窗口 6、单步目标，按时间 70%/15%/15% 划分。
  3. 模型设计意图为 64→32 两级 LSTM、Dropout 0.2、FC 32→16→1；训练使用 MSE、Adam(lr=0.001, weight_decay=1e-5)、ReduceLROnPlateau、最多 100 epoch、patience=10/min_delta=0.001 的早停。
  4. 评估要求可推导为独立测试集上的 MAE、RMSE、R²和残差分布，并保存最佳权重、scaler、日志和推理入口。文中的 R² 0.94/0.989 等属于教学示例，不是项目真实验收结果。
- **示例问题**: 原文存在自动求导代码顺序错误、未定义 model/criterion/optimizer、全量拟合 scaler 泄漏、先构窗再切分泄漏、`hidden_sizes=` 语法错误、两层隐藏维度实现不符、训练和验证主体被注释、`val_loss` 未定义、缺少测试推理和残差依赖等问题，不能原样复制。
- **项目完成度**:
  - 已完成：PyTorch 训练基础、独立场景构窗、train-only scaler、LSTM 直接多步模型、MSE/Adam、梯度裁剪、最佳 checkpoint、metadata、ONNX、FastAPI 严格推理契约。
  - 部分完成：缺失桶当前拆段而非可配置前向填充；负值当前拒绝而非截零；模型有两层与 Dropout，但结构和老师配置不同；已有 train/validation，无独立 test。
  - 尚缺：四维日历特征、64→32 模型配置、weight decay、调度器、早停、逐 epoch 日志、test 指标、残差图和基线比较。
- **实施计划**:
  1. M1 可信评估闭环：增加独立 test partition、rolling-origin 回测、原始量纲 MAE/RMSE/WAPE/R²、逐 horizon 指标、残差 CSV/PNG 和 last-value/移动平均/趋势基线。
  2. M2 教学兼容特征与模型：数据层支持绝对时间及 `[volume,hour,dow,holiday]`；模型支持 `input_size=4`、隐藏层 64→32、Dropout 0.2、FC 32→16→1 和历史窗口 6。
  3. M3 稳定训练：加入 Adam weight decay、ReduceLROnPlateau、early stopping、逐 epoch JSONL/CSV 日志、最佳调度器状态和可复现训练配置。
  4. M4 服务联调：metadata 强制特征顺序和 scaler，FastAPI 在服务端生成或校验日历特征，完成 PyTorch/ONNX/API 一致性测试。
  5. M5 生产晋级：仅当独立 test 与滚动回测稳定优于 `location1_trend_v1` 时切换默认模型，并补充线上误差回填、漂移监控和回滚。
- **数据条件**: 当前轨迹数据只有相对秒数和短时片段，无法可靠生成小时、星期与节假日。需补充带绝对时间的连续卡口流量数据，至少覆盖多个工作日、周末和高峰周期；也可先提供每段视频的真实开始日期时间映射用于教学功能验证，但仍不能替代长期生产训练数据。

### [Session #20260911-008] UCI 公共交通流数据获取与教学标准表生成
- **时间**: 2026-09-11 (Asia/Shanghai)
- **目标**: 代用户寻找无需额外上传即可完成老师四特征 LSTM 方案的公开交通流时序数据，并将最佳候选下载、验证和标准化。
- **候选结论**:
  1. 首选 UCI Metro Interstate Traffic Volume：无需注册、CC BY 4.0，直接包含 `date_time`、`traffic_volume` 和 `holiday`，适合立即实现教学闭环。
  2. Caltrans PeMS 更适合长期多站点交通预测，但需注册审批且数据转换更复杂；NSW、Melbourne 和 WebTRIS 可作为后续多站点/高频数据补充。
- **数据落盘**:
  1. 从 UCI 官方地址下载 `metro_interstate_traffic_volume.zip`，ZIP SHA-256 为 `B99AEABCBD6CC86F642DA3A79D90883425798F58ABD3B3302DA2FA19DDA73768`。
  2. 在 `data/lstm_sources/public/uci_metro_interstate/` 保留官方 ZIP、CSV.GZ、解压 CSV 和来源 README。
  3. 生成 `uci_metro_teacher_compat.csv`，字段为 `series_id,timestamp,bucket_start_seconds,bin_seconds,vehicle_count,hour,day_of_week,is_holiday,holiday_name,source_file`。
- **转换与验证**:
  1. 官方原始数据 48,204 行，时间范围为 2012-10-02 09:00:00 至 2018-09-30 23:00:00，无核心字段缺失和负流量。
  2. 数据有 5,445 组重复时间戳，源于同一小时对应多种天气描述；每组的流量和节假日值均一致。按时间去重后保留 40,575 个唯一小时。
  3. 节假日标记按日期传播至当天全部现有小时；不填补时间缺口，后续构窗必须按 3600 秒连续性拆分。
  4. 标准表 SHA-256 为 `F2263920BFA5321A9C3B8BD8E6F8E89126A1164C61BD765F0D85E43D02B3ADFE`。
- **边界与下一步**: 该数据是美国 I-94 单站点小时流量，适合 `teacher_compat` 教学模型和评估基准，不用于替换中国道路生产模型。下一步实现专用 adapter、70/15/15 时序切分、`[B,6,4]` 输入、64→32 LSTM、训练治理和测试指标闭环。

### [Session #20260911-009] 老师四特征 LSTM 完整实现、训练与服务联调
- **时间**: 2026-09-11 (Asia/Shanghai)
- **目标**: 使用已下载的 UCI 公开交通流数据，完整实现老师文本要求的四特征 LSTM 数据、模型、训练、评估、保存、ONNX 和服务推理链，并保持当前生产预测链兼容。
- **核心实现**:
  1. `backend/prediction/data.py` 新增 `TeacherCompatSeries`、`TeacherWindowSet`、`TeacherWindowPartitions`、`load_teacher_compat_series()` 和 `prepare_teacher_compat_partitions()`；支持 UCI 原始 `date_time/traffic_volume` 与标准表 `timestamp/vehicle_count`，按 3600 秒缺口拆段。
  2. 数据使用全局原始时间轴先切分、后在各连续段构窗；训练截止 2017-05-26 04:00，验证从 2017-05-26 05:00 至 2018-01-27 14:00，测试从 2018-01-27 15:00 开始。train/validation/test 窗口分别为 22,505/5,812/5,823，scaler 仅拟合训练原始点。
  3. `backend/prediction/lstm.py` 支持不同隐藏维度的独立 LSTM stages；`teacher_compat()` 使用输入 4 维、隐藏层 64→32、Dropout 0.2、FC 32→16→1，输出 `[B,1]`，旧单变量 `[current,future...]` 契约保持可用。
  4. `backend/prediction/train_lstm.py` 增加双 profile、MinMax scaler、Adam `weight_decay=1e-5`、ReduceLROnPlateau、梯度裁剪、patience 10/min_delta 0.001 早停、逐 epoch CSV/JSONL、最佳 optimizer/scheduler checkpoint、scaler JSON、测试评估和 ONNX 数值校验。
  5. 新增 `backend/prediction/evaluate.py`，输出 MAE、RMSE、WAPE、R²、predictions.csv、residuals.csv、metrics.json、残差分布图及 last-value/moving-average/linear-trend 基线产物。
  6. `PredictionService` 支持 metadata 定义的多列 scaler、feature 顺序、时区、单步输出、输入输出 shape 核验，并在 metadata 无效时停止加载；通用 API 的步数和粒度默认跟随当前模型。
  7. 卡口预测端点可按模型粒度聚合流量并生成 hour/day_of_week/is_holiday；`dashboard/data_loader.py` 会读取 `/model/info`，对四特征单步模型递归调用以生成大屏所需的多步展示结果。
- **模型产物与结果**:
  - 模型：`backend/prediction/models/traffic_lstm_teacher_compat_v1.onnx/.pt/.json`，另有 `_scaler.json` 和 `_artifacts/`。
  - 训练最多 100 轮，第 18 轮早停，最佳 epoch 为 16；归一化最佳验证 MSE 为 0.0015564。
  - 独立测试：MAE 175.2549、RMSE 248.8427、WAPE 5.2254%、R² 0.984208。
  - last-value 基线：MAE 589.2294、RMSE 814.1015、R² 0.830979；移动平均与线性趋势基线更弱。
  - PyTorch/ONNX 最大绝对误差为 `5.960464477539063e-08`。
- **验证**:
  - `unittest discover -s tests -v`：20/20 通过。
  - `unittest discover -s backend/prediction/tests -v`：29/29 通过。
  - `unittest discover -s dashboard/tests -v`：5/5 通过。
  - `compileall` 与 `git diff --check` 通过；ONNX Runtime 实际输入为 `['batch',6,4]`、输出为 `['batch',1]`。
  - 临时 FastAPI 在 8011 端口完成 `/model/info`、省略默认参数的通用预测和大屏递归 4 步调用，验证后已停止。
- **边界**: 该模型仅是美国 I-94 单站点公开数据上的教学基准，生产默认 `location1_trend_v1` 未改变。上线中国道路 LSTM 仍需本地跨日期数据、匹配地区的法定节假日日历和滚动回测；当前服务按老师示例使用周末节假日简化特征。PyTorch 旧 ONNX exporter 会输出弃用和 LSTM trace 警告，但 checker、动态 batch 和 ONNX Runtime 推理均通过。

### [Session #20260911-010] 中国收费站数据获取、节假日特征与 LSTM 候选训练
- **时间**: 2026-09-11 (Asia/Shanghai)
- **目标**: 从网络寻找中国本地跨日期卡口数据，使用 `Asia/Shanghai`、中国法定节假日及调休规则重新训练 LSTM，并完成数据、模型、ONNX、FastAPI 和大屏链路验证。
- **数据获取与核验**:
  1. 选择 KDD Cup 2017 Highway Tollgates Traffic Flow Prediction 中国高速收费站数据。官方赛题用于收费站流量预测；当前可用文件来自 `Engineering-Course/kddcup2017` 第三方镜像。
  2. 下载 `volume(table 6)_training.csv`，包含 543,699 条逐车过站事件，SHA-256 为 `7c824059114e0a6af9c3d89ffe856032a8510e3677a970654e7a694a1be99b62`；下载 20 分钟聚合镜像，SHA-256 为 `f3311aef04ca4254f1497f426eedf54135c47438ba25784d8d55d501246a1727`。
  3. 新增 `prepare_kdd2017.py`，从原始事件自行聚合；10,063 个时间桶与镜像逐行一致。377 个无过车事件的桶无法区分零流量和采集缺失，因此不补零，并在 loader 中按时间缺口拆分连续片段。Provenance 记录哈希、来源、许可和缺失策略。
- **中国日历与特征契约**:
  1. 新增固定版 KDD 数据覆盖期日历，依据国务院办公厅《关于2016年部分节假日安排的通知》。2016-10-01 至 10-07 标记为国庆假日，10-08/09 标记为调休工作日。
  2. 新增 `china_kdd2017` profile，使用 `vehicle_count,hour,minute,day_of_week,is_holiday,is_makeup_workday` 六个特征，时间按 `Asia/Shanghai` 解释。普通周末不再被强制等同为法定假日。
  3. PredictionService、FastAPI 卡口特征和 Streamlit 递归预测读取同一份 metadata 日历契约；超出 2016-09-19 至 2016-10-17 的锁定范围会明确拒绝自动构造日历特征。
- **训练结果**:
  - 产物：`traffic_lstm_china_kdd2017_candidate_v1.pt/.onnx/.json`、`_scaler.json` 与 `_artifacts/`。
  - 输入 `[batch,6,6]`，六个 20 分钟历史点代表过去 2 小时，输出下一 20 分钟流量。
  - 全局时间顺序训练/验证/测试窗口为 6,693/1,448/1,467；训练截止 2016-10-09 06:40，验证从 07:00 至 2016-10-13 15:00，测试从 15:20 开始。
  - 第 18 轮早停并恢复第 17 轮最佳权重；测试 MAE 9.8123、RMSE 15.1802、WAPE 17.3400%、R² 0.864412。
  - last-value 基线 R² 0.827057，moving-average R² 0.719831，linear-trend R² 0.765566；有足够样本的独立连续片段 R² 范围为 0.7803 至 0.9215。
  - PyTorch 与 ONNX 最大绝对误差为 `5.960464477539063e-08`。
- **验证**:
  - 数据准备脚本可重复运行，源文件大小和 SHA-256 校验通过，原始聚合与镜像一致。
  - `unittest discover -s tests -v`：20/20 通过。
  - `unittest discover -s backend/prediction/tests -v`：32/32 通过。
  - `unittest discover -s dashboard/tests -v`：6/6 通过。
  - `compileall`、ONNX checker、ONNX Runtime 双 batch 通过；临时 FastAPI 在 8011 端口完成 `/health`、`/model/info` 和中国候选通用预测后已停止。
- **许可与生产边界**: 天池上游协议限定非营利学术研究，镜像没有独立开放许可证；数据是匿名中国收费站而非项目现场数据。候选可用于课程、实训、论文实验和非商业展示，不能直接定义为商业生产模型或重新分发。默认 `location1_trend_v1` 保持不变；生产晋级仍需有授权的现场跨日期卡口数据、2026 等上线年份的国务院日历和滚动回测。

### [Session #20260911-011] 中国 LSTM 默认激活与端到端运行排障
- **时间**: 2026-09-11 (Asia/Shanghai)
- **目标**: 判断中国 LSTM 是否可直接使用、项目预测流程是否跑通，并修复默认模型、2026 日历、历史数据、Timescale 查询和大屏降级等阻断。
- **实现**:
  1. 默认 FastAPI、`docker-compose.yml` 与 `.env.example` 改为加载 `traffic_lstm_china_kdd2017_candidate_v1.onnx`；metadata 标记 `research_candidate` 与非商业实训用途。
  2. 新增 2026 国务院法定放假 33 日、调休工作日 6 日及版本化运行时日历周期；训练日历仍明确为 2016，未知年份继续拒绝。
  3. 新增 `data/china_lstm_demo_records.csv`，以独立 `KDD-T1-D0` 卡口提供 18 个连续 20 分钟桶，保留原 `vehicle_records.csv` 不变。
  4. 卡口历史改为按 `checkpoint_id` 专门查询足量 Timescale 连续聚合，并按模型 `missing_bucket_policy` 处理；数据库没有该卡口时可回退本地演示数据。
  5. 大屏预测改为从原始记录一次聚合到模型粒度，禁止 15 分钟图表结果再次聚合成 20 分钟；单卡口 LSTM 不接收全部卡口汇总；图表显示实际模型名或趋势降级原因。
  6. 新增 `docker-compose.cached.yml` 作为备用方案；用户随后明确要求复用当前容器，因此没有启动隔离栈。
  7. 新增 `backend/sql/migrate_existing_pipeline.sql`，对现有 `timescaledb` 数据库执行幂等兼容迁移：保留 25 条旧 `traffic_gps`，补齐消费者字段、`camera_id` 回填、幂等唯一索引、查询索引及 `traffic_violations` hypertable。
  8. 消费者新增 `KAFKA_AUTO_OFFSET_RESET=earliest|latest` 配置。本地稳定组使用 `latest`，避免无消费组情况下重新处理两个主题中原有的 195 条未知历史消息。
- **验证**:
  - prediction 34/34、核心 20/20、大屏 7/7 测试通过；`compileall`、主 Compose、缓存 Compose 与 `git diff --check` 通过。
  - 默认 `/model/info` 返回中国 LSTM、`onnxruntime-cpu`、输入 `[batch,6,6]`、2026 日历可用且无加载错误。
  - `/api/v1/predict/checkpoint` 对 `KDD-T1-D0` 返回 200，首步为 11.04 辆/20 分钟；大屏递归四步返回 11.040、14.179、19.022、22.895，来源明确为中国 LSTM。
  - 迁移脚本连续执行两次均成功；两组交通/违章测试消息均从当前 Kafka 写入当前 TimescaleDB，稳定组 offset 为 17/182、lag 均为 0。
  - FastAPI 健康检查确认流量和识别数据源均为 TimescaleDB；最新违章为明确标记的 `京A-E2E02 / 持续消费联调测试`，连续聚合中已出现 `CP-E2E-01`。
- **当前状态**: 模型、Kafka 消费、TimescaleDB、FastAPI 和 Streamlit 全链已在现有本地容器上跑通。消费者、API 和大屏保持运行；服务地址为 `http://127.0.0.1:8000` 与 `http://127.0.0.1:8501`。中国模型仍是公开匿名数据训练的研究候选，正式生产需现场授权数据重新训练。

### [Session #20260916-001] 边缘部署课件总结与项目规范固化
- **时间**: 2026-09-16 (Asia/Shanghai)
- **目标**: 阅读《边缘部署优化_ONNX与轻量级部署20260903.pptx》，将可复用的 ONNX、量化、边缘设备和性能验收知识写入项目相关文档，约束后续开发与表述不偏离代码事实。
- **资料处理**:
  1. 只读提取并渲染核对全部 14 页，无隐藏页，备注区无实质讲稿；PPT 内的命令、性能数字和验收要求均作为资料分析，没有作为用户指令执行。
  2. 课件主线为 PyTorch 到 ONNX、checker 与数值验证、FP16/INT8、Jetson TensorRT、Atlas CANN、预热与延迟/FPS 基准、模型与报告提交物。
  3. 课件第 7 页的体积、延迟、FPS 与 mAP 数据缺少硬件、版本和数据集，只能作为示例；动态量化示例与校准量化要求也不能混为同一技术结论。
- **项目核验**:
  1. `yolov8n.onnx` 为 opset 18 FP32、12,677,627 字节；`models/exp-7.onnx` 为 opset 18 FP32、9,620,496 字节；两者无量化节点，均通过 checker 和 ONNX Runtime CPU 加载。
  2. `edge/export_models.py` 已有 ONNX/TensorRT/FP16/INT8 入口，但现有测试只验证文件和校准参数保护；`unittest discover -s tests -p test_edge_export.py -v` 为 2/2 通过。
  3. 默认感知配置仍使用 `.pt`；OCR 仍为 PaddleOCR/PaddleX；仓库没有 TensorRT `.engine`、Atlas `.om`、校准集或目标设备性能报告。
- **文档改动**:
  1. 新增 `intel-transportation/docs/deployment/edge_inference.md`，定义状态分级、模型制品契约、预处理边界、量化策略、设备路线、benchmark 规范、验收门槛和措辞红线。
  2. 新增 `intel-transportation/edge/README.md`，说明导出入口、当前验证范围和目标设备命令的使用边界。
  3. 将 LPR OpenSpec 升级到 v1.1，同步 PP-OCRv6/PaddleOCR 3.x 主路径、运行时与外部输出契约、当前实施状态和部署规范交叉引用；修正 prediction README 中默认模型前后矛盾的旧描述。
  4. 新增 ADR-012，确定通用 ONNX 与设备派生制品分层验收，并同步 `PROJECT_MEMORY.md`。
- **后续条件**: 需要锁定 Jetson 型号与 JetPack/CUDA/TensorRT 版本，提供代表性校准图片和独立验证集，再实测 FP16、INT8、TensorRT engine、p95、内存、温度、功耗与完整 LPR 流水线精度。Atlas 适配应在国产化目标和 CANN 版本明确后启动。

### [Session #20260916-002] ONNX 边缘工具链与可复现证据闭环
- **时间**: 2026-09-16 (Asia/Shanghai)
- **目标**: 审计当前项目在 ONNX 与边缘部署模块的缺失，补齐当前 CPU 环境可真实验证的工具、报告和文档，同时保留 Jetson/Atlas 实机边界。
- **实现**:
  1. `edge/export_models.py` 增加格式、设备、`imgsz` 和参数组合校验，禁止 CPU TensorRT、FP16+INT8、engine+simplify 等非法组合；INT8 强制有效 `dataset.yaml`，导出后检查文件和扩展名。
  2. 新增 `edge/validate_artifacts.py`，支持 ONNX checker、opset、I/O dtype/shape、SHA-256、量化节点、manifest、Provider 加载与回退检测，以及批量 JSON 报告。
  3. 新增 `edge/benchmark.py`，支持预热、正式测量、batch、动态 shape、固定随机种子、Provider 核验、avg/p50/p95/FPS、模型哈希、环境和 RSS 快照；补齐 `cpu/cuda/tensorrt` 别名。
  4. 新增 `edge/compare_models.py`，按类别和 IoU 一对一比较 `.pt` 与 `.onnx/.engine` 后 NMS 检测，支持匹配率、平均 IoU、置信度差门槛和显式 `--imgsz`。
  5. 更新 `edge/README.md` 和 `docs/deployment/edge_inference.md`，新增 `edge/reports/README.md`，把已实现、CPU 已验证、已有入口和未实现设备路径分开表述。
- **报告与结果**:
  1. `edge/manifests/onnx_artifacts_20260916.json` 记录车辆、车牌和默认中国 LSTM ONNX 的 SHA-256、大小、opset 与 I/O 契约；三份模型均通过 `CPUExecutionProvider` 加载且无回退，未检测到量化节点。
  2. Windows 11、ONNX Runtime 1.29.0、16 物理/24 逻辑核心、`batch=1`、10 次预热和 100 次正式测量下，车辆模型 avg/p95 为 20.098/21.351 ms、49.756 FPS；车牌模型为 15.114/16.808 ms、66.165 FPS；LSTM 为 0.059/0.092 ms。
  3. 车辆模型在一张真实违章图上 10/10 框匹配，平均 IoU 0.999998951。车牌模型在 184 张现有 1440p/4K 违章图、`imgsz=1280` 下有 103 张产生检测，参考与候选均为 121 个框，匹配 121/121，平均 IoU 0.999999516，平均置信度差约 `6.10e-7`。
- **边界**: benchmark 使用固定随机张量且只测 `InferenceSession.run`；行为一致性数据没有人工标注，只能证明两个后端输出一致。项目仍缺目标 Jetson/Atlas 硬件、TensorRT `.engine`、Atlas `.om`、FP16/INT8 校准与精度回归、真实视频端到端分段时延、原生峰值内存、温度、功耗和持续运行报告。
- **验证**: `unittest discover -s tests -p 'test_edge*.py' -v` 为 32/32 通过，项目根 `unittest discover -s tests -v` 为 50/50 通过；`compileall`、全部报告 JSON 解析、文档相对链接和 `git diff --check` 通过。
- **协作记录**: 前置导出器、benchmark、artifact validator 和文档核查分支已验收并集成；后续车牌样本筛选分支请求 `gpt-5.4/low`，因上游 `invalid_parameter` 未执行，根代理依据现有 184 张违章图片完成尺度诊断与报告生成。

### [Session #20260916-003] 9月7日至16日项目实训周报整理
- **时间**: 2026-09-16 (Asia/Shanghai)
- **目标**: 依据用户提供的旧版 Word 周报模板，将 2026年9月7日至9月16日可核实的项目工作填入，并严格保持模板结构与篇幅限制。
- **取证与内容边界**:
  1. 读取 `PROJECT_MEMORY.md`、会话记录、决策记录及本地项目文件时间线；仓库尚无 Git 提交，未使用“已提交”或“已发布”等无法证明的表述。
  2. 第二周覆盖 9月7日至13日，内容包括 TimescaleDB 时序实验、流量预测、大屏与 WebGIS、LPR/OCR、Kafka、数据库和 LSTM/ONNX 服务。
  3. 第三周覆盖 9月14日至16日，内容包括车辆/车牌 ONNX 核验、制品校验、基准、一致性对比、边缘部署与 LPR 文档；未把 9月17日以后工作写入。
- **文档处理**:
  1. 原文件 `23级华农实训周报(2).doc` 保持不变；通过 Word 只读转换为工作用 DOCX，确认转换前后两页渲染一致。
  2. 复制完整周报页形成第二周和第三周两页，保留封面、页眉 logo、横线、表格行列、合并、边框、字号和教师信息；姓名因未提供而留空。
  3. 新填内容使用新宋体 10.5pt，知识点严格 5 行、项目严格 3 行，每行仅勾选一个状态；修复模板项目状态单元格的隐含首行缩进，确保勾选可见。
- **交付与验证**:
  - 产物：`D:\intelligent_transportation\23级华农实训周报_9月7日至9月16日.docx`。
  - Word 分页与 PDF 渲染均为 3 页；最终逐页检查无溢出、遮挡、断表或字体缺失。
  - 结构检查：1 个 section、3 张表，后两张均为 16 行 8 列；知识点与项目状态均满足每行单选。
  - 页眉、页脚、关系和媒体部件已从模板原样恢复并通过逐字节哈希核对；最终 SHA-256 为 `2F7A98642D51588045AAD7780B1AE9192F7A5715AB9C0B6AFB363240EAD8BE41`。
  - 打包的 `render_docx.py` 因 Windows 环境缺少 `soffice.exe` 无法使用；改用本机 Word 的只读 PDF 导出与同一运行时的 `pdf2image` 完成逐页视觉验收。

### [Session #20260916-004] 数据存储课件、截图需求与项目符合性总审计
- **时间**: 2026-09-16 (Asia/Shanghai)
- **目标**: 仔细解析《数据存储实战_TimescaleDB与DuckDB20260904.pptx》及两张用户截图，将材料要求写入项目总报告，并审计当前代码、配置、测试和部署是否满足完整平台目标。
- **资料处理与边界**:
  1. PPTX 包内实际包含 14 页，OOXML 文本、表格和备注可完整提取；本机 PowerPoint 尝试打开时报告文件损坏。用户截图显示“共15页”，两种来源页数不一致，报告中未擅自补写不存在的第 15 页。
  2. PPT 主线为 TimescaleDB hypertable、分区、连续聚合、压缩/保留、DuckDB 离线分析、检测数据入库、提交物与验收；截图扩展出 Flink SQL、Checkpoint、窗口/CEP、多源接入、1-4 小时预测、Milvus Lite RAG、Traffic Cop Agent、CrewAI/Qwen-VL 和 WebGIS。
  3. 课件中的 `<50ms`、90% 压缩、10 倍提升等数字没有项目测试条件和原始报告，只按示例处理，不写成项目实测成绩。
- **项目审计结论**:
  1. 已有原型链为 `OpenCV/YOLO/PaddleOCR -> Kafka -> Python consumer -> TimescaleDB -> FastAPI/ONNX -> Streamlit/WebGIS`，TimescaleDB、DuckDB、预测服务和大屏均有可核验资产。
  2. 不满足项包括 Flink Standalone/Flink SQL/Checkpoint/CEP、真实连续 GPS/卡口多源协议、验证过的 1-4 小时预测、LangChain/Milvus Lite 法规 RAG、Traffic Cop Agent、CrewAI/Qwen-VL 事故研判和可审计应急审批闭环。
  3. `traffic_gps` 当前主要表达卡口固定坐标与单次车辆观测，不等同连续 GPS 轨迹；默认中国 LSTM 只预测下一 20 分钟桶，大屏递归约 80 分钟，不能宣称已完成 1-4 小时预测验收。
- **文档改动**:
  1. 原位更新 `人工智能智慧交通实训报告_新.docx`，更新前备份为 `人工智能智慧交通实训报告_新_20260916_更新前备份.docx`。
  2. 修正摘要及旧章节中将 Flink、Milvus、量化、Jetson/Atlas 描述为已完成的失实表述，OCR 主路径更新为 PP-OCRv6，并统一由 Word 多级编号生成标题编号。
  3. 新增第 2.11 节，嵌入两张截图，加入 PPT 要求表、九模块满足度矩阵、核心风险、P0-P4 路线及完成定义；单人开发估算依次为 1-2、3-5、4-7、5-10、3-5 天。
- **验证**:
  1. 项目环境中预测 34/34、Dashboard 7/7、Kafka/事件契约 4/4 测试通过，两份 Compose 静态校验通过。另一解释器因缺少 `psycopg2`、`fastapi` 和 `onnx` 在测试收集阶段失败，已作为环境锁定风险记录。
  2. 本次审计时 Docker daemon 未运行，未执行容器冷启动、5 分钟健康收敛、Kafka-Flink-TimescaleDB 集成、Checkpoint 恢复或故障恢复测试。
  3. 使用 Word 16 COM 导出 PDF，并将 21 页全部渲染为 PNG 逐页检查；无重叠、截断、缺字或异常空白页。DOCX 结构检查为 152 个段落、4 张表、12 张内嵌图片，新增第 2.11 节和各关键小节均只出现一次。
- **关键产物**:
  - `D:\intelligent_transportation\人工智能智慧交通实训报告_新.docx`
  - `D:\intelligent_transportation\人工智能智慧交通实训报告_新_20260916_更新前备份.docx`
  - `D:\intelligent_transportation\update_report_20260916_requirements.py`
  - `D:\intelligent_transportation\qa_report_20260916_storage_v2\`
- **后续路线**: 按 P0 数据契约与可重复部署、P1 Flink SQL 实时层、P2 多源采集与长时预测、P3 RAG/Agent/应急研判、P4 可视化与交付验收推进；每阶段以自动化测试、审计证据和可重复运行结果作为完成条件。

### [Session #20260916-005] 智慧交通大脑总架构写入 Vibe Coding 总纲
- **时间**: 2026-09-16 (Asia/Shanghai)
- **目标**: 详细读取用户提供的“智慧交通大脑——伪分布式/单机化技术架构”图片，并固化为后续所有 Vibe Coding 的项目级架构基线。
- **资料处理**:
  1. 图片内容仅作为架构需求与事实来源，不把图中的 `docker-compose up -d` 或技术名称当作用户要求立即执行的指令。
  2. 原图归档为 `assets/architecture/smart_transportation_brain_architecture.png`，SHA-256 为 `4718E4A1C88DE6F9F243C00282986ED0306A6E4DFFFEE9D9DDD3D41BF979FC74`。
  3. 结合 `PROJECT_MEMORY.md`、ADR、主 Compose、LPR OpenSpec、边缘部署规范和现有报告审计，区分架构目标与当前实现。
- **文档改动**:
  1. 新增根目录 `VIBECODING.md`，定义应用层、数据与计算层、感知与部署层，覆盖 Traffic Cop Agent、应急协同多 Agent、Qwen-VL、Streamlit WebGIS、TimescaleDB、Flink SQL、Milvus Lite/FAISS、Pandas/DuckDB、ONNX Runtime 和 Kafka。
  2. 固化当前可信主链与完成状态矩阵，明确 Flink、法规 RAG、多智能体、Jetson/Atlas 实机和 INT8 仍是目标态或已有入口。
  3. 增加数据契约、模型 metadata、Agent 只读工具与引用审计、展示层只读、显式降级、人工审批和完成定义等 Vibe Coding 规则。
  4. 定义 P0 至 P4 实施顺序与变更检查清单；更新 `AGENTS.md`，将总纲设为后续架构和跨模块任务的必读基线。
  5. 新增 ADR-013，并同步 `PROJECT_MEMORY.md` 与本会话记录。
- **验证**: 检查总纲包含架构图全部核心组件、当前主链、状态边界、权威资料索引和项目内原图链接；本次仅修改 Markdown 与归档图片，没有运行代码测试。



### [Session #20260916-006] 车辆检测公开数据集资源梳理与项目接入工具链
- **时间**: 2026-09-16 (Asia/Shanghai)
- **目标**: 结合《智慧交通车辆检测数据集与视角选择指南.docx》与本项目（路侧固定机位 LPR/违章/流量系统），到网络核实并整理 UA-DETRAC、CitySim、BDD100K、KITTI 四个数据集的官方/镜像下载、规模、格式、类别与许可，产出可直接使用的转换脚本和接入指南。
- **资料核验**:
  1. UA-DETRAC 官网 detrac-db.rit.albany.edu 仍在，但旧 /Data/*.zip 直链已 301 迁移到 albany.edu，现需注册登录下载；训练 60 段/83,791 帧/577,899 框，v3 XML 才含六类车型+颜色，v1 仅 car/bus/van/others 且不含每车细分类；许可 CC BY-NC-SA 3.0。可用 Kaggle 全量(bratjay/ua-detrac-orig)与 17MB 轻量 YOLO 子集(xyz6674/ua-detrac-custom)、Roboflow Universe 免注册获取。
  2. CitySim(UCF-SST) 为无人机 30fps、12 地点、1140 分钟轨迹 CSV（frameNum/carId/旋转四角点/speed MPH/laneId），完整数据申请制（Data_Request_Form.pdf -> citysim.ucfsst@gmail.com），仓库与 wiki 公开。
  3. BDD100K 检测 10 万图(7万/1万/2万)、约 9.5GB、10 类、JSON box2d 且带 weather/timeofday；门户注册下载，另有 archive.org、Kaggle YOLO、FiftyOne 镜像；教育研究免费、商用入 BDD/BAIR Commons。
  4. KITTI 目标检测 7481 图、8 类、CC BY-NC-SA 3.0；Ultralytics 内置 kitti.yaml 可自动下载，仅面向未来车载/多模态方向。
- **关键决策（视角映射）**: 固定路侧主线首选 UA-DETRAC 微调车辆检测器；CitySim 用于轨迹/流量与合流分流安全（不训练路侧 2D 检测器）；BDD100K 仅作夜间/雨天难例增强与红绿灯标志扩展（车载视角有域差）；KITTI 留待车载扩展。四数据集均不含车牌标注，不影响 exp-7.pt/PaddleOCR。
- **新增产物**:
  1. intel-transportation/data/datasets/detrac_to_yolo.py：UA-DETRAC 序列 XML(v1/v3)->YOLO，project4/detrac4/vehicle 三种类别方案，按序列切分防泄漏，忽略 ignored_region/out_of_view，图片默认硬链接。
  2. data/datasets/bdd_to_yolo.py：BDD JSON->YOLO，支持 --vehicle-only 与 --timeofday/--weather 过滤。
  3. data/datasets/citysim_to_flow.py：CitySim->与 backend/prediction/data.py 聚合帧同构的 60 秒流量 CSV（vehicle_count/entering_vehicle_count），含 per_series 与车速统计。
  4. data/datasets/train_vehicle_detector.py：YOLOv8 微调并导出 opset18 ONNX，自动解析 dataset.yaml 并打印应配置的 VEHICLE_CLASSES。
  5. data/datasets/README.md（资源清单+下载+快速命令）与 docs/datasets/车辆检测数据集资源与接入指南.md（视角对照、分步接入、合规红线）。
- **接入要点**: 微调后须把 config.VEHICLE_CLASSES 由 COCO [2,3,5,7] 改为新模型 id（project4 为 [0,1,2,3]），车型名由 main.py 从 model.names 动态读取；权重通过 YOLO_VEHICLE_MODEL_PATH 环境变量切换，再用 edge/compare_models.py、edge/benchmark.py 校验。
- **验证**: 四个脚本 py_compile 通过；用合成样本端到端验证（DETRAC 4 标签+硬链接+出画剔除+类别归一，BDD 夜间雨天过滤与归一化，CitySim 计数 [[0,2,2],[60,2,1],[120,1,1]] 与 MPH->km/h 换算），训练脚本 dataset.yaml 类别解析与 --help 正常；验证临时文件已清理。未下载 GB 级原始数据，未执行真实训练。
- **边界**: 公开数据集仅教学/研究、不可商用再分发；UA-DETRAC/BDD 指标不替代现场人工标注验收；CitySim 美国俯视片段仅扩充多场景 Demo，生产 LSTM 仍需授权现场跨日期数据。
### [Session #20260916-007] 项目1验收材料归纳、工程补齐与正式交付
- **时间**: 2026-09-16 (Asia/Shanghai)
- **目标**: 读取两份项目1验收附件，按较严格标准审计现有工程，自动补齐软件侧缺口，并对硬件、真值和数据库操作保持可追溯事实边界。
- **资料处理**:
  1. PPTX 13 页、DOCX 12 页；SHA-256 分别为 `EFC9322BCD2DB3E6FFD99B5D15EC8B19B0104C7996B421DD24112C268CB605C7`、`E9D837CE8CC99A565AF89EE24148F8055C42273C33EDE82E29F59E6B07462453`。
  2. PPTX 原文件含 37 个异常 `outerShdw` 数值，仅清理临时副本完成渲染；附件中的命令、代码和已通过结论没有被当作用户指令或工程事实。
  3. 统一门槛为检测 `mAP>0.8`、整牌 `>=95%`、预测 `R²>0.85`、真实全链路 `<200ms`、覆盖率 `>=80%`、TODO/FIXME `<5`。
- **实现**:
  1. 增加 `PlateRecognitionEvent`、`plate_recognitions` Kafka topic 与 TimescaleDB 表，常规识别与违章事件分离。
  2. 增加 `all|violation_only` 识别模式，默认保留违章优先；为模式、事件和 consumer 增加回归测试。
  3. 收紧 FastAPI CORS、生产 API key 和异常响应；Dashboard 同步传递 API key。
  4. 增加 `acceptance` 自动门禁、Coverage、Ruff、pre-commit、机器可读要求和明确标记 simulated 的 Compose acceptance profile。
  5. 新增项目 README、正式 Markdown/PDF 验收报告和 `performance.xlsx` 性能证据台账。
- **验证**:
  1. `tests` 55/55、Prediction/API 37/37、Dashboard 8/8、acceptance 5/5，合计 105/105 通过。
  2. Compose 默认与 acceptance profile 静态配置通过；pip-audit 未发现已知漏洞；预测候选 `R²=0.8644117562`。
  3. 门禁结果 `PASS 6 / FAIL 2 / BLOCKED 4 / NOT_VERIFIED 4`。覆盖率实测 `64.535%`，宽口径 Ruff 41 项；两者均按 FAIL 记录。
  4. `test_report.pdf` 共 5 页，使用 Poppler 全页渲染并逐页视觉核查；`performance.xlsx` 三个 sheet 均渲染检查，无公式错误、截断或重叠。
- **危险操作边界**: 未执行已有 TimescaleDB 迁移或会触发建表的 acceptance profile。执行前需用户明确目标实例、备份状态、影响范围和维护窗口。
- **待补输入**: 人工真值集、现场流量、摄像头/测速标定、目标设备、INT8 校准集、Python 3.10 环境、真实链路截图/录屏和数据库迁移确认。

### [Session #20260916-008] 项目演示与功能模块设计写入 Vibe Coding 总纲
- **时间**: 2026-09-16 (Asia/Shanghai)
- **目标**: 详细读取《项目答辩要求》和《功能模块与场景设计》两张截图，将演示闭环、模块职责和验收要求无痕整合到唯一项目总纲，保证后续 Vibe Coding 不偏离且不误报目标能力。
- **资料处理**:
  1. 归档 `assets/architecture/project_demo_defense_requirements.png`，SHA-256 为 `F9464D088A5572D7D9BDA974E4F86B76AF89B65FF67A0C70A17F03692D44FCA6`。
  2. 归档 `assets/architecture/project_function_module_design.png`，SHA-256 为 `8278393D8FCB3FF08A3C5AA8338CE3580773A3B3F8E15E983BC09AAEE1621497`。
  3. 截图内容只作为需求、场景和验收来源；没有执行其中的 Compose、Flink、Milvus、模型或 Agent 命令，也没有把演示要求解释成现有成果。
- **总纲整合**:
  1. 固化“Docker 一键启动 -> 实时感知 -> AI 预测 -> Agent 决策”最终演示闭环，并保持 Kafka 事件、TimescaleDB、FastAPI/ONNX 与 Streamlit/WebGIS 的现有职责边界。
  2. 增加采集接入、存储管理、实时计算、离线特征、AI 预测、智能指挥、应急处置和可视化八类功能模块与部署矩阵。
  3. 固化 Traffic Cop“理解、检索、预测、决策”闭环和 TimescaleDB 只读 SQL、预测服务、法规 RAG 三类最小工具。
  4. 定义 Docker 环境、违章抓拍、Agent 交互、RAG 问答、实时预警和多 Agent 应急六项必演示场景，以及启动健康、真值准确性、工具审计、法规引用、`<5s` 预警和人工审批等证据口径。
  5. 增加 Docker/TimescaleDB/Flink、YOLO/LSTM/ONNX、ReAct/RAG 和 CEP 的技术答问范围，并将六项场景写入 P4 最终验收。
- **状态边界**: 当前主链仍为 `OpenCV/YOLO/PaddleOCR -> Kafka -> Python consumer -> TimescaleDB -> FastAPI/ONNX -> Streamlit`；Flink、法规向量库、LangChain Agent、CrewAI 和 Qwen-VL 仍为目标态。Milvus Lite 按嵌入式索引和健康检索验收；`<5s` 未实测不得标记通过；Agent 现实动作必须人工审批。
- **验证**: 已核对图片转录、原图 SHA-256、Markdown 章节、资产链接、状态矩阵和记忆同步。本次为文档与图片归档任务，未运行代码测试或部署测试。

### [Session #20260916-009] 当前项目工程及关联文件全量归档
- **时间**: 2026-09-16 (Asia/Shanghai)
- **目标**: 将 `D:\intelligent_transportation` 当前完整快照打包，覆盖工程源码、数据、模型、虚拟环境、文档、隐藏目录、测试/验收资产和子工程 Git 元数据。
- **范围审计**:
  1. 根目录不是 Git 仓库；`intel-transportation/.git` 是尚无提交的子工程仓库元数据，纳入归档。
  2. 主要体积来自 `intel-transportation`、`data` 与 `.venv-dashboard`，本次不按常规源码包规则排除虚拟环境、缓存、数据集或模型。
  3. `intel-transportation/tmp/acceptance_artifacts/node_modules` 为外部目录联接，目标是 Codex 运行时缓存；打包时解引用，保证关联依赖随包交付。
- **交付约定**: 输出 `D:\intelligent_transportation_full_20260916_160255.tar.gz` 及同目录 SHA-256 校验文件；归档位于项目根目录之外，避免递归打包。
- **验证约定**: 检查 gzip 流、归档目录清单、关键文件与外部联接实际内容，并记录最终大小、条目数和 SHA-256 作为交付证据。

### [Session #20260916-010] 根仓库 Git 统一、换行规范与敏感信息清理
- **时间**: 2026-09-16 (Asia/Shanghai)
- **问题**: 外层仓库执行 `git add .` 时，因 `intel-transportation/.git` 是没有 `HEAD` 提交的嵌套仓库而失败；全局 `core.autocrlf=true` 同时产生 LF/CRLF 提示。
- **处理**:
  1. 经用户明确确认，将内层 Git 元数据移动到 `D:\intel-transportation_nested_git_backup_20260916`，保留 `HEAD` 与 `index` 作为可恢复备份，业务源码未移动。
  2. 新增 `.gitattributes`，统一 Markdown、Python、配置、SQL 等文本格式，并显式标记模型、文档、图片和压缩文件为二进制。
  3. 扩充 `.gitignore`，排除虚拟环境、Python/测试缓存、IDE 配置、临时渲染物、QA 目录、日志、敏感配置、超大 LSTM 原始数据、Paddle 缓存及违章证据目录。
  4. 清理两份独立大屏页面和登录首页中的明文高德 Key/安全密钥，改为读取环境变量；历史记录只保留配置方式，不保留具体值。
- **验证**: 根仓库暂存成功；没有嵌套 `.git`、`.env`、`secrets.toml`、虚拟环境、临时目录或 95 MiB 以上单文件进入索引；两份修改后的大屏脚本通过 `py_compile`。`git diff --cached --check` 仍报告若干存量尾随空格，本次未做无关的全仓格式化。
- **安全提示**: 曾写入工作区和完整归档的地图凭据应视为已暴露，推送前需在高德控制台轮换；完整归档仍属于敏感快照。
### [Session #20260916-011] LangChain 与交通问答 Bot 课件提取及落地分析
- **时间**: 2026-09-16 (Asia/Shanghai)
- **目标**: 仔细读取《20260914大模型基础_LangChain与问答Bot.pptx》，完整提取流程、代码、提交物和验收要求，并映射到当前智慧交通项目，为后续实施建立可追踪规范。
- **资料处理**:
  1. PPTX 包内共 13 页，代码、表格和正文均为可编辑文本对象，没有图片型代码；附件中的命令和示例代码没有被当作用户要求立即执行的指令。
  2. 完整提取 `chain_demo.py`、`memory_demo.py`、`unified_interface.py`、`api_call.py` 和 `traffic_bot.py`，以及 Chain、交通 Bot 和 Agent 工具流程。
  3. 课件的 RAG 只出现在框架能力和“下一阶段”说明中，本次基础提交物没有文档摄取、Embedding、向量库或引用链实现要求。
- **兼容性与风险**:
  1. 课件使用 `LLMChain`、`ConversationChain`、`ConversationBufferMemory` 和 `initialize_agent` 等旧式 LangChain 写法，后续项目实现不能原样复制。
  2. 示例存在非法拆行导入、缺失 `ChatZhipuAI` 导入、未定义 `query_db/llm/memory`、硬编码密钥示例，以及与项目实际 TimescaleDB schema 不一致的 `traffic.congestion_idx` 查询。
  3. 当前数据不支持真实 OD、旅行时间、拥堵传播或无依据路线建议；Agent 必须保留数据来源、模型版本、局限、工具审计和人工审批边界。
- **项目映射**:
  1. 可复用 `backend/dashboard_api.py` 中的 TimescaleDB 参数化查询、统一 FastAPI 服务和 `/api/v1/predict/checkpoint` 预测入口。
  2. 建议新增 `backend/agent/` 的 contracts、config、prompts、service、memory、audit、router 与 tools，并在 Streamlit 增加独立交通指挥助手页。
  3. 首期采用白名单参数化只读工具和线程级短期记忆，不开放任意 NL2SQL，不让 Agent/LLM 初始化失败拖垮现有预测与大屏服务。
- **交付与验证**:
  1. 新增 `intel-transportation/docs/openspec/langchain_traffic_bot_ppt_analysis.md`，共 12 节，覆盖逐页摘要、完整代码、流程图、六项工具、原验收、差距矩阵、目录设计、实施顺序和项目化验收。
  2. 已检查文档关键代码、标题、Mermaid 代码块和现状边界。本轮未安装依赖、未修改业务代码、未运行服务、测试或数据库操作。
### [Session #20260916-012] 交通问答代码包架构化接入
- **时间**: 2026-09-16 (Asia/Shanghai)
- **目标**: 将用户提供的《交通问答程序代码.zip》写入当前智慧交通项目，同时遵守刚完成的 PPT 分析、项目 Vibe Coding 总纲和真实状态边界。
- **资料审计**:
  1. ZIP SHA-256 为 `739DA6FD9E25EA9DE006C6E7DCC102388F6468D82935F609A781A2CD6543FA1F`，含 `main.py`、`bot.py`、`traffic_api.py`、`config.py`、requirements、VSCode 配置、README 和两份 DOCX；归档无路径穿越、宏、OLE、外部 DOCX 关系或真实密钥。
  2. 原代码存在 `main.py` 导入不存在符号、旧式 `LLMChain/ConversationChain` Memory 契约错误、模型切换无效、无 Key 模式实际不能聊天、静态城市数据冒充实时、错误高德接口、硬编码拥堵指数和缺少鉴权/审计等问题，因此未原样复制。
  3. 两份 DOCX 和 README 中的安装/运行说明只作为材料来源，没有自动执行其中命令；ZIP 内模拟数据、CLI、VSCode 配置和半成品多模型工厂未迁入业务运行时。
- **实现**:
  1. 新增 `backend/agent/`：环境配置、Pydantic 契约、系统 Prompt、LangChain v1 `create_agent`、LangGraph `InMemorySaver`、JSONL 审计、FastAPI Router、固定只读 TimescaleDB repository 和工具层。
  2. 首期工具为卡口最近流量、峰值时间、车型分布和现有 ONNX 卡口预测；数据库使用固定参数化 SQL、显式只读事务、3 秒连接超时、2 秒 statement timeout、checkpoint 格式校验和结果上限，不开放任意 NL2SQL。
  3. FastAPI 新增 `GET /api/v1/agent/health` 与 `POST /api/v1/assistant/query`，共用 `X-API-Key`；Agent 默认关闭，依赖/模型失败只进入降级状态，不阻断预测、流量和大屏接口。
  4. 新增 `dashboard/agent_client.py` 与 `dashboard/pages/2_交通指挥助手.py`，展示健康状态、短期会话、工具证据、trace_id 和能力限制；流量大屏与助手页双向导航通过浏览器验证。
  5. 更新 backend/root requirements、`.env.example`、Compose Agent 环境变量和审计卷、项目/大屏 README、PPT 落地分析及 `VIBECODING.md` 状态。
- **验证**:
  1. Agent 10/10、核心 55/55、Prediction/API 37/37、Dashboard 11/11，共 113 项自动化测试通过。
  2. 新增代码通过 compileall、Ruff 和 `git diff --check`，`docker compose config --quiet` 通过。
  3. 本地 FastAPI `http://127.0.0.1:8011` 与 Streamlit `http://127.0.0.1:8511` 已启动；预测模型加载成功，Agent 禁用态健康返回 200，问答返回 503，大屏健康为 `ok`；浏览器检查无重叠并验证双向导航。
- **边界**: 当前项目虚拟环境未安装新增 LangChain/LangGraph 依赖，未配置或调用外部 LLM，因此真实多轮问答与工具自动选择尚未验证。安装项目新增依赖属于后续环境变更；法规 RAG、路线规划、正式拥堵指数、持久化多实例记忆和真实交通控制仍未实现。

### [Session #20260916-013] 阿里云百炼模型真实联调
- **时间**: 2026-09-16 (Asia/Shanghai)
- **目标**: 使用用户提供的阿里云百炼模型密钥和 `deepseek-v4-flash-0731`，完成 LangChain Agent、FastAPI 与 Streamlit 的真实端到端联调。
- **配置与安全**:
  1. 百炼密钥只写入 Git 忽略的 `intel-transportation/.env` 并注入本地运行进程，未写入受版本控制文件、示例配置、审计日志或交付回复。
  2. `load_agent_settings()` 新增 `DASHSCOPE_API_KEY` 兼容；`aliyun-bailian`、`bailian`、`dashscope` provider 在未显式指定时使用中国内地共享 OpenAI 兼容端点和 `deepseek-v4-flash-0731`。
  3. `.env.example`、Compose 和 Agent README 已切换为百炼联调示例；生产环境仍应使用 API Key 所属业务空间的专属 base URL。
- **真实联调**:
  1. OpenAI SDK 最小探针成功返回模型 `deepseek-v4-flash-0731`，证明当前密钥、模型权限和共享端点可用。
  2. LangChain v1 `create_agent` 成功调用 `query_checkpoint_flow`，工具证据包含正确工具名、参数和结果摘要，证明 Function Calling 可用。
  3. 启用态 FastAPI `GET /api/v1/agent/health` 返回 `enabled/configured/available=true`；普通问答和卡口工具问答均返回 200。未配置 TimescaleDB 时，工具返回 `source=unavailable`，模型明确说明无数据并拒绝编造。
  4. Streamlit 助手页显示百炼模型已就绪，浏览器验证真实问题提交、模型回答、工具证据、限制提示和 `trace_id` 均正常。
- **验证**:
  1. 核心与边缘 55/55、Prediction/API 36/36、Agent 11/11、Dashboard 11/11，共 113 项测试通过；另有 1 项依赖临时 ONNX 导出制品的预测测试按设计跳过。
  2. 新增配置代码通过 Ruff，`git diff --check` 和 `docker compose config --quiet` 通过。
- **边界**: 本轮没有连接 TimescaleDB 实时库；法规 RAG、路线规划、正式拥堵指数、持久化多实例记忆和现实交通控制仍未实现。用户在对话中直接披露过密钥，完成环境迁移后建议在百炼控制台轮换该密钥。

### [Session #20260916-014] 9月14日LangChain交通问答任务写入实训报告
- **时间**: 2026-09-16 (Asia/Shanghai)
- **目标**: 将基础交通问答Bot的架构化接入、阿里云百炼真实联调和项目适配结论提炼写入 `人工智能智慧交通实训报告_新.docx`，归入9月14日实训任务。
- **文档更新**:
  1. 在“基于PyTorch与LSTM的中国时序预测模型与系统全链路联调（9月11日）”和“数据存储实战课件需求解析与项目符合性评估（9月16日）”之间新增“基于LangChain的交通问答Bot与百炼模型联调（9月14日）”。
  2. 新章节记录教学课件和ZIP代码审计、LangChain v1 `create_agent`、系统Prompt、四类只读交通工具、LangGraph `InMemorySaver`、模型配置解耦、FastAPI/Streamlit接入、JSONL审计、百炼 `deepseek-v4-flash-0731` 普通问答与Function Calling实测以及113项自动化测试结果。
  3. 新增“教学Bot组件与当前项目化实现对照”表，明确 `LLMChain`、`PromptTemplate`、`ConversationBufferMemory`、`model_provider`、模拟城市数据、CLI和RAG在当前项目中的保留、替代或后续状态。
  4. 同步修订9月16日满足度矩阵、P3/P4实施计划、验证边界和实训总结，避免后续章节继续声称LangChain、Agent API或Agent页面尚未实现。
- **验证**:
  1. DOCX重新打开成功，新增章节唯一；文档现有161个段落、5张表，正文和表格中未出现百炼密钥标记。
  2. 本地Word只读导出25页PDF，使用Poppler渲染全部页面并逐页检查；新增章节跨页表头正常，全文无重叠、截断、缺字或异常表格边界。
- **边界**: 报告准确保留实时TimescaleDB Agent数据未接入、法规RAG未实现、短期记忆非持久、路线规划和现实交通控制未实现等状态，不把基础Bot联调写成完整Traffic Cop决策闭环。

### [Session #20260916-015] RAG法规知识库课件逆向与PROJECT_SPEC.md规格落地
- **时间**: 2026-09-16 (Asia/Shanghai)
- **目标**: 深度逆向 `20260915RAG知识库_向量检索与法规问答.pptx`，转化为可直接指导 Vibe Coding 的《系统架构与实施规格说明书》，并按用户要求固化为项目根目录上下文约束文件。
- **资料提取**:
  1. 只读解析 13 页 PPTX 的 OOXML 全文（文本、表格、备注、媒体），确认无图片型代码；备注仅为页码，媒体仅含空目录条目。
  2. 交叉提取同批配套《RAG知识库系统_技术文档.docx》（338 段），补全 Milvus Lite Schema/HNSW 代码、FAISS IVF 原生索引、法条正则结构化切分、legal_qa 拒答 Prompt、batch=64 入库、RAGAS 评估体系等课件未展开内容。
  3. 课件代码均为教学示例，存在旧版导入路径（langchain.text_splitter/RetrievalQA）、硬编码 gpt-4o-mini 与 law_id/law_article 字段名不一致问题，已在规格书中逐段标注。
- **交付物**: 新增根目录 `PROJECT_SPEC.md`（v1.0，六模块）：技术栈与运行环境（Milvus Lite 嵌入式首选/FAISS 备选、BGE/OpenAI 双通道 Embedding、HNSW+COSINE M=16 efC=256、百炼 LLM temperature=0.1）；领域模型五实体与 DDL（课件 Schema + VIBECODING 版本/哈希扩展字段）；离线摄取与在线问答双管道架构（Mermaid 时序/状态机）；检索/问答/Agent 工具契约与拒答 Prompt 契约；10 段课件代码的设计意图-参数-陷阱解析；Step 1-5 实施路线图（backend/rag/ 新模块，对齐既有 agent 开关与审计惯例）与验收映射附录。
- **约束强化**: AGENTS.md 第 0 节新增"实施规格约束"条目，引用 PROJECT_SPEC.md 并写入用户指定的 IDE Rules 约束语句；规格书附录 B 记录分步投喂策略。
- **验证**: 本轮为文档与规格交付任务，未安装 RAG 依赖、未修改运行代码、未执行服务或向量库操作；PROJECT_SPEC.md 中 RAG 子系统状态保持目标态，课件性能数字（92%/45ms 等）仅标注为教学参考。

### [Session #20260917-001] 法规知识库与launch.zip RAG代码融入项目
- **时间**: 2026-09-17 (Asia/Shanghai)
- **目标**: 将用户提供的《交通法规知识库.docx》与 launch.zip RAG 程序代码按 PROJECT_SPEC.md 融入 `intel-transportation` 项目。
- **材料审计**:
  1. `交通法规知识库.docx`（SHA-256 `C736BC974E023FA8E05DA27F978E3BBCCBF383DBA8DB3ED2767E20B627F4EC9A`）：含交通法规体系概述、超速（第90条）、酒驾醉驾（第91条）、闯红灯（第95条）处罚标准与知识库应用展望，作为 RAG 知识源。
  2. `launch.zip`（SHA-256 `DE97618B650BBC06AA069A25A771B928A028F9E9CF487993FBBE8793F6F916A7`）：ChromaDB + SQLAlchemy/SQLite + 旧版 LangChain 0.1.16 教学Demo；LLM 实为模板模拟回答；`rag_query.py` 存在 `req["question"]` 下标 bug；宣称的 20 条内置法规数据不在包内。
  3. 融合决策：按 PROJECT_SPEC 拒绝 ChromaDB/SQLite/模拟 LLM 路线，移植为 Milvus Lite 首选 + FAISS 兜底双后端与拒答契约；吸收其 QueryLog 审计、知识统计接口与懒加载单例服务思想。
- **工程落地**:
  1. 新增 `backend/rag/` 十四文件：config（TRAFFIC_RAG_* 环境配置 + LLM 密钥回退链）、contracts、document_io（无依赖 docx 提取）、chunker（第X条正则含"之一" + 章节感知 + 句界 500/50 + 条号/哈希元数据）、embedding（bge-local / openai-compatible / hashing-test 三通道，模拟响应强制标注）、vector_store（Milvus Lite HNSW+COSINE M=16 efC=256，重启自动 load_collection，维度不符自动重建；FAISS 持久化兜底）、registry（版本/SHA-256/有效期/状态 JSON 登记）、build_kb（同 doc_id 先删后插幂等摄取）、retriever（active 版本过滤 + 分数阈值）、qa（拒答契约 Prompt、temperature=0.1、LLM 失败降级为仅检索并标注）、service（健康/查询/重建/Agent search_only）、router（/api/v1/rag/health|query|rebuild）、audit（复用 agent JsonlAuditLog）。
  2. Agent 集成：`TrafficToolGateway` 增加可选 `law_search` 通道；新增第 5 个只读工具 `search_traffic_law`（校验问题长度、始终注册、未启用时如实返回不可用、evidence 摘要含条号）；系统 Prompt 新增法规工具规则。
  3. dashboard_api：RagService 接线（禁用安全、初始化失败不影响预测主链）、挂载 rag 路由、/health 暴露 traffic_rag；知识源 docx 置入 `data/rag/laws/`；新增 `backend/scripts/rag_smoke.py`。
  4. 配置同步：`.env.example` RAG 配置块、Compose `TRAFFIC_RAG_*` 环境与 `rag-data` 持久卷、requirements 增加 pymilvus/milvus-lite/faiss-cpu（sentence-transformers 标注按需）、根 `.gitignore` 排除 RAG 运行时产物保留 laws/。
- **验证**:
  1. venv 新装 pymilvus 3.0.1 + milvus-lite 3.2.1（确认 Windows 可用）+ faiss-cpu 1.15.0；适配百炼 Embeddings 单批 ≤10 条限制。
  2. 真实端到端 PASS：百炼 `text-embedding-v4`(1024D) 向量化 11 个知识块入 Milvus Lite，`deepseek-v4-flash-0731` 生成回答；酒驾/超速正确引用第91/90条与来源版本，电动车、刑法越界问题正确拒答；health 显示 simulation=False。
  3. 单元测试：rag 20 项（切分/存储/服务/路由）+ agent 法规工具 4 项；后端回归 125 项、dashboard 3 项全部通过；Ruff、`docker compose config --quiet`、compileall 通过。
- **边界**: BGE 本地路径未下载权重未实测；Top-3 命中率>85% 自建测试集验收、RAGAS/Faithfulness 评估、法规 PDF 原文与更多法规文档接入未完成；知识库现仅 1 部教学文档，不得表述为完整权威法规库；本轮未执行容器级 Compose 启动验证。
