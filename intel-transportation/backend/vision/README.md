# 多模态理解与监控视频智能解说（Qwen-VL）

对应 9/18 课件《多模态理解：Qwen-VL 与监控视频智能解说》。一条完整链路：
监控视频 → 抽帧 → 关键帧筛选 → 图文理解/事故分析 → 时间线 → 智能解说 + 告警，
并对外提供 REST 与 WebSocket。无密钥、无 GPU 时每个环节都给出**带原因的降级**，不编造结论。

## 目录结构（课件提交物）

| 课件要求 | 本目录文件 |
| --- | --- |
| `multimodal_system.py` | `VideoAnalysisSystem`（`run()` / `analyze_video()` / `analyze_image()` / `analyze_frame()`、`subscribe` 推流、`send_alert()`）+ `VehicleCounter` + `FrameFinding` / `VideoReport` |
| `video_processor.py` | 抽帧 `extract_frames`、关键帧检测 `select_keyframes` / `frame_difference`、`Frame`（长边缩放 + JPEG/base64）、`summarize_extraction` |
| `vl_analyzer.py` | `VLAnalyzer`：场景描述 / VQA / 事故分析 / 解说 / 告警；`AccidentAnalysis` 结构化标签；`RemoteVisionClient`（OpenAI 兼容）与 `TransformersVisionClient`（本机 Qwen2-VL） |
| `prompts/` | `scene_description` / `accident_analysis` / `narration` / `alert` 四份模板，`$变量` 用 `string.Template` 渲染（事故 JSON 含花括号，不能用 `str.format`） |
| `api_server.py` | 独立 FastAPI 服务。接口实现只在 `router.py`，本文件负责装配与启动，因此独立服务与大屏后端**共用同一份路由** |
| `README.md` | 本文件 |

支撑文件：`config.py`（环境变量装配）、`prompt_store.py`、`service.py`（生命周期与降级）、
`router.py`、`contracts.py`（Pydantic 响应模型）、`demo_clip.py`（合成演示片段）、`tests/`。

## 架构

```
输入：MP4 / 图片目录（RTSP 需先用 ffmpeg 落成 mp4，或用摄像头帧序列）
  │
  ├─ extract_frames     每 TRAFFIC_VISION_FRAME_INTERVAL 秒取一帧，上限 max_frames_per_video
  └─ select_keyframes   64×64 灰度「变化像素占比」≥ 阈值才保留（课件第 12 页的关键帧检测）
        │
        ▼  每个 Frame 并发两路
   ┌────────────────────────────┬──────────────────────────────────┐
   │ VehicleCounter（YOLOv8）   │ VLAnalyzer（Qwen-VL，多模态）    │
   │ COCO 类别 2/3/5/7          │ describe_scene → 画面描述        │
   │ 未安装 → vehicles=None      │ analyze_accident → 结构化标签    │
   └────────────────────────────┴──────────────────────────────────┘
        │  FrameFinding（index/time/vehicles/status/accident）→ 时间线 + WebSocket 逐帧推送
        ▼  _finish
   narrate(时间线事实串)  → 智能解说文本（150–250 字，必须指出异常首次出现的时刻）
   build_alert(每条事故)  → {title, level, summary, actions, push_text}
        ▼
   data/vision/runs.jsonl（分析记录）· alerts.jsonl（告警流水）· frames/<片段名>/*.jpg
```

关键帧判据用**变化像素占比**（`|Δ灰度| ≥ 24` 的像素比例，默认阈值 0.02）而不是平均灰度差：
固定机位监控里背景占绝对多数，平均差会被静止背景稀释到接近 0，事故帧筛不掉。

## 模型选型（课件第 5 页的对比）

选 Qwen-VL 作主模型，理由与课件一致：中文原生、OCR 强、支持多帧视频理解，且百炼提供
OpenAI 兼容端点——本仓库的 `backend/agent`、`backend/crew`、`backend/rag` 已经在同一条密钥链上，
多模态不必引入第二套部署方式。LLaVA 的差别是 CLIP ViT + 线性投影 + Vicuna，英文/通用场景够用、
中文与 OCR 需微调，因此**未实现 LLaVA 分支**：`vl_analyzer.py` 的 `VisionClient` 是 Protocol，
任何能收 `[text, image_url]` 消息的端点都能接（含 `gpt-4o`、本地 llama.cpp 服务），
换模型只改 `TRAFFIC_VL_MODEL` / `TRAFFIC_VL_BASE_URL`。

| | 远程端点（默认） | 本机 transformers |
| --- | --- | --- |
| `TRAFFIC_VL_BACKEND` | `remote` | `local-transformers` |
| 依赖 | `httpx`（已装） | `torch transformers accelerate`（未装，需 ≥16GB 显存） |
| 消息形状 | `content: [{type:text}, {type:image_url, image_url:{url:"data:image/jpeg;base64,…"}}]` | `apply_chat_template` + `AutoProcessor` |
| 默认模型 | `qwen-vl-max`（百炼）/ `gpt-4o` | `Qwen/Qwen2-VL-7B-Instruct` |

