# 边缘模型工具链

`edge/` 负责 ONNX/TensorRT 导出、制品校验、模型级性能基准和导出前后行为一致性检查。完整的制品契约、量化策略、设备路线、性能口径和验收标准见 [边缘推理与 ONNX 部署规范](../docs/deployment/edge_inference.md)，已有报告的适用范围见 [reports/README.md](reports/README.md)。

## 当前能力

| 能力 | 入口 | 当前状态 |
| --- | --- | --- |
| YOLO 导出 | `export_models.py` | 支持 ONNX、TensorRT engine、FP16、INT8、图简化和校准数据参数；参数组合和导出产物已由单元测试覆盖，GPU、TensorRT 和 INT8 校准仍需目标设备验证 |
| ONNX 制品校验 | `validate_artifacts.py` | 已实现 checker、opset、I/O 契约、SHA-256、量化节点和 Execution Provider 校验，并可生成 manifest 与 JSON 报告 |
| ONNX 性能基准 | `benchmark.py` | 已实现预热、正式测量、动态 shape、Provider 防回退、avg/p50/p95/FPS 和环境记录；当前实测仅覆盖本机 CPU 模型调用 |
| 导出行为对比 | `compare_models.py` | 已实现 `.pt` 与 `.onnx/.engine` 的后 NMS 类别、框、IoU 和置信度对比，支持门槛失败退出与显式 `imgsz` |

当前 `yolov8n.onnx`、`models/exp-7.onnx` 和默认中国交通流 LSTM ONNX 已通过 `onnx.checker`、I/O 契约提取和 `CPUExecutionProvider` 加载，未发生 Provider 静默回退。两份感知模型是 opset 18 FP32，LSTM 是 opset 17 FP32，三者均未检测到量化节点。默认感知配置仍使用 `.pt`，可通过 `YOLO_VEHICLE_MODEL_PATH` 和 `YOLO_PLATE_MODEL_PATH` 切换到 Ultralytics 支持的 `.onnx` 或 `.engine`。

仓库仍没有 TensorRT `.engine`、Atlas `.om`、INT8 校准制品和目标边缘设备性能报告。因此，ONNX FP32 属于已实现并完成 CPU 基础验证的能力；TensorRT、FP16、INT8、Jetson 和 Atlas 仍不能描述为部署完成。

## 使用方式

所有命令在项目根目录执行。导出两份 YOLO ONNX：

```powershell
python -m edge.export_models yolov8n.pt models/exp-7.pt --format onnx --imgsz 640 --simplify
```

TensorRT engine 必须在带 NVIDIA GPU 和 TensorRT 的目标环境中构建；INT8 还必须提供代表性交通数据对应的 Ultralytics `dataset.yaml`：

```powershell
python -m edge.export_models models/exp-7.pt --format engine --device 0 --half --imgsz 640
python -m edge.export_models models/exp-7.pt --format engine --device 0 --int8 --calibration-data path\to\dataset.yaml --imgsz 640
```

生成或核验 ONNX manifest，并强制要求实际使用 CPU Provider：

```powershell
python -m edge.validate_artifacts yolov8n.onnx models\exp-7.onnx `
  backend\prediction\models\traffic_lstm_china_kdd2017_candidate_v1.onnx `
  --providers cpu --require-provider `
  --write-manifest edge\manifests\onnx_artifacts_20260916.json `
  --json-output edge\reports\onnx_artifact_validation_cpu_20260916.json --pretty
```

运行模型级 ONNX Runtime 基准。`cpu`、`cuda` 和 `tensorrt` 会展开为对应的 Execution Provider，若请求的 Provider 不可用或发生主 Provider 回退，命令会失败：

```powershell
python -m edge.benchmark yolov8n.onnx --provider cpu --batch-size 1 `
  --warmup 10 --iterations 100 --image-size 640 --seed 20260916 `
  --output edge\reports\yolov8n_onnx_cpu_benchmark_20260916.json
```

比较车牌 PyTorch 与 ONNX 后 NMS 结果。现有违章图为 1440p/4K，`640` 会漏掉小车牌，因此已验证报告使用相同的 `1280` 推理尺寸：

```powershell
python -m edge.compare_models models\exp-7.pt models\exp-7.onnx data\violations `
  --output edge\reports\plate_pt_vs_onnx_cpu_20260916.json `
  --imgsz 1280 --conf 0.25 --match-iou 0.5 `
  --min-reference-match-rate 0.99 --min-mean-iou 0.999 `
  --max-mean-confidence-delta 0.001
```

## 验证边界

当前 CPU benchmark 使用固定随机张量，只测同步 `InferenceSession.run`，不包含图像解码、预处理、NMS、OCR、事件处理、温度、功耗和原生峰值内存。行为对比使用仓库现有图片，但没有人工标注，因此只能证明两个后端在这些样本上的输出高度一致，不能替代 mAP、召回率或整牌识别准确率。

每次准备交付新制品时，应依次执行 manifest 校验、固定样本行为对比、独立标注验证集精度回归和目标设备性能测试。FP16、INT8 和 engine 参数只有在锁定的 Ultralytics、JetPack、CUDA、TensorRT 或 CANN 版本上完成真实导出、加载、精度和持续运行验证后，才能进入项目交付清单。
