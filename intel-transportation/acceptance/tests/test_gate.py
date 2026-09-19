from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from acceptance.gate import CheckResult, GateReport, Status, _prediction_r2, _required_files


class GateModelTests(unittest.TestCase):
    def _report(self, *checks: CheckResult) -> GateReport:
        return GateReport("test/v1", "2026-09-16T00:00:00+00:00", "C:/project", "full", checks)

    def test_exit_code_prioritises_failure_over_missing_evidence(self):
        failed = CheckResult("A", "failed", Status.FAIL, "criterion", "evidence")
        blocked = CheckResult("B", "blocked", Status.BLOCKED, "criterion", "evidence")
        self.assertEqual(self._report(failed, blocked).exit_code, 1)
        self.assertEqual(self._report(blocked).exit_code, 2)
        self.assertEqual(
            self._report(CheckResult("C", "pass", Status.PASS, "criterion", "evidence")).exit_code,
            0,
        )

    def test_serialization_keeps_explicit_status_and_markdown(self):
        report = self._report(CheckResult("A", "check", Status.NOT_VERIFIED, "x|y", "missing"))
        self.assertEqual(report.to_dict()["checks"][0]["status"], "NOT_VERIFIED")
        self.assertIn("x\\|y", report.to_markdown())

    def test_required_files_reports_missing_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            result = _required_files(Path(directory))
        self.assertEqual(result.status, Status.FAIL)
        self.assertIn("main.py", result.evidence)

    def test_prediction_r2_requires_traceable_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            result = _prediction_r2(Path(directory))
        self.assertEqual(result.status, Status.BLOCKED)
        self.assertIn("metrics_summary.json", result.evidence)

    def test_source_tree_does_not_count_as_runtime_evidence(self):
        blocked = CheckResult(
            "FUNC-001",
            "end to end",
            Status.NOT_VERIFIED,
            "real chain",
            "source exists but runtime evidence is missing",
        )
        self.assertEqual(self._report(blocked).exit_code, 2)


if __name__ == "__main__":
    unittest.main()
