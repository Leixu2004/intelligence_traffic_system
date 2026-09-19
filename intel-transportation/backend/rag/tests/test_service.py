"""End-to-end RagService tests with the software-simulated embedding profile."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

LAW_SAMPLE = """交通法规知识库

一、超速行驶处罚
超速行驶是引发交通事故的主要原因之一。根据《道路交通安全法》第九十条，在高速公路上行驶超过规定时速20%以上未达50%的，处200元罚款，记6分；超过规定时速50%以上的，处200元以上2000元以下罚款，可以并处吊销机动车驾驶证，记12分。

二、酒驾与醉驾处罚
根据《道路交通安全法》第九十一条，饮酒后驾驶机动车的，处暂扣六个月机动车驾驶证，并处一千元以上二千元以下罚款。醉酒驾驶机动车的，由公安机关交通管理部门约束至酒醒，吊销机动车驾驶证，依法追究刑事责任；五年内不得重新取得机动车驾驶证。

三、闯红灯处罚
根据《道路交通安全法》第九十五条，驾驶机动车违反道路交通信号灯通行的，处警告或者二十元以上二百元以下罚款，记6分。
"""


def _rag_env(tmp: Path) -> dict:
    docs_dir = tmp / "laws"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "traffic_law_kb.txt").write_text(LAW_SAMPLE, encoding="utf-8")
    return {
        "TRAFFIC_RAG_ENABLED": "true",
        "TRAFFIC_RAG_EMBEDDING_PROVIDER": "hashing-test",
        "TRAFFIC_RAG_EMBEDDING_DIM": "256",
        "TRAFFIC_RAG_VECTOR_BACKEND": "auto",
        "TRAFFIC_RAG_MILVUS_URI": str(tmp / "traffic_law.db"),
        "TRAFFIC_RAG_DOCS_DIR": str(docs_dir),
        "TRAFFIC_RAG_REGISTRY_PATH": str(tmp / "law_documents.json"),
        "TRAFFIC_RAG_AUDIT_PATH": str(tmp / "rag_runs.jsonl"),
        "TRAFFIC_RAG_MIN_SCORE": "0.2",
        # 测试不出网：屏蔽 LLM 密钥回退链
        "TRAFFIC_RAG_LLM_API_KEY": "",
        "TRAFFIC_AGENT_API_KEY": "",
        "DASHSCOPE_API_KEY": "",
        "OPENAI_API_KEY": "",
    }


class RagServiceEndToEndTests(unittest.TestCase):
    def setUp(self):
        # Milvus Lite 在 Windows 上关闭后仍短暂持有 WAL 句柄，忽略清理错误。
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self._env = patch.dict(os.environ, _rag_env(Path(self._tmp.name)))
        self._env.start()
        self.addCleanup(self._env.stop)
        from backend.rag import RagService, load_rag_settings

        self.service = RagService(load_rag_settings())
        self.addCleanup(self.service.close)

    def test_disabled_service_reports_unavailable(self):
        with patch.dict(os.environ, {"TRAFFIC_RAG_ENABLED": "false"}):
            from backend.rag import RagService, load_rag_settings

            service = RagService(load_rag_settings())
            self.addCleanup(service.close)
            self.assertFalse(service.available)
            health = service.health()
            self.assertFalse(health["enabled"])
            self.assertFalse(health["available"])
            result = service.search_only("酒驾怎么处罚")
            self.assertFalse(result["ok"])

    def test_ingest_query_citations_and_refusal(self):
        self.assertTrue(self.service.available)
        report = self.service.rebuild()
        self.assertEqual(report["errors"], [])
        self.assertGreater(report["total_chunks"], 0)

        data = self.service.query("酒驾怎么处罚", top_k=3)
        self.assertFalse(data.refusal)
        self.assertEqual(data.generation, "retrieval-only")
        self.assertIn("第九十一条", "".join(c.law_articles for c in data.citations))
        self.assertTrue(data.citations[0].chunk_id)
        self.assertTrue(
            any("hashing-test" in item for item in data.limitations),
            "模拟 Embedding 必须在 limitations 中显式标注",
        )

        off_topic = self.service.query("今天晚上吃什么好")
        self.assertTrue(off_topic.refusal)
        self.assertEqual(off_topic.citations, [])

        health = self.service.health()
        self.assertTrue(health["available"])
        self.assertIn(health["store_backend"], {"milvus-lite", "faiss"})
        self.assertTrue(health["simulation"])
        self.assertEqual(health["documents"][0]["status"], "active")

    def test_idempotent_reingest_and_version_history(self):
        self.service.rebuild()
        again = self.service.rebuild()
        self.assertTrue(again["documents"][0]["unchanged"])

        docs_dir = Path(self.service.settings.docs_dir)
        target = docs_dir / "traffic_law_kb.txt"
        target.write_text(LAW_SAMPLE + "\n四、新增条款\n新增的测试内容。", encoding="utf-8")
        changed = self.service.rebuild()
        self.assertFalse(changed["documents"][0]["unchanged"])
        entry = self.service.registry.get("traffic_law_kb")
        self.assertGreaterEqual(len(entry["history"]), 1)

    def test_search_only_returns_verifiable_citations(self):
        self.service.rebuild()
        result = self.service.search_only("闯红灯记几分", 2)
        self.assertTrue(result["ok"])
        self.assertTrue(result["found"])
        self.assertTrue(result["citations"][0]["law_articles"])
        self.assertTrue(result["citations"][0]["text"])


if __name__ == "__main__":
    unittest.main()
