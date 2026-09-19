"""Dependency-free document text extraction for .txt / .md / .docx sources."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_SUPPORTED_SUFFIXES = {".txt", ".md", ".docx"}


class DocumentReadError(RuntimeError):
    """Raised when a knowledge source cannot be read."""


def load_document_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise DocumentReadError(
            f"不支持的文档格式 {suffix!r}（支持 {sorted(_SUPPORTED_SUFFIXES)}；"
            "PDF 请先另存为 txt/md/docx 或安装 pypdf 后扩展本模块）"
        )
    if suffix in {".txt", ".md"}:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="gb18030", errors="replace")
    return _load_docx_text(path)


def _load_docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
    except (KeyError, zipfile.BadZipFile) as exc:
        raise DocumentReadError(f"无法解析 DOCX 文件: {path}") from exc
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{_W_NS}p"):
        pieces = [node.text or "" for node in paragraph.iter(f"{_W_NS}t")]
        line = "".join(pieces).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)
