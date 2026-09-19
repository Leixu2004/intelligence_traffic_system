from __future__ import annotations

import argparse
import json
import sys
import unittest
from io import StringIO
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="在内存中运行项目测试并计算业务代码覆盖率")
    parser.add_argument("--minimum", type=float, default=80.0)
    args = parser.parse_args()

    try:
        import coverage
    except ImportError:
        print(json.dumps({"error": "coverage is not installed", "percent": None}, ensure_ascii=False))
        return 2

    root = Path(__file__).resolve().parents[1]
    cov = coverage.Coverage(
        branch=True,
        source=["core", "backend", "db", "edge"],
        omit=["*/tests/*", "*/__pycache__/*"],
        data_file=None,
    )
    stream = StringIO()
    cov.start()
    suite = unittest.TestSuite(
        [
            unittest.TestLoader().discover(str(root / "tests"), pattern="test_*.py"),
            unittest.TestLoader().discover(
                str(root / "backend" / "prediction" / "tests"),
                pattern="test_*.py",
                top_level_dir=str(root),
            ),
        ]
    )
    result = unittest.TextTestRunner(stream=stream, verbosity=1, buffer=True).run(suite)
    cov.stop()
    try:
        percent = cov.report(file=StringIO(), skip_empty=True)
    except coverage.exceptions.CoverageException as error:
        print(
            json.dumps(
                {"error": str(error), "percent": None, "test_output": stream.getvalue()},
                ensure_ascii=False,
            )
        )
        return 1
    payload = {
        "percent": round(percent, 4),
        "minimum": args.minimum,
        "tests_successful": result.wasSuccessful(),
        "tests_run": result.testsRun,
        "test_output": stream.getvalue().strip(),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if result.wasSuccessful() and percent >= args.minimum else 1


if __name__ == "__main__":
    sys.exit(main())
