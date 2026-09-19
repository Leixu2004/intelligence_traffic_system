"""run_crew 与工具取证链路：不触发真实模型调用，只验证协作结果的装配。"""

import json
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.crew.crew_system import SAMPLE_EVENT as CLI_SAMPLE_EVENT
from backend.crew.crew_system import run_crew
from backend.crew.prompts import LIMITATIONS

from .support import SAMPLE_EVENT, make_service


class _FakeOutput:
    raw = "一、事件等级 III 级\n二、请调度拖车 2 台\n（数据来源见工具取证）"


class _FakeCrew:
    def __init__(self, service, fail=False):
        self.service = service
        self.fail = fail

    def kickoff(self):
        # 协作过程中由 Agent 触发真实工具，用来验证取证链路是否记录。
        self.service.toolbox.query_checkpoint_flow("CP-NORTH-01", 5)
        if self.fail:
            raise RuntimeError("模型不可用")
        return _FakeOutput()


class RunCrewTests(unittest.TestCase):
    def test_report_and_tool_evidence_are_returned_together(self):
        with TemporaryDirectory() as tmp:
            service = make_service(tmp)
            with patch("backend.crew.crew_system.assemble_crew", return_value=_FakeCrew(service)):
                result = run_crew(SAMPLE_EVENT, service=service)
        self.assertTrue(result.ok)
        self.assertEqual(result.process, "hierarchical")
        self.assertIn("事件等级", result.report)
        self.assertEqual([item["name"] for item in result.tool_evidence], ["query_checkpoint_flow"])
        self.assertTrue(result.tool_evidence[0]["ok"])
        self.assertEqual(result.tool_evidence[0]["source"], "TimescaleDB")
        self.assertGreater(result.elapsed_ms, 0)
        # 模型输出 + 模拟通告，任何情况下都不能被当成真实处置结果。
        self.assertTrue(result.simulation)
        self.assertEqual(result.limitations, LIMITATIONS)

    def test_trace_is_closed_even_when_the_crew_raises(self):
        from backend.agent.tools import _TRACE

        with TemporaryDirectory() as tmp:
            service = make_service(tmp)
            with patch("backend.crew.crew_system.assemble_crew", return_value=_FakeCrew(service, fail=True)):
                with self.assertRaises(RuntimeError):
                    run_crew(SAMPLE_EVENT, service=service)
        self.assertIsNone(_TRACE.get())

    def test_cli_uses_the_sample_event_when_no_json_is_given(self):
        from backend.crew.contracts import EmergencyEvent

        event = EmergencyEvent.model_validate(CLI_SAMPLE_EVENT)
        self.assertEqual(event.title, "高速 K128 处多车追尾")
        self.assertEqual(event.lanes_blocked, 2)

    def test_cli_degrades_cleanly_when_crew_is_disabled(self):
        from backend.crew.crew_system import main

        with TemporaryDirectory() as tmp:
            output = tmp + "/result.json"
            service = make_service(tmp, TRAFFIC_CREW_ENABLED="false")
            with patch("backend.crew.crew_system._service_for_cli", return_value=service), patch.dict(
                "os.environ", {}, clear=True
            ):
                code = main(["--output", output])
            with open(output, encoding="utf-8") as handle:
                payload = json.loads(handle.read())
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["simulation"])


if __name__ == "__main__":
    unittest.main()
