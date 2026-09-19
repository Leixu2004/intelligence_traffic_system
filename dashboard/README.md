# 智慧交通可视化大屏

本目录是当前 Intelligent Transportation 项目的只读展示层。它不启动 YOLO/OCR、Kafka 或数据库写入进程，只读取现有 FastAPI 接口和本地数据资产。当前页面采用宽屏暗色态势大屏布局，包含顶部实时状态、四项 KPI、车流趋势与 ONNX 短时预测、车型分布、路口时段热力图、高德 WebGIS 卡口地图和可展开的识别取证列表。

多页面入口还包含“交通指挥助手”。该页面调用统一 FastAPI 的 `/api/v1/assistant/query`，展示模型回答、只读工具证据和能力限制；Agent 未配置时只显示健康状态，不生成模拟回答。

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

## 高德地图

未配置 Key 时，页面使用 Streamlit 本地地图展示经纬度点位。应用优先读取本地 `dashboard/.streamlit/secrets.toml`，从项目根目录或 `dashboard` 目录启动都可以读取，也支持通过环境变量注入高德地图 JS API 2.0：

```powershell
$env:AMAP_KEY = "你的 Web 端 JS API Key"
$env:AMAP_SECURITY_CODE = "你的 Security Code"
```

生产环境不要把 Key 和 Security Code 写入 Python 源码，并应在高德控制台配置域名白名单。

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
