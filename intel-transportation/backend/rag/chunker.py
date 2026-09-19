"""Knowledge chunking: structured law-article split plus section-aware fallback.

Implements the PROJECT_SPEC.md chunking contract: article boundaries via
``第X条`` regex (spec 5.6), otherwise heading/section aware recursive split with
``chunk_size=500`` / ``chunk_overlap=50`` defaults and cited-article metadata.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

ARTICLE_PATTERN = re.compile(r"第[一二三四五六七八九十百千零]+条(?:之[一二三四五六七八九十]+)?")
ARTICLE_BOUNDARY = re.compile(r"(第[一二三四五六七八九十百千零]+条(?:之[一二三四五六七八九十]+)?\s)")
SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？；;])\s*")
SECTION_HEADING = re.compile(
    r"^(#{1,6}\s+\S.*|[一二三四五六七八九十]+、\S.*|[（(][一二三四五六七八九十]+[)）]\S*.*|第[一二三四五六七八九十百千]+[章节篇]\s*\S.*|\d{1,2}[、.]\s*\S.*)$"
)
MIN_HEADING_LENGTH = 40
ARTICLE_MODE_MIN_ARTICLES = 3
LONG_ARTICLE_THRESHOLD = 800


@dataclass
class KnowledgeChunk:
    text: str
    chunk_index: int
    section: str
    law_articles: list[str] = field(default_factory=list)
    content_hash: str = ""

    def to_metadata(self, doc_id: str, doc_version: str) -> dict[str, str | int]:
        return {
            "doc_id": doc_id,
            "doc_version": doc_version,
            "chunk_index": self.chunk_index,
            "section": self.section[:120],
            "law_articles": "、".join(self.law_articles)[:500],
            "content_hash": self.content_hash,
        }


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_articles(text: str) -> list[str]:
    seen: list[str] = []
    for match in ARTICLE_PATTERN.finditer(text):
        article = match.group(0)
        if article not in seen:
            seen.append(article)
    return seen


def _split_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_title = "前言"
    buffer: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if (
            line
            and len(line) <= MIN_HEADING_LENGTH
            and SECTION_HEADING.match(line)
        ):
            if buffer:
                sections.append((current_title, "\n".join(buffer)))
                buffer = []
            current_title = line
            continue
        buffer.append(raw_line)
    if buffer:
        sections.append((current_title, "\n".join(buffer)))
    return [(title, body.strip()) for title, body in sections if body.strip()]


def _sentence_window_chunks(body: str, chunk_size: int, overlap: int) -> list[str]:
    sentences = [piece for piece in SENTENCE_BOUNDARY.split(body) if piece.strip()]
    if not sentences:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and current_length + len(sentence) > chunk_size:
            chunks.append("".join(current))
            tail: list[str] = []
            tail_length = 0
            for previous in reversed(current):
                if tail_length >= overlap:
                    break
                tail.insert(0, previous)
                tail_length += len(previous)
            current = tail
            current_length = tail_length
        current.append(sentence)
        current_length += len(sentence)
    if current:
        chunks.append("".join(current))
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def _article_mode_chunks(text: str, chunk_size: int, overlap: int) -> list[tuple[str, str]] | None:
    articles = ARTICLE_BOUNDARY.split(text)
    if len(articles) < 3 or len(articles) % 2 == 0:
        return None
    parsed: list[tuple[str, str]] = []
    for index in range(1, len(articles), 2):
        article_id = articles[index].strip()
        content = articles[index + 1].strip() if index + 1 < len(articles) else ""
        if not content:
            continue
        parsed.append((article_id, f"{article_id} {content}"))
    return parsed or None


def chunk_document(
    text: str,
    *,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> list[KnowledgeChunk]:
    """Split knowledge text into chunks with article/section provenance."""

    cleaned = re.sub(r"[ \t\u3000]+", " ", text).strip()
    if not cleaned:
        return []

    article_chunks = _article_mode_chunks(cleaned, chunk_size, chunk_overlap)
    bodies: list[tuple[str, str]] = []
    if article_chunks:
        for article_id, body in article_chunks:
            if len(body) > LONG_ARTICLE_THRESHOLD:
                for piece in _sentence_window_chunks(body, chunk_size, chunk_overlap):
                    bodies.append((article_id, piece))
            else:
                bodies.append((article_id, body))
    else:
        for section_title, body in _split_sections(cleaned):
            if len(body) > chunk_size:
                for piece in _sentence_window_chunks(body, chunk_size, chunk_overlap):
                    bodies.append((section_title, piece))
            else:
                bodies.append((section_title, body))

    chunks: list[KnowledgeChunk] = []
    for index, (section_title, body) in enumerate(bodies):
        body = body.strip()
        if not body:
            continue
        article_hits = extract_articles(body)
        chunks.append(
            KnowledgeChunk(
                text=body,
                chunk_index=index,
                section=section_title,
                law_articles=article_hits,
                content_hash=_content_hash(body),
            )
        )
    return chunks


def chunks_to_payload(chunks: list[KnowledgeChunk], doc_id: str, doc_version: str) -> list[dict]:
    return [
        {
            "chunk": chunk.to_metadata(doc_id, doc_version),
            "text": chunk.text,
        }
        for chunk in chunks
    ]


def report_payload(documents: list[dict]) -> str:
    return json.dumps(documents, ensure_ascii=False, default=str)
