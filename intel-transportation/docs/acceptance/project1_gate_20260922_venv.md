# 项目1自动质量门禁报告

生成时间（UTC）：`2026-09-22T10:08:14.921544+00:00`

运行配置：`engineering`

汇总：PASS 6，FAIL 3，BLOCKED 0，NOT_VERIFIED 1。

| ID | 状态 | 验收项 | 标准 | 证据 |
| --- | --- | --- | --- | --- |
| `ENV-001` | `NOT_VERIFIED` | Python 运行环境 | 技术验收文档要求 Python 3.10.x；工程支持范围为 3.10 至 3.13。 | 当前解释器：Python 3.13.14。 |

`ENV-001` 说明：工程配置支持该版本，但原验收材料指定 Python 3.10.x，仍需在 3.10 环境复跑。
| `ART-001` | `PASS` | 核心源码、SQL、部署配置与模型制品 | PPT 第8页要求源码、SQL、模型、部署与配置文件齐全。 | 已核验 15 个代表性源码、SQL、部署配置和模型文件。 |
| `DOC-001` | `PASS` | 可独立部署的提交文档与验收附件 | README、部署说明、测试报告、截图和性能材料齐全。 | 截图取自 data/demo_shots（20 张 PNG）；所需路径均存在；内容有效性仍应由验收人复核。 |
| `CODE-001` | `PASS` | Python 语法检查 | 全部 Python 源文件可编译。 | 已编译检查 173 个 Python 文件。 |
| `TEST-001` | `FAIL` | 自动化单元测试 | 核心、预测和门禁测试全部通过。 | $ -X utf8 -m unittest discover -s tests -p test_*.py -v<br>...（前文省略 8456 字符）<br><br>Traceback (most recent call last):<br>  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\Lib\unittest\loader.py", line 396, in _find_test_path<br>    module = self._get_module_from_name(name)<br>  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\Lib\unittest\loader.py", line 339, in _get_module_from_name<br>    __import__(name)<br>    ~~~~~~~~~~^^^^^^<br>  File "D:\CODE\TRAFFIC\intelligent_transportation-main\intelligent_transportation-main\intel-transportation\tests\test_pipeline.py", line 5, in <module><br>    from core.pipeline import PlateRecognition<br>  File "D:\CODE\TRAFFIC\intelligent_transportation-main\intelligent_transportation-main\intel-transportation\core\pipeline.py", line 6, in <module><br>    from core.detector import VehicleDetector<br>  File "D:\CODE\TRAFFIC\intelligent_transportation-main\intelligent_transportation-main\intel-transportation\core\detector.py", line 1, in <module><br>    from ultralytics import YOLO<br>ModuleNotFoundError: No module named 'ultralytics'<br><br><br>----------------------------------------------------------------------<br>Ran 46 tests in 0.104s<br><br>FAILED (errors=2, skipped=6) |
| `CODE-002` | `FAIL` | Ruff 静态检查 | PEP 8/Lint 门禁不允许存在阻断问题。 | ...（前文省略 21424 字符）<br>edge\export_models.py:3:1<br>  \|<br>1 \|   """Export YOLO perception weights for ONNX Runtime or TensorRT edge inference."""<br>2 \|<br>3 \| / from __future__ import annotations<br>4 \| \|<br>5 \| \| import argparse<br>6 \| \| from pathlib import Path<br>7 \| \| from typing import Any, Iterable<br>  \| \|________________________________^<br>help: Organize imports<br>  \|<br>8 \|<br>  -<br>9 \| SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}<br>  \|<br><br>I001 [*] Import block is un-sorted or un-formatted<br>  --> edge\validate_artifacts.py:9:1<br>   \|<br> 7 \|   """<br> 8 \|<br> 9 \| / from __future__ import annotations<br>10 \| \|<br>11 \| \| import argparse<br>12 \| \| import hashlib<br>13 \| \| import json<br>14 \| \| import sys<br>15 \| \| from datetime import datetime, timezone<br>16 \| \| from pathlib import Path<br>17 \| \| from typing import Any, Iterable, Mapping, Sequence<br>   \| \|___________________________________________________^<br>help: Organize imports<br>   \|<br>18 \|<br>   -<br>19 \| SCHEMA_VERSION = "edge-artifact-report/v1"<br>   \|<br><br>I001 [*] Import block is un-sorted or un-formatted<br>  --> main.py:1:1<br>   \|<br> 1 \| / from __future__ import annotations<br> 2 \| \|<br> 3 \| \| import argparse<br> 4 \| \| from concurrent.futures import Future, ThreadPoolExecutor<br> 5 \| \| import os<br> 6 \| \| from threading import Lock<br> 7 \| \| import time<br> 8 \| \|<br> 9 \| \| import cv2<br>10 \| \|<br>11 \| \| import config<br>12 \| \| from backend.events import PlateRecognitionEvent, TrafficObservation, ViolationEvent, utc_now_iso<br>13 \| \| from core.detector import VehicleDetector<br>14 \| \| from core.pipeline import PlateRecognition<br>15 \| \| from core.violation import IllegalParkingChecker, LineCrossingChecker<br>16 \| \| from db.kafka_client import KafkaClient<br>   \| \|_______________________________________^<br>help: Organize imports<br>  \|<br>3 \| import argparse<br>4 + import os<br>5 + import time<br>6 \| from concurrent.futures import Future, ThreadPoolExecutor<br>  - import os<br>7 \| from threading import Lock<br>  - import time<br>8 \|<br>  \|<br><br>F401 `onnx` imported but unused; consider using `importlib.util.find_spec` to test for availability<br>  --> tests\test_edge_artifact_validation.py:21:20<br>   \|<br>19 \|     def setUpClass(cls):<br>20 \|         try:<br>21 \|             import onnx<br>   \|                    ^^^^<br>22 \|             from onnx import TensorProto, helper<br>23 \|         except ImportError:<br>   \|<br>help: Remove unused import: `onnx`<br><br>I001 [*] Import block is un-sorted or un-formatted<br>  --> tests\test_edge_benchmark.py:1:1<br>   \|<br> 1 \| / import math<br> 2 \| \| import json<br> 3 \| \| import tempfile<br> 4 \| \| import unittest<br> 5 \| \| from pathlib import Path<br> 6 \| \|<br> 7 \| \| import numpy as np<br> 8 \| \|<br> 9 \| \| from edge.benchmark import (<br>10 \| \|     benchmark_session,<br>11 \| \|     build_random_inputs,<br>12 \| \|     normalize_provider_names,<br>13 \| \|     parse_shape_overrides,<br>14 \| \|     resolve_input_shape,<br>15 \| \|     run_benchmark,<br>16 \| \|     summarize_latencies,<br>17 \| \| )<br>   \| \|_^<br>help: Organize imports<br>  \|<br>1 + import json<br>2 \| import math<br>  - import json<br>3 \| import tempfile<br>  \|<br><br>Found 41 errors.<br>[*] 35 fixable with the `--fix` option (4 hidden fixes can be enabled with the `--unsafe-fixes` option). |
| `TEST-002` | `FAIL` | 业务代码测试覆盖率 | 核心、backend、db 与 edge 业务代码覆盖率不低于 80%。 | 业务代码覆盖率 28.78%。 |

`TEST-002` 说明：....ssss.ss.............................E.E................EE.........E
======================================================================
ERROR: test_lstm_training (unittest.loader._FailedTest.test_lstm_training)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_lstm_training
Traceback (most recent call last):
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\Lib\unittest\loader.py", line 396, in _find_test_path
    module = self._get_module_from_name(name)
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\Lib\unittest\loader.py", line 339, in _get_module_from_name
    __import__(name)
    ~~~~~~~~~~^^^^^^
  File "D:\CODE\TRAFFIC\intelligent_transportation-main\intelligent_transportation-main\intel-transportation\tests\test_lstm_training.py", line 13, in <module>
    from backend.prediction.train_lstm import _parse_args, build_windows, load_flow_series
  File "D:\CODE\TRAFFIC\intelligent_transportation-main\intelligent_transportation-main\intel-transportation\backend\prediction\train_lstm.py", line 34, in <module>
    from .evaluate import (
    ...<4 lines>...
    )
  File "D:\CODE\TRAFFIC\intelligent_transportation-main\intelligent_transportation-main\intel-transportation\backend\prediction\evaluate.py", line 9, in <module>
    import matplotlib
ModuleNotFoundError: No module named 'matplotlib'


======================================================================
ERROR: test_pipeline (unittest.loader._FailedTest.test_pipeline)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_pipeline
Traceback (most recent call last):
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\Lib\unittest\loader.py", line 396, in _find_test_path
    module = self._get_module_from_name(name)
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\Lib\unittest\loader.py", line 339, in _get_module_from_name
    __import__(name)
    ~~~~~~~~~~^^^^^^
  File "D:\CODE\TRAFFIC\intelligent_transportation-main\intelligent_transportation-main\intel-transportation\tests\test_pipeline.py", line 5, in <module>
    from core.pipeline import PlateRecognition
  File "D:\CODE\TRAFFIC\intelligent_transportation-main\intelligent_transportation-main\intel-transportation\core\pipeline.py", line 6, in <module>
    from core.detector import VehicleDetector
  File "D:\CODE\TRAFFIC\intelligent_transportation-main\intelligent_transportation-main\intel-transportation\core\detector.py", line 1, in <module>
    from ultralytics import YOLO
ModuleNotFoundError: No module named 'ultralytics'


======================================================================
ERROR: backend.prediction.tests.test_evaluate (unittest.loader._FailedTest.backend.prediction.tests.test_evaluate)
----------------------------------------------------------------------
ImportError: Failed to import test module: backend.prediction.tests.test_evaluate
Traceback (most recent call last):
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\Lib\unittest\loader.py", line 396, in _find_test_path
    module = self._get_module_from_name(name)
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\Lib\unittest\loader.py", line 339, in _get_module_from_name
    __import__(name)
    ~~~~~~~~~~^^^^^^
  File "D:\CODE\TRAFFIC\intelligent_transportation-main\intelligent_transportation-main\intel-transportation\backend\prediction\tests\test_evaluate.py", line 9, in <module>
    from backend.prediction.evaluate import (
    ...<4 lines>...
    )
  File "D:\CODE\TRAFFIC\intelligent_transportation-main\intelligent_transportation-main\intel-transportation\backend\prediction\evaluate.py", line 9, in <module>
    import matplotlib
ModuleNotFoundError: No module named 'matplotlib'


======================================================================
ERROR: backend.prediction.tests.test_lstm_model (unittest.loader._FailedTest.backend.prediction.tests.test_lstm_model)
----------------------------------------------------------------------
ImportError: Failed to import test module: backend.prediction.tests.test_lstm_model
Traceback (most recent call last):
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\Lib\unittest\loader.py", line 396, in _find_test_path
    module = self._get_module_from_name(name)
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\Lib\unittest\loader.py", line 339, in _get_module_from_name
    __import__(name)
    ~~~~~~~~~~^^^^^^
  File "D:\CODE\TRAFFIC\intelligent_transportation-main\intelligent_transportation-main\intel-transportation\backend\prediction\tests\test_lstm_model.py", line 6, in <module>
    import onnx
ModuleNotFoundError: No module named 'onnx'


======================================================================
ERROR: backend.prediction.tests.test_teacher_training (unittest.loader._FailedTest.backend.prediction.tests.test_teacher_training)
----------------------------------------------------------------------
ImportError: Failed to import test module: backend.prediction.tests.test_teacher_training
Traceback (most recent call last):
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\Lib\unittest\loader.py", line 396, in _find_test_path
    module = self._get_module_from_name(name)
  File "C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\Lib\unittest\loader.py", line 339, in _get_module_from_name
    __import__(name)
    ~~~~~~~~~~^^^^^^
  File "D:\CODE\TRAFFIC\intelligent_transportation-main\intelligent_transportation-main\intel-transportation\backend\prediction\tests\test_teacher_training.py", line 9, in <module>
    from backend.prediction.train_lstm import (
    ...<5 lines>...
    )
  File "D:\CODE\TRAFFIC\intelligent_transportation-main\intelligent_transportation-main\intel-transportation\backend\prediction\train_lstm.py", line 34, in <module>
    from .evaluate import (
    ...<4 lines>...
    )
  File "D:\CODE\TRAFFIC\intelligent_transportation-main\intelligent_transportation-main\intel-transportation\backend\prediction\evaluate.py", line 9, in <module>
    import matplotlib
ModuleNotFoundError: No module named 'matplotlib'


----------------------------------------------------------------------
Ran 71 tests in 0.375s

FAILED (errors=5, skipped=6)
| `CODE-003` | `PASS` | 显式待办数量 | PPT 第6页要求 TODO 数量少于 5。 | 发现 0 处 TODO/FIXME。 |
| `DEPLOY-001` | `PASS` | Docker Compose 静态配置 | Compose 文件可解析，服务依赖与变量展开有效。 | docker compose config --quiet 返回退出码 0。 |

`DEPLOY-001` 说明：该检查不启动容器，不能证明 compose up 后服务健康。
| `MODEL-002` | `PASS` | 交通流预测 R² | 独立测试集预测 R² > 0.85。 | 候选模型 R²=0.864412，sample_count=1467，来源 backend\prediction\models\traffic_lstm_china_kdd2017_candidate_v1_artifacts\metrics_summary.json。 |

`MODEL-002` 说明：该证据只覆盖材料所述中国收费站研究候选，不代表现场生产数据精度。

退出码约定：`0` 表示全部检查通过，`1` 表示存在已证实失败，`2` 表示没有已证实失败但仍缺少验收证据。
