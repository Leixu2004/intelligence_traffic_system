# -*- coding: utf-8 -*-
"""End-to-end RAG smoke on the real law docx.

Read-only except data/rag artifacts. Never prints secret values.
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Load Bailian key from git-ignored .env into this process only.
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

PROVIDER = os.getenv("SMOKE_EMBEDDING_PROVIDER", "openai-compatible")
if PROVIDER not in {"openai-compatible", "hashing-test"}:
    PROVIDER = "hashing-test"

env_overrides = {
    "TRAFFIC_RAG_ENABLED": "true",
    "TRAFFIC_RAG_EMBEDDING_PROVIDER": PROVIDER,
    "TRAFFIC_RAG_EMBEDDING_DIM": "1024",
    "TRAFFIC_RAG_EMBEDDING_MODEL": (
        "text-embedding-v4" if PROVIDER == "openai-compatible" else "hashing-ngram-test"
    ),
    "TRAFFIC_RAG_VECTOR_BACKEND": "auto",
    "TRAFFIC_RAG_MILVUS_URI": str(ROOT / "data" / "rag" / "traffic_law.db"),
    "TRAFFIC_RAG_DOCS_DIR": str(ROOT / "data" / "rag" / "laws"),
    "TRAFFIC_RAG_REGISTRY_PATH": str(ROOT / "data" / "rag" / "law_documents.json"),
    "TRAFFIC_RAG_AUDIT_PATH": str(ROOT / "data" / "rag" / "rag_runs.jsonl"),
    "TRAFFIC_RAG_TOP_K": "3",
    "TRAFFIC_RAG_MIN_SCORE": "0.3" if PROVIDER == "openai-compatible" else "0.2",
    # 百炼链路（rag config 兜底读取 DASHSCOPE_API_KEY）
    "TRAFFIC_AGENT_PROVIDER": "aliyun-bailian",
    "TRAFFIC_AGENT_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "TRAFFIC_AGENT_MODEL": "deepseek-v4-flash-0731",
    "TRAFFIC_AGENT_API_KEY": "",
    "OPENAI_API_KEY": "",
    "TRAFFIC_RAG_LLM_API_KEY": "",
}
for key, value in env_overrides.items():
    os.environ[key] = value

from backend.rag import RagService, load_rag_settings  # noqa: E402

QUESTIONS = [
    "酒驾怎么处罚？",
    "超速50%以上怎么罚？",
    "电动车需要上牌吗？",
    "刑法对醉驾怎么判？",
]


def main() -> int:
    settings = load_rag_settings()
    service = RagService(settings)
    print(f"[smoke] enabled={service.available} provider={settings.embedding_provider} "
          f"model={settings.embedding_model} llm={settings.llm_model}")
    if not service.available:
        print(f"[smoke] RAG unavailable: {service._load_error}")
        return 2

    started = time.perf_counter()
    rebuild = service.rebuild()
    print(f"[smoke] rebuild: chunks={rebuild['total_chunks']} "
          f"backend={rebuild['store_backend']} errors={rebuild['errors']} "
          f"elapsed={time.perf_counter() - started:.1f}s")
    if rebuild["errors"]:
        return 2

    ok = True
    for question in QUESTIONS:
        data = service.query(question)
        top = data.citations[0] if data.citations else None
        print("-" * 70)
        print(f"Q: {question}")
        print(f"A({data.generation}, refusal={data.refusal}, "
              f"{data.latency_ms:.0f}ms): {data.answer[:220]}")
        if top:
            print(f"   cite: {top.law_articles} | {top.doc_title} {top.doc_version} "
                  f"| {top.chunk_id} | score={top.score}")
        if question.startswith("刑法") and not data.refusal:
            print("   [WARN] 越界问题未拒答")
            ok = False
    health = service.health()
    print("-" * 70)
    print(f"[smoke] health: backend={health['store_backend']} "
          f"chunks={health['chunk_count']} docs={health['document_count']} "
          f"simulation={health['simulation']}")
    tool_result = service.search_only("闯红灯记几分", 2)
    print(f"[smoke] agent tool search_only: ok={tool_result['ok']} "
          f"found={tool_result.get('found')}")
    service.close()
    print(f"[smoke] RESULT: {'PASS' if ok else 'CHECK'} "
          f"(embedding={settings.embedding_provider})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