## 运行

### 1. 依赖

```bash
# 项目现有 .venv 已装：opencv-python / Pillow / httpx / fastapi / uvicorn / python-multipart
pip install opencv-python Pillow "httpx>=0.27" python-multipart
# 可选（本机推理与真实车辆计数，体积数 GB 且需要 GPU，本项目**未安装**）
pip install torch transformers accelerate ultralytics
```

`ultralytics` 缺失时 `VehicleCounter.available` 为 `False`，时间线里的车辆数返回 `None`
（`vehicle_source=unavailable`），**不会**把「没有检测器」报成「0 辆车」。

### 2. 配密钥（只走环境变量，绝不写进代码或仓库）

`.env` 里已备好 `TRAFFIC_VISION_*` / `VISION_*` 段，最小改动两行：

```
DASHSCOPE_API_KEY=sk-xxxx
TRAFFIC_VISION_ENABLED=true
```

`TRAFFIC_VL_PROVIDER=aliyun-bailian`（默认）时 `base_url` 自动指向百炼兼容模式，
`model` 默认 `qwen-vl-max`。密钥链回落顺序与 `backend/crew`、`backend/rag` 一致：
`TRAFFIC_VL_API_KEY` → `TRAFFIC_AGENT_API_KEY` → `DASHSCOPE_API_KEY` → `OPENAI_API_KEY`。

### 3. 四种跑法

| 入口 | 命令 |
| --- | --- |
| 演示脚本 | `python backend/scripts/vision_smoke.py`（本机 Mock 端点，无需密钥）／`--live`（真实 Qwen-VL）／`--source path` 换素材 |
| 独立服务 | `python -m backend.vision.api_server`（默认 `127.0.0.1:8600`，交互式文档 `http://127.0.0.1:8600/docs`） |
| 大屏后端 | `backend/dashboard_api.py` 已 `include_router(vision_router)`，与预测/RAG/Crew 共用端口，`GET /health` 里带 `vision` 一行 |
| 调试器 | 运行和调试 → `9/18 · 多模态视频解说演示脚本` |

命令行调用示例：

```bash
curl -F "file=@data/vision/demo/road_demo.mp4" http://127.0.0.1:8600/api/v1/vision/video
curl -F "file=@frame.jpg" -F "question=画面里有几辆车？" http://127.0.0.1:8600/api/v1/vision/analyze
curl "http://127.0.0.1:8600/api/v1/vision/results?alerts_only=true"
# WebSocket：连 ws://127.0.0.1:8600/api/v1/vision/stream，发 {"source":"data/vision/demo/road_demo.mp4"}
```

分析一次视频会产生 `描述 × 帧数 + 事故分析 × 帧数 + 解说 1 + 告警 N` 次模型调用，
30 帧约 60+ 次请求，是耗时主要来源。调试时先把 `TRAFFIC_VISION_MAX_FRAMES` 压到 6–8。

## 配置项

全部由环境变量驱动（`backend/vision/config.py`），越界值会被夹到合法区间而不是抛异常。

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `TRAFFIC_VISION_ENABLED` | `false` | 总开关；关闭时接口返回 503 + 原因 |
| `TRAFFIC_VL_BACKEND` | `remote` | `remote` / `local-transformers`（非法值回落 remote） |
| `TRAFFIC_VL_API_KEY` / `_MODEL` / `_BASE_URL` / `_PROVIDER` | 见上 | 端点与模型；provider 为百炼时套用其默认 base_url |
| `TRAFFIC_VL_LOCAL_MODEL` | `Qwen/Qwen2-VL-7B-Instruct` | 本机推理路径的模型名或权重目录 |
| `TRAFFIC_VL_TEMPERATURE` / `_TIMEOUT_SECONDS` / `_MAX_RETRIES` / `_MAX_TOKENS` | `0.2` / `60` / `1` / `800` | 分析类任务压低随机性 |
| `TRAFFIC_VISION_FRAME_INTERVAL` | `2.0` | 抽帧间隔（秒） |
| `TRAFFIC_VISION_MAX_FRAMES` | `30` | 单次分析帧数上限，超出按等间隔下采样 |
| `TRAFFIC_VISION_KEYFRAME_THRESHOLD` | `0.02` | 关键帧阈值（变化像素占比）；`0` 关闭筛选，全帧送模 |
| `TRAFFIC_VISION_JPEG_QUALITY` / `_MAX_SIDE` | `85` / `1024` | 送模图片编码 |
| `TRAFFIC_VISION_OUTPUT_DIR` | `data/vision` | `runs.jsonl` / `alerts.jsonl` / `frames/` 落盘位置 |
| `TRAFFIC_VISION_PROMPTS_DIR` | `backend/vision/prompts` | 自定义提示词目录 |
| `VISION_HOST` / `VISION_PORT` | `127.0.0.1` / `8600` | 独立服务监听 |
| `VISION_CORS_ORIGINS` | `http://localhost:8501,…` | 逗号分隔白名单，`*` 会被忽略 |
| `TRAFFIC_PLATE_ENABLED` | `true` | 单图车牌取证开关；关闭时 `/plate` 与 `/plate/health` 报 503/false，不影响图文链路 |

