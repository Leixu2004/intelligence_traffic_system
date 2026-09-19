# 项目1自动质量门禁报告

生成时间（UTC）：`2026-09-16T06:47:49.626914+00:00`

运行配置：`full`

汇总：PASS 6，FAIL 2，BLOCKED 4，NOT_VERIFIED 4。

| ID | 状态 | 验收项 | 标准 | 证据 |
| --- | --- | --- | --- | --- |
| `ENV-001` | `NOT_VERIFIED` | Python 运行环境 | 技术验收文档要求 Python 3.10.x；工程支持范围为 3.10 至 3.13。 | 当前解释器：Python 3.13.2。 |

`ENV-001` 说明：工程配置支持该版本，但原验收材料指定 Python 3.10.x，仍需在 3.10 环境复跑。
| `ART-001` | `PASS` | 核心源码、SQL、部署配置与模型制品 | PPT 第8页要求源码、SQL、模型、部署与配置文件齐全。 | 已核验 15 个代表性源码、SQL、部署配置和模型文件。 |
| `DOC-001` | `BLOCKED` | 可独立部署的提交文档与验收附件 | PPT 第8、10页要求 README、部署说明、测试报告、截图和性能材料可独立支撑部署与验收。 | 未发现：screenshots |

`DOC-001` 说明：本门禁不会生成或伪造人工截图、性能表或最终验收报告。
| `CODE-001` | `PASS` | Python 语法检查 | 全部 Python 源文件可编译。 | 已编译检查 55 个 Python 文件。 |
| `TEST-001` | `PASS` | 自动化单元测试 | 核心、预测和门禁测试全部通过。 | 三个 unittest 测试集均返回退出码 0。 |

`TEST-001` 说明：...（前文省略 1710 字符）
racerWarning: Converting a tensor to a Python boolean might cause the trace to be incorrect. We can't record the data flow of Python values, so this value will be treated as a constant in the future. This means that the trace might not generalize to other inputs!
  if hx.size() != expected_hidden_size:
