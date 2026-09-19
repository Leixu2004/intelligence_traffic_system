# 项目1质量门禁运行说明

## 目标

`acceptance` 包将项目1的工程检查、制品检查和外部证据缺口汇总为统一报告。它只使用当前工作区与实际命令输出，不沿用参考文档中的“已通过”结论，不启动容器，也不生成模拟端到端证据。

## 环境准备

运行环境需要 Python 3.10 至 3.13。原技术验收材料记录的是 Python 3.10.x，因此正式复验仍应保留一次 Python 3.10 环境运行记录。业务依赖与开发依赖分别安装：

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

开发依赖包含 Ruff、Coverage.py、pytest、pre-commit 和 pip-audit。当前门禁直接使用 `unittest`，因此即使没有 pytest 也可以运行单元测试；Ruff 或 Coverage.py 缺失时，对应检查会返回 `BLOCKED`。

## 执行方式

工程门禁运行语法、单元测试、Ruff、覆盖率、TODO 数量、Compose 静态配置、核心制品和预测 R²检查：

```powershell
python -m acceptance.run --profile engineering
```

完整验收配置还会列出必须由真实运行、标注数据集或人工证据完成的项目：

```powershell
python -m acceptance.run --profile full
```

报告可以同时写为 JSON 和 Markdown，路径由调用方显式提供：

```powershell
python -m acceptance.run --profile full `
  --json-output artifacts\acceptance\project1.json `
  --markdown-output artifacts\acceptance\project1.md
```

门禁不会默认写入最终验收报告。输出中的 `PASS`、`FAIL`、`BLOCKED` 和 `NOT_VERIFIED` 应按 `docs/acceptance/REQUIREMENTS.md` 的定义解释。退出码分别为全部通过 `0`、存在失败 `1`、仍缺证据 `2`。

## 提交前检查

安装开发依赖后启用本地预提交钩子：

```powershell
python -m pre_commit install
python -m pre_commit run --all-files
```

预提交钩子只运行 Ruff、Ruff 格式检查和门禁自身测试。完整业务测试和验收报告应在提交前另行执行，避免把耗时或需要外部环境的步骤隐藏在单次 Git 操作中。

## 外部证据补齐

车辆检测 mAP 需要锁定的人工标注验证集和可追溯评估输出；车牌准确率需要逐样本真值与整牌匹配结果；端到端和分段性能需要同一环境下的正式基准；Compose 运行验收需要容器健康状态与接口探测记录。缺少任一类证据时，保留门禁给出的未验证状态，并在独立验收材料中填写测试环境、命令、原始输出路径、指标和复核人。

硬件不可用时可以使用 CPU ONNX、录制视频或离线事件验证软件层行为。报告应把硬件目标和软件降级结果分别列示，不将后者升级为 TensorRT、INT8、Jetson、Atlas 或真实摄像头通过结论。
