"""Embedding providers: local BGE, OpenAI-compatible API, and a test-only
deterministic hashing embedder used by the software-simulated test profile.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Sequence

import numpy as np

from .config import RagSettings

LOGGER = logging.getLogger(__name__)


class EmbeddingUnavailableError(RuntimeError):
    """Raised when the configured embedding provider cannot be initialised."""


class EmbeddingProvider:
    name = "base"
    model_name = "base"
    dim = 0
    simulation = False

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        raise NotImplementedError

    def encode_query(self, text: str) -> np.ndarray:
        vectors = self.encode([text])
        return vectors[0]


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.clip(norms, 1e-8, None)


class BgeLocalProvider(EmbeddingProvider):
    """Local BGE embeddings via sentence-transformers (production offline path)."""

    def __init__(self, model_name: str, device: str, dim: int):
        self.model_name = model_name
        self.device = device
        self.dim = dim
        self._model = None

    name = "bge-local"

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingUnavailableError(
                "sentence-transformers 未安装，无法使用 BGE 本地 Embedding"
            ) from exc
        try:
            self._model = SentenceTransformer(
                self.model_name, device=self.device, trust_remote_code=True
            )
        except Exception as exc:
            raise EmbeddingUnavailableError(
                f"BGE 模型 {self.model_name} 加载失败: {exc}"
            ) from exc
        return self._model

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype="float32")
        model = self._ensure_model()
        vectors = model.encode(
            list(texts), normalize_embeddings=True, show_progress_bar=False
        )
        array = np.asarray(vectors, dtype="float32")
        if array.shape[1] != self.dim:
            raise EmbeddingUnavailableError(
                f"Embedding 维度 {array.shape[1]} 与配置维度 {self.dim} 不一致，"
                "请核对 TRAFFIC_RAG_EMBEDDING_MODEL / TRAFFIC_RAG_EMBEDDING_DIM 并重建索引"
            )
        return array


class OpenAICompatProvider(EmbeddingProvider):
    """Embeddings through an OpenAI-compatible /embeddings endpoint."""

    name = "openai-compatible"

    def __init__(self, model_name: str, dim: int, api_key: str, base_url: str | None,
                 timeout: float, max_retries: int):
        self.model_name = model_name
        self.dim = dim
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise EmbeddingUnavailableError("openai SDK 未安装") from exc
        if not self.api_key:
            raise EmbeddingUnavailableError("Embedding API Key 未配置")
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )
        return self._client

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype="float32")
        client = self._ensure_client()
        vectors: list[list[float]] = []
        # 阿里云百炼 OpenAI 兼容 embeddings 端点限制单批最多 10 条输入。
        batch_size = 10
        items = list(texts)
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            response = client.embeddings.create(
                model=self.model_name,
                input=batch,
                dimensions=self.dim,
            )
            vectors.extend(item.embedding for item in response.data)
        array = _normalize(np.asarray(vectors, dtype="float32"))
        if array.shape[1] != self.dim:
            raise EmbeddingUnavailableError(
                f"Embedding 维度 {array.shape[1]} 与配置维度 {self.dim} 不一致，请重建索引"
            )
        return array


_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+")


class HashingTestProvider(EmbeddingProvider):
    """Deterministic character n-gram hashing embedder.

    Software-simulated profile ONLY: it enables reproducible end-to-end tests
    without downloading BGE weights. Every response surfaced through this
    provider must keep ``simulation=true`` so it is never mistaken for the
    production embedding path.
    """

    name = "hashing-test"
    simulation = True

    def __init__(self, model_name: str, dim: int):
        self.model_name = model_name
        self.dim = dim

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype="float32")
        matrix = np.zeros((len(texts), self.dim), dtype="float32")
        for row, text in enumerate(texts):
            tokens = _TOKEN_PATTERN.findall(text.lower())
            grams: list[str] = []
            for token in tokens:
                grams.append(token)
                if len(token) > 1:
                    grams.extend(
                        token[i : i + 2] for i in range(len(token) - 1)
                    )
                    grams.extend(
                        token[i : i + 3] for i in range(len(token) - 2)
                    )
            for gram in grams:
                digest = hashlib.md5(gram.encode("utf-8")).digest()
                bucket = int.from_bytes(digest[:4], "little") % self.dim
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                matrix[row, bucket] += sign
        return _normalize(matrix)


def build_embedding_provider(settings: RagSettings) -> EmbeddingProvider:
    if settings.embedding_provider == "hashing-test":
        return HashingTestProvider(settings.embedding_model, settings.embedding_dim)
    if settings.embedding_provider == "openai-compatible":
        return OpenAICompatProvider(
            model_name=settings.embedding_model,
            dim=settings.embedding_dim,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
    return BgeLocalProvider(
        model_name=settings.embedding_model,
        device=settings.embedding_device,
        dim=settings.embedding_dim,
    )


def cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    denom = float(np.linalg.norm(vec1) * np.linalg.norm(vec2))
    if denom <= 0:
        return 0.0
    return float(math.sqrt(max(np.dot(vec1, vec2) ** 2, 0.0)) / denom)
