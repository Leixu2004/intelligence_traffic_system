from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_VERIFIED = "NOT_VERIFIED"


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    title: str
    status: Status
    criterion: str
    evidence: str
    detail: str = ""
    duration_seconds: float | None = None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class GateReport:
    schema_version: str
    generated_at_utc: str
    project_root: str
    profile: str
    checks: tuple[CheckResult, ...]

    @property
    def counts(self) -> dict[str, int]:
        return {status.value: sum(item.status is status for item in self.checks) for status in Status}

    @property
    def exit_code(self) -> int:
        if any(item.status is Status.FAIL for item in self.checks):
            return 1
        if any(item.status in {Status.BLOCKED, Status.NOT_VERIFIED} for item in self.checks):
            return 2
        return 0

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generated_at_utc": self.generated_at_utc,
            "project_root": self.project_root,
            "profile": self.profile,
            "summary": {"counts": self.counts, "exit_code": self.exit_code},
            "checks": [item.to_dict() for item in self.checks],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        counts = self.counts
        lines = [
            "# 项目1自动质量门禁报告",
            "",
            f"生成时间（UTC）：`{self.generated_at_utc}`",
            "",
            f"运行配置：`{self.profile}`",
            "",
            (
                "汇总："
                f"PASS {counts['PASS']}，FAIL {counts['FAIL']}，"
                f"BLOCKED {counts['BLOCKED']}，NOT_VERIFIED {counts['NOT_VERIFIED']}。"
            ),
            "",
            "| ID | 状态 | 验收项 | 标准 | 证据 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for item in self.checks:
            criterion = _markdown_cell(item.criterion)
            evidence = _markdown_cell(item.evidence)
            lines.append(
                f"| `{item.check_id}` | `{item.status.value}` | {_markdown_cell(item.title)} | "
                f"{criterion} | {evidence} |"
            )
            if item.detail:
                lines.extend(["", f"`{item.check_id}` 说明：{item.detail}"])
        lines.extend(
            [
                "",
                "退出码约定：`0` 表示全部检查通过，`1` 表示存在已证实失败，`2` 表示没有已证实失败但仍缺少验收证据。",
                "",
            ]
        )
        return "\n".join(lines)


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _run(command: list[str], root: Path, timeout: int) -> tuple[int, str, float]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        duration = time.perf_counter() - started
        output = "\n".join(part for part in (error.stdout, error.stderr) if part)
        return 124, f"命令在 {timeout}s 后超时。\n{output}".strip(), duration
    duration = time.perf_counter() - started
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    return completed.returncode, output, duration


def _tail(value: str, limit: int = 3000) -> str:
    if len(value) <= limit:
        return value
    return f"...（前文省略 {len(value) - limit} 字符）\n{value[-limit:]}"


def _required_files(root: Path) -> CheckResult:
    groups = {
        "检测与识别源码": ["main.py", "core/detector.py", "core/pipeline.py", "core/ocr.py"],
        "数据与预测源码": [
            "backend/traffic_consumer.py",
            "backend/dashboard_api.py",
            "backend/prediction/service.py",
        ],
        "SQL与部署": ["backend/sql/init_pipeline.sql", "docker-compose.yml", ".env.example", "requirements.txt"],
        "模型制品": [
            "yolov8n.pt",
            "models/exp-7.pt",
            "models/exp-7.onnx",
            "backend/prediction/models/traffic_lstm_china_kdd2017_candidate_v1.onnx",
        ],
    }
    missing = [f"{group}: {path}" for group, paths in groups.items() for path in paths if not (root / path).is_file()]
    if missing:
        return CheckResult(
            "ART-001",
            "核心源码、SQL、部署配置与模型制品",
            Status.FAIL,
            "PPT 第8页要求源码、SQL、模型、部署与配置文件齐全。",
            "缺失：" + "；".join(missing),
        )
    total = sum(len(paths) for paths in groups.values())
    return CheckResult(
        "ART-001",
        "核心源码、SQL、部署配置与模型制品",
        Status.PASS,
        "PPT 第8页要求源码、SQL、模型、部署与配置文件齐全。",
        f"已核验 {total} 个代表性源码、SQL、部署配置和模型文件。",
    )


def _submission_documents(root: Path) -> CheckResult:
    missing = [
        path
        for path in ("README.md", "test_report.pdf", "performance.xlsx")
        if not (root / path).exists()
    ]
    screenshot_candidates = ("screenshots", "data/demo_shots")
    shots = next(
        (
            directory
            for name in screenshot_candidates
            if (directory := root / name).is_dir() and any(directory.glob("*.png"))
        ),
        None,
    )
    if shots is None:
        missing.append("截图目录（" + " 或 ".join(screenshot_candidates) + "，需含图片）")
    if missing:
        return CheckResult(
            "DOC-001",
            "可独立部署的提交文档与验收附件",
            Status.BLOCKED,
            "PPT 第8、10页要求 README、部署说明、测试报告、截图和性能材料可独立支撑部署与验收。",
            "未发现：" + "、".join(missing),
            "本门禁不会生成或伪造人工截图、性能表或最终验收报告。",
        )
    return CheckResult(
        "DOC-001",
        "可独立部署的提交文档与验收附件",
        Status.PASS,
        "README、部署说明、测试报告、截图和性能材料齐全。",
        f"截图取自 {shots.relative_to(root).as_posix()}（{len(list(shots.glob('*.png')))} 张 PNG）；"
        "所需路径均存在；内容有效性仍应由验收人复核。",
    )


def _python_environment() -> CheckResult:
    version = sys.version_info
    rendered = f"{version.major}.{version.minor}.{version.micro}"
    if (version.major, version.minor) == (3, 10):
        status = Status.PASS
        detail = "与技术验收文档记录的 Python 3.10.x 环境一致。"
    elif version.major == 3 and 10 <= version.minor < 14:
        status = Status.NOT_VERIFIED
        detail = "工程配置支持该版本，但原验收材料指定 Python 3.10.x，仍需在 3.10 环境复跑。"
    else:
        status = Status.FAIL
        detail = "当前解释器超出 pyproject.toml 声明的支持范围。"
    return CheckResult(
        "ENV-001",
        "Python 运行环境",
        status,
        "技术验收文档要求 Python 3.10.x；工程支持范围为 3.10 至 3.13。",
        f"当前解释器：Python {rendered}。",
        detail,
    )


def _syntax_check(root: Path) -> CheckResult:
    source_roots = ("acceptance", "backend", "core", "db", "edge", "tests")
    files = [path for name in source_roots for path in (root / name).rglob("*.py") if "__pycache__" not in path.parts]
    errors: list[str] = []
    for path in files:
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except (OSError, SyntaxError, UnicodeError) as error:
            errors.append(f"{path.relative_to(root)}: {error}")
    status = Status.PASS if not errors else Status.FAIL
    evidence = f"已编译检查 {len(files)} 个 Python 文件。" if not errors else "；".join(errors)
    return CheckResult("CODE-001", "Python 语法检查", status, "全部 Python 源文件可编译。", evidence)


def _unit_tests(root: Path, timeout: int) -> CheckResult:
    commands = [
        [sys.executable, "-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
        [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "unittest",
            "discover",
            "-s",
            "backend/prediction/tests",
            "-t",
            ".",
            "-p",
            "test_*.py",
            "-v",
        ],
        [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "unittest",
            "discover",
            "-s",
            "acceptance/tests",
            "-t",
            ".",
            "-p",
            "test_*.py",
            "-v",
        ],
    ]
    outputs: list[str] = []
    duration = 0.0
    for command in commands:
        code, output, elapsed = _run(command, root, timeout)
        duration += elapsed
        outputs.append(f"$ {' '.join(command[1:])}\n{_tail(output, 1200)}")
        if code != 0:
            return CheckResult(
                "TEST-001",
                "自动化单元测试",
                Status.FAIL,
                "核心、预测和门禁测试全部通过。",
                _tail("\n\n".join(outputs)),
                duration_seconds=duration,
            )
    return CheckResult(
        "TEST-001",
        "自动化单元测试",
        Status.PASS,
        "核心、预测和门禁测试全部通过。",
        "三个 unittest 测试集均返回退出码 0。",
        detail=_tail("\n\n".join(outputs), 1800),
        duration_seconds=duration,
    )


def _ruff_check(root: Path, timeout: int) -> CheckResult:
    if not _module_available("ruff"):
        return CheckResult(
            "CODE-002",
            "Ruff 静态检查",
            Status.BLOCKED,
            "PEP 8/Lint 门禁不允许存在阻断问题。",
            "当前解释器未安装 ruff；执行 pip install -r requirements-dev.txt 后重跑。",
        )
    code, output, duration = _run([sys.executable, "-m", "ruff", "check", "."], root, timeout)
    return CheckResult(
        "CODE-002",
        "Ruff 静态检查",
        Status.PASS if code == 0 else Status.FAIL,
        "PEP 8/Lint 门禁不允许存在阻断问题。",
        "ruff check . 返回退出码 0。" if code == 0 else _tail(output),
        duration_seconds=duration,
    )


def _coverage_check(root: Path, timeout: int) -> CheckResult:
    if not _module_available("coverage"):
        return CheckResult(
            "TEST-002",
            "业务代码测试覆盖率",
            Status.BLOCKED,
            "PPT 与技术验收文档要求覆盖率不低于 80%。",
            "当前解释器未安装 coverage；执行 pip install -r requirements-dev.txt 后重跑。",
        )
    code, output, duration = _run(
        [sys.executable, "-X", "utf8", "-m", "acceptance.coverage_gate", "--minimum", "80"],
        root,
        timeout,
    )
    try:
        payload = json.loads(output.splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        payload = {"percent": None, "output": _tail(output)}
    percent = payload.get("percent")
    if code == 0:
        status = Status.PASS
    else:
        status = Status.FAIL
    evidence = f"业务代码覆盖率 {percent:.2f}%。" if isinstance(percent, (int, float)) else _tail(output)
    return CheckResult(
        "TEST-002",
        "业务代码测试覆盖率",
        status,
        "核心、backend、db 与 edge 业务代码覆盖率不低于 80%。",
        evidence,
        detail=str(payload.get("test_output", "")),
        duration_seconds=duration,
    )


def _todo_check(root: Path) -> CheckResult:
    pattern = re.compile(r"\b(?:TODO|FIXME)\b", re.IGNORECASE)
    hits: list[str] = []
    for base in ("backend", "core", "db", "edge"):
        for path in (root / base).rglob("*.py"):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{path.relative_to(root)}:{line_number}")
    status = Status.PASS if len(hits) < 5 else Status.FAIL
    evidence = f"发现 {len(hits)} 处 TODO/FIXME。" + (f" 位置：{', '.join(hits)}" if hits else "")
    return CheckResult("CODE-003", "显式待办数量", status, "PPT 第6页要求 TODO 数量少于 5。", evidence)


def _compose_config(root: Path, timeout: int) -> CheckResult:
    code, output, duration = _run(["docker", "compose", "config", "--quiet"], root, timeout)
    return CheckResult(
        "DEPLOY-001",
        "Docker Compose 静态配置",
        Status.PASS if code == 0 else Status.FAIL,
        "Compose 文件可解析，服务依赖与变量展开有效。",
        "docker compose config --quiet 返回退出码 0。" if code == 0 else _tail(output),
        detail="该检查不启动容器，不能证明 compose up 后服务健康。",
        duration_seconds=duration,
    )


def _prediction_r2(root: Path) -> CheckResult:
    metrics_path = root / (
        "backend/prediction/models/traffic_lstm_china_kdd2017_candidate_v1_artifacts/metrics_summary.json"
    )
    if not metrics_path.is_file():
        return CheckResult(
            "MODEL-002",
            "交通流预测 R²",
            Status.BLOCKED,
            "PPT 第10页要求预测 R² > 0.85。",
            f"缺少可解析指标文件：{metrics_path.relative_to(root)}。",
        )
    try:
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        r2 = float(payload["model"]["r2"])
        sample_count = int(payload["model"]["sample_count"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return CheckResult(
            "MODEL-002",
            "交通流预测 R²",
            Status.FAIL,
            "PPT 第10页要求预测 R² > 0.85。",
            f"指标文件不可用：{error}",
        )
    status = Status.PASS if r2 > 0.85 else Status.FAIL
    return CheckResult(
        "MODEL-002",
        "交通流预测 R²",
        status,
        "独立测试集预测 R² > 0.85。",
        f"候选模型 R²={r2:.6f}，sample_count={sample_count}，来源 {metrics_path.relative_to(root)}。",
        "该证据只覆盖材料所述中国收费站研究候选，不代表现场生产数据精度。",
    )


def _external_checks() -> Iterable[CheckResult]:
    yield CheckResult(
        "FUNC-001",
        "检测到可视化的真实端到端链路",
        Status.NOT_VERIFIED,
        "检测→识别→TimescaleDB→预测→可视化端到端无断点。",
        "缺少同一次真实运行产生的关联事件、数据库记录、预测响应和大屏截图/录屏。",
        "单元测试与源码存在只能证明局部能力，不能替代真实链路证据。",
    )
    yield CheckResult(
        "MODEL-001",
        "车辆检测 mAP",
        Status.BLOCKED,
        "PPT 第10页要求独立标注验证集 mAP > 0.8。",
        "未发现包含验证集身份、样本数、类别和 mAP 的可追溯评估报告。",
        "PT/ONNX 输出一致性报告不能替代对人工真值的 mAP 评估。",
    )
    yield CheckResult(
        "MODEL-003",
        "整牌识别准确率",
        Status.BLOCKED,
        "取两份材料较严格门槛：独立标注集整牌准确率 >= 95%。",
        "未发现车牌真值清单及逐样本 OCR 评估结果。",
    )
    yield CheckResult(
        "PERF-001",
        "真实全链路延迟",
        Status.BLOCKED,
        "采用 PPT 第10页较严格门槛：端到端延迟 < 200ms。",
        "现有 edge 报告只测模型 Session.run，不含解码、NMS、OCR、消息、数据库、API 和大屏渲染。",
    )
    yield CheckResult(
        "PERF-002",
        "检测、OCR、数据库、API 与大屏分段性能",
        Status.NOT_VERIFIED,
        "检测 <20ms、OCR <80ms、DB 读P99 <20ms、写P99 <100ms、API <50ms、大屏首渲 <1s。",
        "缺少统一环境、预热策略、样本规模和分位数口径下的分段基准报告。",
    )
    yield CheckResult(
        "DEPLOY-002",
        "Docker 一键启动与服务健康",
        Status.NOT_VERIFIED,
        "docker compose up 后 TimescaleDB、Kafka、消费者、预测 API 与大屏均健康。",
        "本门禁按任务边界只做静态配置检查，不启动或修改容器。",
    )


def build_report(root: Path, profile: str = "full", timeout: int = 60) -> GateReport:
    root = root.resolve()
    checks: list[CheckResult] = [
        _python_environment(),
        _required_files(root),
        _submission_documents(root),
        _syntax_check(root),
        _unit_tests(root, timeout),
        _ruff_check(root, timeout),
        _coverage_check(root, timeout),
        _todo_check(root),
        _compose_config(root, timeout),
        _prediction_r2(root),
    ]
    if profile == "full":
        checks.extend(_external_checks())
    elif profile != "engineering":
        raise ValueError(f"未知验收配置：{profile}")
    return GateReport(
        schema_version="project1-quality-gate/v1",
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        project_root=str(root),
        profile=profile,
        checks=tuple(checks),
    )
