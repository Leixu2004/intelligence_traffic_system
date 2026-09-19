"""Tests for the search_traffic_law agent toolbox integration."""

import json
import unittest

from backend.agent.tools import TrafficToolbox, TrafficToolGateway, finish_tool_trace, start_tool_trace


class SearchTrafficLawToolTests(unittest.TestCase):
    def _toolbox(self, law_search) -> TrafficToolbox:
        return TrafficToolbox(
            TrafficToolGateway(
                traffic_records=lambda checkpoint_id, limit: ([], "TimescaleDB", ""),
                detection_records=lambda checkpoint_id, limit: ([], "TimescaleDB", ""),
                predict_checkpoint=lambda checkpoint_id, steps: {"forecast": []},
                law_search=law_search,
            )
        )

    def test_tool_registered_when_gateway_provided(self):
        toolbox = self._toolbox(lambda question, top_k: {"ok": True, "found": True, "citations": []})
        names = [tool.name for tool in toolbox.as_langchain_tools()]
        self.assertIn("search_traffic_law", names)
        self.assertEqual(len(names), 5)

    def test_tool_result_and_evidence(self):
        def law_search(question, top_k):
            self.assertEqual(question, "酒驾怎么处罚")
            self.assertEqual(top_k, 3)
            return {
                "ok": True,
                "found": True,
                "citations": [
                    {
                        "law_articles": "第九十一条",
                        "doc_title": "交通法规知识库",
                        "doc_version": "v1",
                        "chunk_id": "traffic_law_kb#2",
                        "score": 0.87,
                        "text": "饮酒后驾驶机动车的，处暂扣六个月机动车驾驶证。",
                    }
                ],
                "note": "条文来自版本化法规知识库",
                "source": "LawRAG",
            }

        toolbox = self._toolbox(law_search)
        token = start_tool_trace()
        try:
            payload = json.loads(toolbox.search_traffic_law("酒驾怎么处罚", 3))
        finally:
            evidence = finish_tool_trace(token)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["citations"][0]["law_articles"], "第九十一条")
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].name, "search_traffic_law")
        self.assertIn("第九十一条", evidence[0].summary)

    def test_unavailable_gateway_returns_structured_error(self):
        toolbox = self._toolbox(None)
        token = start_tool_trace()
        try:
            payload = json.loads(toolbox.search_traffic_law("酒驾怎么处罚"))
        finally:
            finish_tool_trace(token)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["source"], "LawRAG")

    def test_blank_question_rejected(self):
        captured = []

        def law_search(question, top_k):
            captured.append(question)
            return {"ok": True, "found": False, "citations": []}

        toolbox = self._toolbox(law_search)
        payload = json.loads(toolbox.search_traffic_law("   "))
        self.assertFalse(payload["ok"])
        self.assertEqual(captured, [])


if __name__ == "__main__":
    unittest.main()
