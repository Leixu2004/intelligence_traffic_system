from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .gate import build_report


def _configure_stdout() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")


def _write(path: str | None, content: str) -> None:
    if not path:
        return
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def main() -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(description="项目1可复现质量门禁")
    parser.add_argument("--profile", choices=("engineering", "full"), default="full")
    parser.add_argument("--timeout", type=int, default=60, help="每个外部命令的超时秒数")
    parser.add_argument("--json-output", help="可选 JSON 报告输出路径")
    parser.add_argument("--markdown-output", help="可选 Markdown 报告输出路径")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    report = build_report(root, profile=args.profile, timeout=args.timeout)
    _write(args.json_output, report.to_json() + "\n")
    _write(args.markdown_output, report.to_markdown())
    print(report.to_markdown())
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
