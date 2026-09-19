# LSTM 交通流训练

训练入口支持轨迹 Excel、轨迹 CSV 和预聚合流量 CSV。不同文件和不同
`series_id` 始终作为独立时序构窗，不会在场景边界拼接。

## 标准聚合数据

推荐字段如下：

```text
series_id,bucket_start_seconds,bin_seconds,vehicle_count,entering_vehicle_count,source_file
```

- `vehicle_count`：时间桶内出现过的唯一车辆数，偏向占用状态。
- `entering_vehicle_count`：车辆首次出现所在桶的数量，更接近流入量。
- 正式训练应让目标列与线上 TimescaleDB 流量事件保持同一计数语义。

## 训练命令

以下命令将每个 CSV 作为独立场景，并显式使用流入量作为目标：

```powershell
.\.venv\Scripts\python.exe -m backend.prediction.train_lstm `
  ..\data\lstm_sources\processed\location5_flow_60s.csv `
  ..\data\lstm_sources\processed\emergency_lane_open_flow_60s.csv `
  ..\data\lstm_sources\processed\location6_ljsdd2_flow_60s.csv `
  ..\data\lstm_sources\processed\location6_ljsdd3_flow_60s.csv `
  --target-column entering_vehicle_count `
  --history-steps 5 `
  --forecast-steps 4 `
  --bin-seconds 60 `
  --output backend\prediction\models\traffic_lstm_candidate.onnx
```

训练器优先将一条完整场景留作验证集。只有一个可用场景时，才按互不重叠的
原始时间段切分训练和验证。Scaler 只使用训练场景或训练时间段拟合。

输出包括 `.pt` checkpoint、ONNX 模型和 `.json` metadata。Metadata 会记录目标列、
场景切分、训练与验证窗口数量，以及被跳过的短序列。短视频数据只适合验证训练链，
不能作为生产模型精度依据。

当前 `traffic_lstm_multiscene_demo_v2` 使用这些短场景生成，仅用于验证多场景训练和
ONNX 服务契约。运行链默认由 `TRAFFIC_MODEL_PATH` 加载下文的中国收费站研究候选；
该默认值用于实训演示，不代表现场生产模型。`location1_trend_v1.onnx` 保留为历史趋势
基线与可选制品。

## 老师四特征教学模型

`teacher_compat` 配置实现老师文本中的完整结构与训练约束：

```text
输入：[batch, 6, 4]
特征：vehicle_count, hour, day_of_week, is_holiday
模型：LSTM 64 -> LSTM 32 -> Dropout(0.2) -> Linear 16 -> Linear 1
切分：全局时间顺序 70% / 15% / 15%
训练：MSE + Adam(weight_decay=1e-5) + ReduceLROnPlateau + Early Stopping
```

使用已经整理的 UCI 数据训练：

```powershell
.\.venv\Scripts\python.exe -m backend.prediction.train_lstm `
  ..\data\lstm_sources\public\uci_metro_interstate\uci_metro_teacher_compat.csv `
  --profile teacher_compat `
  --output backend\prediction\models\traffic_lstm_teacher_compat_v1.onnx
```

训练器仅使用训练时间段拟合 MinMax scaler，先切原始时间轴再分别构窗，并在时间缺口
处拆段。模型旁会生成 `.pt` checkpoint、`.json` metadata、`_scaler.json` 以及
`_artifacts/` 目录；评估目录包含逐轮日志、测试预测、残差、残差分布图和
last-value、moving-average、linear-trend 三类基线。

2026-09-11 的可复现实验在 5,823 个独立测试窗口上得到：MAE 175.25、RMSE 248.84、
WAPE 5.23%、R² 0.9842。该结果只代表美国 I-94 单站点公开数据上的教学基准，不能
直接解释为当前中国道路场景的生产精度，也不参与默认运行模型的自动切换。

## 中国收费站候选模型

`china_kdd2017` 配置使用 KDD Cup 2017 中国高速收费站逐车过站数据。准备脚本会校验
原始文件和公开聚合镜像的 SHA-256，并从原始事件重新计算 20 分钟流量。没有事件的
时间桶可能是零流量，也可能是采集缺失，因此不会擅自补零；loader 会在缺口处分割连续
片段。五个收费站方向组合始终作为独立序列构窗。

```powershell
.\.venv\Scripts\python.exe -m backend.prediction.prepare_kdd2017

.\.venv\Scripts\python.exe -m backend.prediction.train_lstm `
  ..\data\lstm_sources\public\kddcup2017_china_tollgate\kdd2017_china_tollgate_20min.csv `
  --profile china_kdd2017 `
  --output backend\prediction\models\traffic_lstm_china_kdd2017_candidate_v1.onnx
```

模型输入为 `[batch, 6, 6]`，代表过去 2 小时的六个 20 分钟时间桶。特征顺序是
`vehicle_count, hour, minute, day_of_week, is_holiday, is_makeup_workday`，输出为下一
个 20 分钟时间桶的流量。时间按 `Asia/Shanghai` 解释；2016 年国庆节 10 月 1 日至
7 日标记为法定假日，10 月 8 日和 9 日标记为调休工作日。日历有效期之外会明确拒绝
自动构造特征，避免把未知日期静默当成普通周末或工作日。

2026-09-11 的候选训练使用全局时间顺序 70%/15%/15% 切分，得到 6,693 个训练窗口、
1,448 个验证窗口和 1,467 个独立测试窗口。测试指标为 MAE 9.81、RMSE 15.18、WAPE
17.34%、R² 0.8644，优于 last-value 基线的 R² 0.8271；PyTorch 与 ONNX 最大绝对
误差为 `5.96e-08`。

该数据来自匿名中国收费站，可用于课程、实训和非营利研究基准。天池上游协议没有给出
可直接用于商业生产和重新分发的开放许可，因此模型保持 candidate 状态，生产默认模型
不应被解释为现场生产模型。当前项目默认启用它来跑通研究/实训演示链，配套
`data/china_lstm_demo_records.csv` 提供一个独立的 `KDD-T1-D0` 演示卡口。推理日历已经
固定 2026 年国务院放假和调休安排，未知年份继续严格拒绝。正式上线仍需把
`TRAFFIC_RECORDS_PATH` 或 TimescaleDB 切换到有明确生产授权的现场卡口数据，并重新训练、
滚动回测后再晋级模型。

主机直接启动：

```powershell
python -m uvicorn backend.dashboard_api:app --host 127.0.0.1 --port 8000
```

验证模型及卡口预测：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/model/info
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/predict/checkpoint `
  -ContentType application/json `
  -Body '{"checkpoint_id":"KDD-T1-D0","future_steps":1}'
```

Docker Hub 不可达但本机已缓存 TimescaleDB/Kafka 镜像时，可使用隔离端口的开发基础设施：

```powershell
docker compose -f docker-compose.cached.yml up -d
$env:TIMESCALEDB_DSN='host=localhost port=55432 dbname=traffic user=postgres password=postgres'
$env:KAFKA_BROKER='localhost:19092'
```

该文件只提供基础设施，FastAPI、消费者和大屏继续使用项目虚拟环境从主机启动。
