# 智慧交通可视化大屏

本目录是当前 Intelligent Transportation 项目的只读展示层。它不启动 YOLO/OCR、Kafka 或数据库写入进程，只读取现有 FastAPI 接口和本地数据资产。当前页面采用宽屏暗色态势大屏布局，包含顶部实时状态、四项 KPI、车流趋势与 ONNX 短时预测、车型分布、路口时段热力图、高德 WebGIS 卡口地图和可展开的识别取证列表。

多页面入口还包含“交通指挥助手”。该页面调用统一 FastAPI 的 `/api/v1/assistant/query`，展示模型回答、只读工具证据和能力限制；Agent 未配置时只显示健康状态，不生成模拟回答。

## 页面清单

| 页面 | 作用 | 依赖接口 |
| --- | --- | --- |
| `1_交通流量大屏.py` | 态势大屏：KPI、趋势与 ONNX 预测、车型分布、热力图、卡口地图 | `/api/traffic_trend`、`/api/v1/predict/*` |
| `2_交通指挥助手.py` | 多轮问答 + 工具证据 + 能力限制 | `/api/v1/agent/health`、`/api/v1/assistant/query` |
| `3_视频智能解说.py` | 图片/视频上传 → 抽帧解说与事故告警、历史结果 | `/api/v1/vision/*` |
| `4_车路云诱导与取证.py` | 诱导问答（含模型端点展示）、路径规划（起终点 + 地图）、车牌取证（上传 + 画框）三个页签 | `/api/v1/assistant/query`、`/api/v1/crew/route/plan`、`/api/v1/vision/plate` |

第 4 页对应 9/22 的《大模型服务化与系统集成》补充材料，用来替代原始 PyQt 单文件 demo：模型端点不再
写死某家厂商，而是显示统一后端当前生效的 provider/model（切 vLLM 只改 `TRAFFIC_AGENT_BASE_URL`）；
车牌取证复用项目自己的 `core.pipeline.PlateRecognition`（推理发生在后端进程，大屏仍不加载 YOLO/OCR 权重），
不再用 `yolov8n` 通用检测权重冒充车牌识别；路线规划复用 Crew 侧高德 v5 `RoutePlanner`，未配置
「Web服务」Key 时返回演示走廊并显式标注「未经实时核验」。**本页只做只读展示与人工核对，不下发控制指令。**

## 运行

```powershell
cd D:\intelligent_transportation
python -m venv .venv-dashboard
.\.venv-dashboard\Scripts\Activate.ps1
python -m pip install -r dashboard\requirements.txt
streamlit run dashboard\app.py --server.port 8501
```

默认打开 `http://127.0.0.1:8501`。

## 数据优先级

1. `DASHBOARD_API_URL` 指向现有 FastAPI `/api/traffic_trend` 时，优先读取 TimescaleDB 聚合数据。
2. API 不可用时读取 `intel-transportation/data/vehicle_records.csv` 或根目录 `vehicle_records.csv`。
3. CSV 不可用时尝试 `intel-transportation/data/traffic_archive.parquet`。
4. 所有外部数据都不可用时，展示固定随机种子的内置示例数据，页面不会白屏。

## 监测区域（当前：广州天河）

地图范围由卡口经纬度决定。`data_loader.py` 在数据规范化阶段把各数据源的卡口重定位到
天河区地标（天河路·体育西路、珠江新城、天河北路、岗顶、棠下、科韵路、华南快速、瘦狗岭、
京溪、龙洞），因此无论数据来自 FastAPI、本地 CSV、Parquet 还是内置示例，地图都落在天河一带。

- 只重映射经纬度，车流量与速度沿用数据源原值。页面副标题已写明「卡口坐标按天河地标演示化落点」，
  不要把这张图当作天河区真实现场采集结果。
- 未登记的卡口 ID 按排序轮转分配地标，同一次运行内位置稳定。
- 关掉区域化、回到数据源原始坐标：`$env:DASHBOARD_MAP_AREA = "source"`。
- 换其他城区：改 `CP_TIANHE_COORDS` / `TIANHE_LANDMARKS` 两张表即可，无需动数据文件。

## 高德地图

未配置 Key 时，页面使用 Streamlit 本地地图展示经纬度点位。应用优先读取本地 `dashboard/.streamlit/secrets.toml`，从项目根目录或 `dashboard` 目录启动都可以读取，也支持通过环境变量注入高德地图 JS API 2.0：

```powershell
$env:AMAP_KEY = "你的 Web 端 JS API Key"
$env:AMAP_SECURITY_CODE = "你的 Security Code"
```

配置 Key 后，地图除自建卡口热力图层外，还会叠加高德官方实时路况图层
（`AMap.TileLayer.Traffic`，60 秒自动刷新），这是页面上唯一反映真实路网拥堵的图层。

生产环境不要把 Key 和 Security Code 写入 Python 源码，并应在高德控制台配置域名白名单。

注意 Key 类型：地图用的是高德「Web端(JS API)」Key，而第 4 页路径规划调 `restapi.amap.com/v5/direction/driving`
要「Web服务」Key。后端按 `AMAP_WEB_SERVICE_KEY` → `AMAP_KEY` 顺序取值，因此只配 JS Key 时路线会退回
演示走廊（或报 `USERKEY_PLAT_NOMATCH`），这不是地图坏了。

## 与现有服务协同

启动统一 FastAPI 预测服务（同时提供大屏数据接口、ONNX 预测接口和 Swagger）：

```powershell
cd D:\intelligent_transportation
.\intel-transportation\.venv\Scripts\python.exe .\intel-transportation\backend\dashboard_api.py
```

再启动 Streamlit。Kafka 消费者和边缘模拟器仍按现有流程独立运行，大屏不会阻塞这些业务进程。

服务启动后可访问：

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/model/info`
- `http://127.0.0.1:8000/docs`
- `POST http://127.0.0.1:8000/api/v1/predict/traffic-flow`

完整联调顺序：先启动 FastAPI，再启动 Streamlit；如果 FastAPI 或 TimescaleDB 暂时不可用，大屏会按本地 CSV、Parquet、内置示例数据顺序降级，页面不会白屏。

```powershell
cd D:\intelligent_transportation
& .\intel-transportation\.venv\Scripts\python.exe -m uvicorn backend.dashboard_api:app --app-dir .\intel-transportation --host 127.0.0.1 --port 8000

& .\.venv-dashboard\Scripts\python.exe -m streamlit run .\dashboard\app.py --server.port 8501 --server.headless true
```

《程序代码.docx》中的 `admin/admin` 登录和硬编码模拟数据属于教学示例，本项目没有将默认弱口令写入源码；当前大屏直接使用已有服务和本地数据资产。若部署到共享环境，再通过外部认证或反向代理增加登录守卫。

首次生成 ONNX 模型：

```powershell
cd D:\intelligent_transportation\intel-transportation
..\.venv-dashboard\Scripts\python.exe -m backend.prediction.export_onnx
```

如果本地未安装 `onnx` 或 `onnxruntime`，服务仍可用 NumPy 兼容推理启动；安装 `backend\requirements.txt` 后会自动切换为 ONNX Runtime。
