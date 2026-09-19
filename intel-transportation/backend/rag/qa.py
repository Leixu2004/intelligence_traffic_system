"""RAG answer generation with the mandatory refusal contract (spec 4.3)."""

from __future__ import annotations

import logging
from uuid import uuid4

from .config import RagSettings
from .contracts import RagCitation, RagQueryData
from .retriever import LawRetriever

LOGGER = logging.getLogger(__name__)

REFUSAL_TEXT = (
    "根据现有法规资料，无法回答此问题。知识库中未找到与问题相关的条文，"
    "建议换一种问法或人工核验权威法规原文。"
)
PROMPT_TEMPLATE = """你是智慧交通项目的交通法规问答助手。请严格基于以下法规条文回答用户问题。
若条文未提及答案，请回答"根据现有法规资料，无法回答此问题"。
禁止编造法条、处罚标准或数字。
回答末尾单独一行以"依据："开头，列出所引用的条号与来源文档。
参考条文（格式：[条号｜来源 版本] 条文原文）：
{context}

用户问题：{question}

回答："""


def build_context(hits: list[tuple[RagCitation, str]]) -> str:
    lines = []
    for citation, text in hits:
        lines.append(
            f"[{citation.law_articles or '未标注条号'}｜{citation.doc_title} "
            f"{citation.doc_version}] {text}"
        )
    return "\n\n".join(lines)


class LawQaService:
    def __init__(self, settings: RagSettings, retriever: LawRetriever):
        self.settings = settings
        self.retriever = retriever
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        from openai import OpenAI

        kwargs: dict = {
            "api_key": self.settings.llm_api_key,
            "timeout": self.settings.llm_timeout_seconds,
            "max_retries": self.settings.llm_max_retries,
        }
        if self.settings.llm_base_url:
            kwargs["base_url"] = self.settings.llm_base_url
        self._client = OpenAI(**kwargs)
        return self._client

    def _generate(self, question: str, context: str) -> str:
        client = self._ensure_client()
        response = client.chat.completions.create(
            model=self.settings.llm_model,
            messages=[{"role": "user", "content": PROMPT_TEMPLATE.format(context=context, question=question)}],
            temperature=self.settings.llm_temperature,
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            raise RuntimeError("模型返回空回答")
        return content

    def answer(self, question: str, top_k: int | None = None) -> RagQueryData:
        import time

        started = time.perf_counter()
        trace_id = uuid4().hex
        hits = self.retriever.search(question, top_k)
        store_backend = getattr(self.retriever.store, "backend_name", "unknown")
        limitations = [
            "回答基于知识库检索到的条文，不构成正式法律意见。",
            "知识库版本可能滞后于最新法规修订，重要事项请人工核验。",
        ]
        if self.retriever.provider.simulation:
            limitations.append(
                "当前使用 hashing-test 模拟 Embedding（软件模拟验收口径），"
                "检索质量不代表生产 BGE 模型水平。"
            )
        if not hits:
            return RagQueryData(
                trace_id=trace_id,
                question=question,
                answer=REFUSAL_TEXT,
                refusal=True,
                generation="refusal",
                citations=[],
                embedding_provider=self.retriever.provider.name,
                embedding_model=self.retriever.provider.model_name,
                store_backend=store_backend,
                llm_model=self.settings.llm_model,
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                limitations=limitations,
            )
        context = build_context(hits)
        citations = [citation for citation, _ in hits]
        if not self.settings.llm_configured:
            answer = (
                "（未配置生成模型，以下为检索到的法规原文，未经生成组织）\n\n" + context
            )
            generation = "retrieval-only"
        else:
            try:
                answer = self._generate(question, context)
                generation = "llm"
            except Exception as exc:
                LOGGER.warning("法规问答 LLM 调用失败，降级为纯检索结果: %s", exc)
                answer = (
                    f"（生成模型调用失败：{type(exc).__name__}。以下为检索到的法规原文）\n\n"
                    + context
                )
                generation = "retrieval-only"
        if "无法回答此问题" in answer and generation == "llm":
            refusal = True
        else:
            refusal = False
        return RagQueryData(
            trace_id=trace_id,
            question=question,
            answer=answer,
            refusal=refusal,
            generation=generation,
            citations=citations,
            embedding_provider=self.retriever.provider.name,
            embedding_model=self.retriever.provider.model_name,
            store_backend=store_backend,
            llm_model=self.settings.llm_model,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            limitations=limitations,
        )