## 接口

统一 `{code, message, data, timestamp}` 信封（`contracts.py`）。状态码口径：

- **503** —— 未启用 / 缺密钥 / Prompt 缺失，`detail` 直接给原因，不去调用注定失败的模型；
- **400** —— 上传文件类型不在白名单、为空、或超过 64MB；
- **502** —— 配置齐全但整条链跑完没有有效帧，`detail` 拼出降级说明。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/vision/health` | 非敏感配置摘要 + `available` + 运行时健康（不含密钥值） |
| POST | `/api/v1/vision/analyze` | multipart 图片；`question` 非空时追加一次 VQA |
| POST | `/api/v1/vision/video` | multipart 视频 → 抽帧分析 + 解说 + 告警 |
| GET | `/api/v1/vision/results` | `limit` / `alerts_only`，读 `runs.jsonl` 或告警流水 |
| WS | `/api/v1/vision/stream` | 收 `{"source": 视频路径或图片目录}`，推 `start → frame* → result`（异常推 `error`） |
| GET | `/api/v1/vision/plate/health` | 车牌识别可用性（模型是否已装载、`call_count`、上次耗时、缺依赖原因） |
| POST | `/api/v1/vision/plate` | multipart 车牌图片 → 本地 `core.pipeline.PlateRecognition`，**不调用多模态模型、不产生 token 费用** |

车牌链路与图文解说链路相互独立：`plate_service.py` 惰性装载检测/OCR 管线，`TRAFFIC_PLATE_ENABLED=false`
或依赖缺失时 `/plate` 返回 503 + 原因，`/analyze`、`/video` 不受影响。返回体固定带
`source="core.pipeline.PlateRecognition"` 与两条 `limitations`（整牌准确率未经人工真值集核验、
检测/OCR 置信度不代表识别正确），单张上限 8MB。响应只保留 JSON 友好的字段（`text/conf/det_conf/
is_valid/plate_type/box`），`core.pipeline` 结果里的 `roi` 是 numpy 数组，会被剥掉而不是跟着序列化。

`data` 里固定带 `verified` 与 `degradations`：只要有任何降级（无检测器、无密钥、解码失败、
某帧推理出错），`verified` 就是 `false`，前端与验收都不能把这轮结果当真实结论。

## 课件验收对照

| # | 验收项 | 状态 | 证据 |
| --- | --- | --- | --- |
| 1 | Qwen-VL 模型加载成功 | 远程路径已验证；本机 GPU 路径未验证 | `remote` 端点走真实 httpx 请求栈（`tests/test_vl_analyzer.py` 断言 `/v1/chat/completions`、`Bearer`、消息里 `[text, image_url]` 顺序）；`local-transformers` 代码按课件第 4 页实现，但本机未装 torch、无 GPU，**未实机加载** |
| 2 | 图片描述功能正常 | 通过 | `POST /analyze` 返回 `description`；Mock 端点真解码 base64 图像后作描述，能区分事故帧与非事故帧 |
| 3 | VQA 问答功能正常 | 链路通过，准确性待真实模型 | `answer_question` + `question` 表单字段已测；答案是否「准确」需 `--live` 人工核对 |
| 4 | 视频抽帧功能正常 | 通过 | 40s/10fps 合成片段按 2s 抽出 12 帧，时间戳 `[0,2,…,22]` 与帧号 0→220 逐一对齐；关键帧 12 保留/0 跳过，帧去重 12/12 唯一 |
| 5 | 事故场景检测准确 | 未做准确率声明 | 合成片段第 18–30s 编排了「两车同车道停住并重叠」，Mock 端点真读图后 12 帧里 3 帧被标 `vehicle_collision`，与生成脚本时段吻合。**这是对已知脚本设定的对拍，且由 Mock 完成，不是检测精度指标**；真实准确率需 `--live` + 真实监控片段 |
| 6 | 智能解说生成 | 通过 | `narration.txt` 只喂时间线事实串，要求指明异常首次出现的时刻；缺密钥时不产出正文而是降级标注 |
| 7 | API 接口可用 | 通过 | 上述 4 个 REST + WebSocket，`tests/test_api_server.py` 用 `TestClient` 实测（含 WS 的 `start→frame→result` 序列） |
| 8 | 端到端流程跑通 | 通过（Mock）／真实端点待密钥 | `python backend/scripts/vision_smoke.py` 退出码 0，五段全绿；`--live` 需 `DASHSCOPE_API_KEY` |

## 演示素材与 Mock 验证

`demo_clip.py` 用 PIL + `cv2.VideoWriter` 画一条三车道道路：正常车流 → 18s 起两车在同车道
停住并重叠（同时叠一个只在事故时段出现的橙色警示三角）→ 后车排队。生成幂等，文件已存在则复用。
**画面是程序生成的，不是真实事故影像**，只用于验证抽帧、关键帧筛选与端到端流程。

`backend/scripts/vision_smoke.py` 默认在本机起一个 OpenAI 兼容 Mock 端点（`ThreadingHTTPServer`），
它会**真读**请求里的 base64 图像、按橙色三角占比决定有没有事故，再返回符合 schema 的描述与 JSON 标签。
因此第 4 段证明的是「图像→模型→结构化标签→时间线→解说→告警→REST/WS」这条链可用，
Mock 不是 Qwen-VL，**不能用来宣称任何识别准确率**。想跑真实模型：`--live`。

测试与覆盖率：

```bash
python -m pytest backend/vision/tests -q            # 80 个用例（含 tests/test_plate_service.py 6 个）
python -m coverage run -m pytest backend/vision/tests -q && python -m coverage report --include="backend/vision/*"
```

## 与课件示例代码的差异（有意为之）

1. **不硬编码 `Qwen2VLForConditionalGeneration`**：课件第 4/7/9 页把模型加载写死在
   `VideoAnalysisSystem.__init__(model_path)` 里。本实现把它抽成 `VisionClient`，远程端点作默认路径，
   本机推理作可选路径——没有 GPU 也能完成端到端交付与验收 #2/#4/#6/#7/#8。
2. **`VideoAnalysisSystem.run()` 保留课件签名**（返回 dict），内部转调 `analyze_video()`，
   验收时可直接照课件第 9 页调用。
3. **车辆计数与车牌识别都独立于多模态链路**：课件第 7 页的 YOLOv8 / PaddleOCR 分支需要额外重型依赖，
   这里 `VehicleCounter` 惰性导入、缺失即 `None`；车牌走 `plate_service.py` 复用项目自己的
   `core.pipeline.PlateRecognition`（检测权重 + 透视校正 + PaddleOCR + 号牌校验），只作为单图取证
   接口暴露，**不会把结果写进解说时间线**——识别文本一旦进入提示词就会诱导模型把未核验的号牌当成事实。
4. **事故标签沿用课件第 10 页字段**（`event_type/severity/vehicles/location/lane/action`），
   并加了 `confidence/latency_ms/model/verified`，便于把「模型说的」与「画面里有的」分开核对。
5. **提示词禁止编造**：`narration.txt` 明确不许虚构车牌、伤亡人数、堆积车辆数，
   `alert.txt` 规定结论为「无事故」时必须回 `{"level":"ok"}`。
6. **接口一份实现**：课件第 9 页的 4 个接口全部在 `router.py`，`api_server.py` 只是装配层，
   大屏后端复用同一 router，避免两套逻辑漂移。

## 已知限制

- 车牌取证目前只做到**接口与降级链路验证**：当前 `.venv` 未装 `ultralytics` 与 `paddleocr`，
  `/plate` 会如实返回 503 + `ModuleNotFoundError` 原因，`/plate/health` 返回 `available=false`。
  权重与 OCR 缓存已在仓库内（`models/exp-7.pt`、`data/paddlex_cache`，顶层 `config.py` 与 `core/ocr.py`
  已把模型与缓存路径固定到项目内），装上这两个依赖即可真实推理；但**整牌准确率仍未做过真值集核验**，不得当作指标。
- 本机 transformers 推理路径未实机验证（无 GPU、未装 torch），仅代码与错误提示就绪。
- 不支持 RTSP 直连：`extract_frames` 收的是文件路径或图片目录，实时流需外部先落成片段。
- WebSocket 推流复用单例 `VideoAnalysisSystem` 的订阅回调，多个客户端会收到彼此的帧进度；
  演示够用，多租户需改成按连接分配。
- 上传文件落 `data/vision/uploads`，无清理任务；单次上限 64MB。
- 一次完整视频分析是几十次串行模型调用，非实时；课件第 12 页的批处理推理、结果缓存、
  FP16/INT8 量化未实现（量化只在 `local-transformers` 路径由 `torch_dtype='auto'` 顺带受益）。
- 9/22 的 Flink CEP 告警与本模块尚未打通：当前告警只写本地 JSONL，未回灌 Kafka。