D:\intelligent_transportation\intel-transportation\.venv\Lib\site-packages\torch\onnx\_internal\torchscript_exporter\symbolic_opset9.py:4462: UserWarning: Exporting a model to ONNX with a batch_size other than 1, with a variable length with LSTM can cause an error when running the ONNX model with a different batch size. Make sure to save the model with a batch size of 1, or define the initial states (h0/c0) as inputs of the model. 
  return _generic_rnn(
ok

----------------------------------------------------------------------
Ran 37 tests in 2.709s

OK

$ -X utf8 -m unittest discover -s acceptance/tests -t . -p test_*.py -v
test_exit_code_prioritises_failure_over_missing_evidence (acceptance.tests.test_gate.GateModelTests.test_exit_code_prioritises_failure_over_missing_evidence) ... ok
test_prediction_r2_requires_traceable_metrics (acceptance.tests.test_gate.GateModelTests.test_prediction_r2_requires_traceable_metrics) ... ok
test_required_files_reports_missing_artifacts (acceptance.tests.test_gate.GateModelTests.test_required_files_reports_missing_artifacts) ... ok
test_serialization_keeps_explicit_status_and_markdown (acceptance.tests.test_gate.GateModelTests.test_serialization_keeps_explicit_status_and_markdown) ... ok
test_source_tree_does_not_count_as_runtime_evidence (acceptance.tests.test_gate.GateModelTests.test_source_tree_does_not_count_as_runtime_evidence) ... ok

----------------------------------------------------------------------
Ran 5 tests in 0.005s

OK
| `CODE-002` | `FAIL` | Ruff 静态检查 | PEP 8/Lint 门禁不允许存在阻断问题。 | ...（前文省略 22249 字符）<br>edge\export_models.py:3:1<br>  \|<br>1 \|   """Export YOLO perception weights for ONNX Runtime or TensorRT edge inference."""<br>2 \|<br>3 \| / from __future__ import annotations<br>4 \| \|<br>5 \| \| import argparse<br>6 \| \| from pathlib import Path<br>7 \| \| from typing import Any, Iterable<br>  \| \|________________________________^<br>help: Organize imports<br>  \|<br>8 \|<br>  -<br>9 \| SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}<br>  \|<br><br>I001 [*] Import block is un-sorted or un-formatted<br>  --> edge\validate_artifacts.py:9:1<br>   \|<br> 7 \|   """<br> 8 \|<br> 9 \| / from __future__ import annotations<br>10 \| \|<br>11 \| \| import argparse<br>12 \| \| import hashlib<br>13 \| \| import json<br>14 \| \| import sys<br>15 \| \| from datetime import datetime, timezone<br>16 \| \| from pathlib import Path<br>17 \| \| from typing import Any, Iterable, Mapping, Sequence<br>   \| \|___________________________________________________^<br>help: Organize imports<br>   \|<br>18 \|<br>   -<br>19 \| SCHEMA_VERSION = "edge-artifact-report/v1"<br>   \|<br><br>I001 [*] Import block is un-sorted or un-formatted<br>  --> main.py:1:1<br>   \|<br> 1 \| / from __future__ import annotations<br> 2 \| \|<br> 3 \| \| import argparse<br> 4 \| \| from concurrent.futures import Future, ThreadPoolExecutor<br> 5 \| \| import os<br> 6 \| \| from threading import Lock<br> 7 \| \| import time<br> 8 \| \|<br> 9 \| \| import cv2<br>10 \| \|<br>11 \| \| import config<br>12 \| \| from backend.events import PlateRecognitionEvent, TrafficObservation, ViolationEvent, utc_now_iso<br>13 \| \| from core.detector import VehicleDetector<br>14 \| \| from core.pipeline import PlateRecognition<br>15 \| \| from core.violation import IllegalParkingChecker, LineCrossingChecker<br>16 \| \| from db.kafka_client import KafkaClient<br>   \| \|_______________________________________^<br>help: Organize imports<br>  \|<br>3 \| import argparse<br>4 + import os<br>5 + import time<br>6 \| from concurrent.futures import Future, ThreadPoolExecutor<br>  - import os<br>7 \| from threading import Lock<br>  - import time<br>8 \|<br>  \|<br><br>F401 `onnx` imported but unused; consider using `importlib.util.find_spec` to test for availability<br>  --> tests\test_edge_artifact_validation.py:21:20<br>   \|<br>19 \|     def setUpClass(cls):<br>20 \|         try:<br>21 \|             import onnx<br>   \|                    ^^^^<br>22 \|             from onnx import TensorProto, helper<br>23 \|         except ImportError:<br>   \|<br>help: Remove unused import: `onnx`<br><br>I001 [*] Import block is un-sorted or un-formatted<br>  --> tests\test_edge_benchmark.py:1:1<br>   \|<br> 1 \| / import math<br> 2 \| \| import json<br> 3 \| \| import tempfile<br> 4 \| \| import unittest<br> 5 \| \| from pathlib import Path<br> 6 \| \|<br> 7 \| \| import numpy as np<br> 8 \| \|<br> 9 \| \| from edge.benchmark import (<br>10 \| \|     benchmark_session,<br>11 \| \|     build_random_inputs,<br>12 \| \|     normalize_provider_names,<br>13 \| \|     parse_shape_overrides,<br>14 \| \|     resolve_input_shape,<br>15 \| \|     run_benchmark,<br>16 \| \|     summarize_latencies,<br>17 \| \| )<br>   \| \|_^<br>help: Organize imports<br>  \|<br>1 + import json<br>2 \| import math<br>  - import json<br>3 \| import tempfile<br>  \|<br><br>Found 41 errors.<br>[*] 37 fixable with the `--fix` option (2 hidden fixes can be enabled with the `--unsafe-fixes` option). |
| `TEST-002` | `FAIL` | 业务代码测试覆盖率 | 核心、backend、db 与 edge 业务代码覆盖率不低于 80%。 | 业务代码覆盖率 64.53%。 |

`TEST-002` 说明：............................................................................................
----------------------------------------------------------------------
Ran 92 tests in 4.366s

OK
| `CODE-003` | `PASS` | 显式待办数量 | PPT 第6页要求 TODO 数量少于 5。 | 发现 0 处 TODO/FIXME。 |
| `DEPLOY-001` | `PASS` | Docker Compose 静态配置 | Compose 文件可解析，服务依赖与变量展开有效。 | docker compose config --quiet 返回退出码 0。 |

`DEPLOY-001` 说明：该检查不启动容器，不能证明 compose up 后服务健康。
| `MODEL-002` | `PASS` | 交通流预测 R² | 独立测试集预测 R² > 0.85。 | 候选模型 R²=0.864412，sample_count=1467，来源 backend\prediction\models\traffic_lstm_china_kdd2017_candidate_v1_artifacts\metrics_summary.json。 |

`MODEL-002` 说明：该证据只覆盖材料所述中国收费站研究候选，不代表现场生产数据精度。
| `FUNC-001` | `NOT_VERIFIED` | 检测到可视化的真实端到端链路 | 检测→识别→TimescaleDB→预测→可视化端到端无断点。 | 缺少同一次真实运行产生的关联事件、数据库记录、预测响应和大屏截图/录屏。 |

`FUNC-001` 说明：单元测试与源码存在只能证明局部能力，不能替代真实链路证据。
| `MODEL-001` | `BLOCKED` | 车辆检测 mAP | PPT 第10页要求独立标注验证集 mAP > 0.8。 | 未发现包含验证集身份、样本数、类别和 mAP 的可追溯评估报告。 |

`MODEL-001` 说明：PT/ONNX 输出一致性报告不能替代对人工真值的 mAP 评估。
| `MODEL-003` | `BLOCKED` | 整牌识别准确率 | 取两份材料较严格门槛：独立标注集整牌准确率 >= 95%。 | 未发现车牌真值清单及逐样本 OCR 评估结果。 |
| `PERF-001` | `BLOCKED` | 真实全链路延迟 | 采用 PPT 第10页较严格门槛：端到端延迟 < 200ms。 | 现有 edge 报告只测模型 Session.run，不含解码、NMS、OCR、消息、数据库、API 和大屏渲染。 |
| `PERF-002` | `NOT_VERIFIED` | 检测、OCR、数据库、API 与大屏分段性能 | 检测 <20ms、OCR <80ms、DB 读P99 <20ms、写P99 <100ms、API <50ms、大屏首渲 <1s。 | 缺少统一环境、预热策略、样本规模和分位数口径下的分段基准报告。 |
| `DEPLOY-002` | `NOT_VERIFIED` | Docker 一键启动与服务健康 | docker compose up 后 TimescaleDB、Kafka、消费者、预测 API 与大屏均健康。 | 本门禁按任务边界只做静态配置检查，不启动或修改容器。 |

退出码约定：`0` 表示全部检查通过，`1` 表示存在已证实失败，`2` 表示没有已证实失败但仍缺少验收证据。
