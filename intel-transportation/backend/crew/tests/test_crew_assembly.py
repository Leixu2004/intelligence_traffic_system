import unittest
from tempfile import TemporaryDirectory

from backend.crew.contracts import EmergencyEvent

from .support import SAMPLE_EVENT, make_service, make_settings

try:
    from crewai import Process

    from backend.crew.agents import CrewUnavailableError, build_agents, build_llm
    from backend.crew.crew_system import assemble_crew, build_crew_tool_map
    from backend.crew.tasks import build_tasks

    CREWAI = True
except ImportError:  # pragma: no cover - 依赖缺失时整套装配测试跳过
    CREWAI = False

ROLES = {"commander": "应急处置指挥官", "analyst": "交通数据分析师", "dispatcher": "资源调度员"}


@unittest.skipUnless(CREWAI, "crewai 未安装")
class CrewAssemblyTests(unittest.TestCase):
    def test_three_agents_are_assembled_in_courseware_order(self):
        with TemporaryDirectory() as tmp:
            _, crew = self._crew(tmp)
        self.assertEqual([agent.role for agent in crew.agents], [ROLES[key] for key in ("commander", "analyst", "dispatcher")])

    def test_hierarchical_needs_a_manager_llm_and_sequential_does_not(self):
        with TemporaryDirectory() as tmp:
            service, crew = self._crew(tmp)
            self.assertEqual(crew.process, Process.hierarchical)
            self.assertEqual(crew.manager_llm.model, service.settings.model)
            self.assertEqual(crew.manager_llm.base_url, service.settings.base_url)
            sequential = assemble_crew(make_service(tmp, TRAFFIC_CREW_PROCESS="sequential"), SAMPLE_EVENT)
        self.assertEqual(sequential.process, Process.sequential)
        self.assertIsNone(sequential.manager_llm)

    def test_manager_model_override_is_honoured(self):
        with TemporaryDirectory() as tmp:
            _, crew = self._crew(tmp, TRAFFIC_CREW_MANAGER_MODEL="qwen-max")
        self.assertEqual(crew.manager_llm.model, "qwen-max")

    def test_only_the_commander_may_delegate(self):
        with TemporaryDirectory() as tmp:
            _, crew = self._crew(tmp)
        self.assertEqual([agent.allow_delegation for agent in crew.agents], [True, False, False])

    def test_tools_are_bound_per_role(self):
        with TemporaryDirectory() as tmp:
            _, crew = self._crew(tmp)
        bound = {agent.role: {tool.name for tool in agent.tools} for agent in crew.agents}
        self.assertEqual(
            bound[ROLES["commander"]], {"query_checkpoint_flow", "query_peak_period", "publish_public_notice"}
        )
        self.assertEqual(
            bound[ROLES["analyst"]],
            {"query_checkpoint_flow", "query_peak_period", "predict_checkpoint_flow", "search_traffic_law"},
        )
        self.assertEqual(bound[ROLES["dispatcher"]], {"plan_detour_route", "publish_public_notice"})

    def test_event_fields_reach_the_task_descriptions(self):
        with TemporaryDirectory() as tmp:
            service = make_service(tmp)
        event = EmergencyEvent.model_validate({**SAMPLE_EVENT.model_dump(), "title": "隧道内车辆起火", "checkpoint_id": "CP-TUNNEL-09"})
        tasks = build_tasks(self._agents(service, event), service.profile, event)
        joined = "\n".join(task.description for task in tasks)
        self.assertIn("隧道内车辆起火", joined)
        self.assertIn("CP-TUNNEL-09", joined)

    def test_tasks_chain_analysis_then_command_then_dispatch(self):
        with TemporaryDirectory() as tmp:
            service = make_service(tmp)
        analysis, command, dispatch = build_tasks(self._agents(service, SAMPLE_EVENT), service.profile, SAMPLE_EVENT)
        self.assertEqual(
            [task.agent.role for task in (analysis, command, dispatch)],
            [ROLES["analyst"], ROLES["commander"], ROLES["dispatcher"]],
        )
        self.assertEqual([task for task in command.context], [analysis])
        self.assertEqual({id(task) for task in dispatch.context}, {id(command), id(analysis)})
        self.assertTrue(all(task.expected_output for task in (analysis, command, dispatch)))

    def test_llm_carries_endpoint_key_and_timeout(self):
        with TemporaryDirectory() as tmp:
            settings = make_settings(tmp)
        llm = build_llm(settings)
        self.assertEqual(llm.model, "deepseek-v4-flash-0731")
        self.assertEqual(llm.base_url, "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(llm.api_key, "test-key")
        self.assertEqual(llm.timeout, settings.timeout_seconds)

    def test_missing_api_key_raises_before_any_network_call(self):
        with TemporaryDirectory() as tmp:
            settings = make_settings(tmp, TRAFFIC_CREW_LLM_API_KEY="")
        with self.assertRaises(CrewUnavailableError):
            build_llm(settings)

    def _crew(self, tmp, **extra):
        service = make_service(tmp, **extra)
        return service, assemble_crew(service, SAMPLE_EVENT)

    def _agents(self, service, event):
        llm = build_llm(service.settings)
        return build_agents(service.settings, service.profile, build_crew_tool_map(service, event), llm=llm)


if __name__ == "__main__":
    unittest.main()
