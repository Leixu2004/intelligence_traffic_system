# 边缘推理报告

本目录保存可机器读取的 ONNX 校验、性能和行为一致性报告。报告名称包含执行环境与日期；同名模型在不同设备、Provider、输入尺寸或软件版本下的结果必须分别保存，不能覆盖后直接比较。

## 2026-09-16 本机基线

测试环境为 Windows 11、ONNX Runtime 1.29.0、`CPUExecutionProvider`、16 个物理核心和 24 个逻辑核心。模型级 benchmark 使用固定随机输入、`batch=1`、10 次预热和 100 次正式测量。

| 报告 | 主要结论 |
| --- | --- |
| `onnx_artifact_validation_cpu_20260916.json` | 三份 ONNX 全部通过 checker 和 CPU Provider 加载，无回退；感知模型 opset 18，LSTM opset 17，均为 FP32 且未检测到量化节点 |
| `yolov8n_onnx_cpu_benchmark_20260916.json` | `1x3x640x640`，avg 20.098 ms，p50 20.045 ms，p95 21.351 ms，49.756 FPS |
| `exp-7_onnx_cpu_benchmark_20260916.json` | `1x3x640x640`，avg 15.114 ms，p50 15.052 ms，p95 16.808 ms，66.165 FPS |
| `traffic_lstm_china_candidate_onnx_cpu_benchmark_20260916.json` | `1x6x6`，avg 0.059 ms，p50 0.056 ms，p95 0.092 ms；该吞吐不代表视频链路 |
| `vehicle_pt_vs_onnx_cpu_20260916.json` | 1 张真实违章图、10 个参考框，匹配 10/10，平均 IoU 0.999998951 |
| `plate_pt_vs_onnx_cpu_20260916.json` | 184 张真实违章图、`imgsz=1280`；103 张有参考检测，121 个框匹配 121/121，平均 IoU 0.999999516 |

`onnx_artifacts_20260916.json` 位于 `../manifests/`，记录三份 ONNX 的 SHA-256、文件大小、opset 和 I/O 契约，可作为后续制品漂移检查的基线。

## 解释限制

benchmark 的 FPS 是单模型随机张量吞吐，不包括图像读取、颜色转换、letterbox、NMS、车辆到车牌级联、透视矫正、PaddleOCR、Kafka 或数据库。RSS 只记录进程测量前后快照，不是原生峰值内存。报告也没有温度和功耗数据，因此不能用于证明 Jetson 或 Atlas 性能。

行为一致性报告比较 `.pt` 与 `.onnx` 的后 NMS 输出，没有人工标注。`100%` 匹配表示两个后端在现有样本上给出了近似相同的检测结果，不表示检测结果全部正确，也不表示 mAP、车牌召回率或整牌准确率达到生产门槛。

正式边缘验收需要新增目标设备报告，至少包含硬件型号、系统镜像、驱动、运行时、功耗模式、时钟、温度、模型哈希、真实视频端到端 p50/p95、峰值内存、持续运行时长和独立标注验证集精度。
