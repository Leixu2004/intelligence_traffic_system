"""Chunker contract tests (PROJECT_SPEC 5.1 / 5.6)."""

import unittest

from backend.rag.chunker import chunk_document, extract_articles

ARTICLE_TEXT = """第一条 为了维护道路交通秩序，制定本法。
第二条 中华人民共和国境内的车辆驾驶人，都应当遵守本法。
第三条 道路交通安全工作，应当遵循依法管理、方便群众的原则。
第四条 县级以上地方各级人民政府应当保障道路交通安全管理工作与经济建设和社会发展相适应。
"""

SECTION_TEXT = """交通法规知识库

一、常见违法行为
超速行驶是引发交通事故的主要原因之一。根据《道路交通安全法》第九十条，超过规定时速20%以上未达50%的，处200元罚款，记6分。

二、酒驾处罚
根据《道路交通安全法》第九十一条，饮酒后驾驶机动车的，处暂扣六个月机动车驾驶证，并处一千元以上二千元以下罚款。
"""


class ChunkerTests(unittest.TestCase):
    def test_article_mode_keeps_articles_whole(self):
        chunks = chunk_document(ARTICLE_TEXT, chunk_size=500, chunk_overlap=50)
        self.assertGreaterEqual(len(chunks), 4)
        self.assertTrue(chunks[0].text.startswith("第一条"))
        self.assertEqual(chunks[0].law_articles, ["第一条"])
        self.assertEqual(chunks[2].law_articles, ["第三条"])

    def test_extract_articles_supports_zhiyi(self):
        text = "第九十一条之一 违反本规定。参照第九十条执行。"
        self.assertEqual(extract_articles(text), ["第九十一条之一", "第九十条"])

    def test_section_mode_for_prose_documents(self):
        chunks = chunk_document(SECTION_TEXT, chunk_size=500, chunk_overlap=50)
        sections = {chunk.section for chunk in chunks}
        self.assertIn("一、常见违法行为", sections)
        self.assertIn("二、酒驾处罚", sections)
        joined = "\n".join(chunk.text for chunk in chunks)
        self.assertIn("第九十一条", joined)

    def test_long_article_split_avoids_mid_sentence_truncation(self):
        body = "第九十九条 " + "驾驶机动车违反道路交通安全法律的，处二百元罚款。" * 40
        chunks = chunk_document(body, chunk_size=500, chunk_overlap=50)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks[:-1]:
            self.assertTrue(
                chunk.text.rstrip().endswith(("。", "；", "！", "？", ";")),
                f"切分块在句子中间截断: ...{chunk.text[-20:]}",
            )

    def test_chunk_indices_and_content_hash(self):
        chunks = chunk_document(SECTION_TEXT, chunk_size=500, chunk_overlap=50)
        indices = [chunk.chunk_index for chunk in chunks]
        self.assertEqual(indices, list(range(len(chunks))))
        import hashlib

        for chunk in chunks:
            self.assertEqual(
                chunk.content_hash,
                hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
            )

    def test_empty_text_returns_empty(self):
        self.assertEqual(chunk_document("   \n  "), [])


if __name__ == "__main__":
    unittest.main()
