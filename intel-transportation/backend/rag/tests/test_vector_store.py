"""Vector store backend contract tests: Milvus Lite (primary) and FAISS (fallback)."""

import tempfile
import unittest
from pathlib import Path

from backend.rag.vector_store import FaissStore, MilvusLiteStore, Point

try:
    import faiss  # noqa: F401

    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False

try:
    import milvus_lite  # noqa: F401

    MILVUS_AVAILABLE = True
except ImportError:
    MILVUS_AVAILABLE = False

DIM = 32


def _point(doc_id: str, chunk_index: int, vector, text: str = "测试条文") -> Point:
    return Point(
        vector=vector,
        text=text,
        doc_id=doc_id,
        doc_version="v1",
        chunk_index=chunk_index,
        section="测试",
        law_article="第九十一条",
        law_articles="第九十一条",
        content_hash=f"hash-{doc_id}-{chunk_index}",
    )


def _unit(dim: int, index: int):
    vector = [0.0] * dim
    vector[index] = 1.0
    return vector


class StoreContract:
    """Shared assertions for both backends."""

    def make_store(self):
        raise NotImplementedError

    def test_upsert_search_and_count(self):
        store = self.make_store()
        try:
            store.upsert(
                [
                    _point("law_a", 0, _unit(DIM, 0), "酒驾处罚条文"),
                    _point("law_a", 1, _unit(DIM, 1), "超速处罚条文"),
                    _point("law_b", 0, _unit(DIM, 2), "闯红灯处罚条文"),
                ]
            )
            self.assertEqual(store.count(), 3)
            hits = store.search(_unit(DIM, 0), limit=2)
            self.assertEqual(len(hits), 2)
            self.assertEqual(hits[0].metadata["law_article"], "第九十一条")
            self.assertEqual(hits[0].metadata["doc_id"], "law_a")
            self.assertGreater(hits[0].score, 0.9)
        finally:
            store.close()

    def test_delete_document_and_filter(self):
        store = self.make_store()
        try:
            store.upsert(
                [
                    _point("law_a", 0, _unit(DIM, 0)),
                    _point("law_b", 0, _unit(DIM, 2)),
                ]
            )
            unknown = store.search(_unit(DIM, 0), limit=5, allowed_doc_ids=["law_c"])
            self.assertEqual(len(unknown), 0)
            scoped = store.search(_unit(DIM, 0), limit=5, allowed_doc_ids=["law_b"])
            self.assertEqual(len(scoped), 1)
            self.assertEqual(scoped[0].metadata["doc_id"], "law_b")
            self.assertEqual(store.count(), 2)
            store.delete_document("law_a")
            self.assertEqual(store.count(), 1)
            remaining = store.search(_unit(DIM, 2), limit=5)
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0].metadata["doc_id"], "law_b")
        finally:
            store.close()


@unittest.skipUnless(MILVUS_AVAILABLE, "milvus-lite is not installed")
class MilvusLiteStoreTests(StoreContract, unittest.TestCase):
    def make_store(self):
        # Milvus Lite keeps WAL handles open briefly after close() on Windows,
        # so temp-dir cleanup errors are ignored (best-effort removal).
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        return MilvusLiteStore(
            Path(self._tmp.name) / "test.db", "traffic_law_test", DIM
        )


@unittest.skipUnless(FAISS_AVAILABLE, "faiss-cpu is not installed")
class FaissStoreTests(StoreContract, unittest.TestCase):
    def make_store(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        return FaissStore(Path(self._tmp.name), "traffic_law_test", DIM)

    def test_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            store = FaissStore(directory, "traffic_law_test", DIM)
            store.upsert([_point("law_a", 0, _unit(DIM, 0))])
            store.close()
            reopened = FaissStore(directory, "traffic_law_test", DIM)
            try:
                self.assertEqual(reopened.count(), 1)
                hits = reopened.search(_unit(DIM, 0), limit=1)
                self.assertEqual(len(hits), 1)
                self.assertGreater(hits[0].score, 0.9)
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
