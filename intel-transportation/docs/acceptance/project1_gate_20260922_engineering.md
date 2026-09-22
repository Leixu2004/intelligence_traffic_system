# 项目1自动质量门禁报告

生成时间（UTC）：`2026-09-22T10:10:05.980287+00:00`

运行配置：`engineering`

汇总：PASS 6，FAIL 1，BLOCKED 2，NOT_VERIFIED 1。

| ID | 状态 | 验收项 | 标准 | 证据 |
| --- | --- | --- | --- | --- |
| `ENV-001` | `NOT_VERIFIED` | Python 运行环境 | 技术验收文档要求 Python 3.10.x；工程支持范围为 3.10 至 3.13。 | 当前解释器：Python 3.13.14。 |

`ENV-001` 说明：工程配置支持该版本，但原验收材料指定 Python 3.10.x，仍需在 3.10 环境复跑。
| `ART-001` | `PASS` | 核心源码、SQL、部署配置与模型制品 | PPT 第8页要求源码、SQL、模型、部署与配置文件齐全。 | 已核验 15 个代表性源码、SQL、部署配置和模型文件。 |
| `DOC-001` | `PASS` | 可独立部署的提交文档与验收附件 | README、部署说明、测试报告、截图和性能材料齐全。 | 截图取自 data/demo_shots（20 张 PNG）；所需路径均存在；内容有效性仍应由验收人复核。 |
| `CODE-001` | `PASS` | Python 语法检查 | 全部 Python 源文件可编译。 | 已编译检查 173 个 Python 文件。 |
| `TEST-001` | `FAIL` | 自动化单元测试 | 核心、预测和门禁测试全部通过。 | $ -X utf8 -m unittest discover -s tests -p test_*.py -v<br>...（前文省略 7002 字符）<br>le_count_explicitly) ... ok<br>test_series_holdout_has_no_shared_values_between_splits (test_lstm_training.LSTMTrainingTests.test_series_holdout_has_no_shared_values_between_splits) ... ok<br>test_short_series_is_reported_without_dropping_valid_series (test_lstm_training.LSTMTrainingTests.test_short_series_is_reported_without_dropping_valid_series) ... ok<br>test_vehicle_must_remain_in_zone_for_threshold (test_parking.IllegalParkingTests.test_vehicle_must_remain_in_zone_for_threshold) ... ok<br>test_recognize_rejects_empty_input (test_pipeline.PlatePipelineTests.test_recognize_rejects_empty_input) ... ok<br>test_recognize_runs_full_facade (test_pipeline.PlatePipelineTests.test_recognize_runs_full_facade) ... ok<br>test_routes_all_event_topics_to_separate_tables (test_traffic_consumer.TrafficConsumerTests.test_routes_all_event_topics_to_separate_tables) ... ok<br>test_accepts_complete_plate (test_validator.PlateValidatorTests.test_accepts_complete_plate) ... ok<br>test_rejects_valid_prefix_with_trailing_noise (test_validator.PlateValidatorTests.test_rejects_valid_prefix_with_trailing_noise) ... ok<br><br>----------------------------------------------------------------------<br>Ran 55 tests in 0.267s<br><br>OK (skipped=6)<br><br>$ -X utf8 -m unittest discover -s backend/prediction/tests -t . -p test_*.py -v<br>...（前文省略 12979 字符）<br>  File "C:\Users\lei\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\site-packages\torch\onnx\_internal\torchscript_exporter\utils.py", line 550, in export<br>    _export(<br>    ~~~~~~~^<br>        model,<br>        ^^^^^^<br>    ...<14 lines>...<br>        autograd_inlining=autograd_inlining,<br>        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^<br>    )<br>    ^<br>  File "C:\Users\lei\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\site-packages\torch\onnx\_internal\torchscript_exporter\utils.py", line 1586, in _export<br>    proto = onnx_proto_utils._add_onnxscript_fn(<br>        proto,<br>        custom_opsets,<br>    )<br>  File "C:\Users\lei\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\site-packages\torch\onnx\_internal\torchscript_exporter\onnx_proto_utils.py", line 185, in _add_onnxscript_fn<br>    raise errors.OnnxExporterError("Module onnx is not installed!") from e<br>torch.onnx.OnnxExporterError: Module onnx is not installed!<br><br>----------------------------------------------------------------------<br>Ran 29 tests in 14.248s<br><br>FAILED (errors=4) |
| `CODE-002` | `BLOCKED` | Ruff 静态检查 | PEP 8/Lint 门禁不允许存在阻断问题。 | 当前解释器未安装 ruff；执行 pip install -r requirements-dev.txt 后重跑。 |
| `TEST-002` | `BLOCKED` | 业务代码测试覆盖率 | PPT 与技术验收文档要求覆盖率不低于 80%。 | 当前解释器未安装 coverage；执行 pip install -r requirements-dev.txt 后重跑。 |
| `CODE-003` | `PASS` | 显式待办数量 | PPT 第6页要求 TODO 数量少于 5。 | 发现 0 处 TODO/FIXME。 |
| `DEPLOY-001` | `PASS` | Docker Compose 静态配置 | Compose 文件可解析，服务依赖与变量展开有效。 | docker compose config --quiet 返回退出码 0。 |

`DEPLOY-001` 说明：该检查不启动容器，不能证明 compose up 后服务健康。
| `MODEL-002` | `PASS` | 交通流预测 R² | 独立测试集预测 R² > 0.85。 | 候选模型 R²=0.864412，sample_count=1467，来源 backend\prediction\models\traffic_lstm_china_kdd2017_candidate_v1_artifacts\metrics_summary.json。 |

`MODEL-002` 说明：该证据只覆盖材料所述中国收费站研究候选，不代表现场生产数据精度。

退出码约定：`0` 表示全部检查通过，`1` 表示存在已证实失败，`2` 表示没有已证实失败但仍缺少验收证据。
