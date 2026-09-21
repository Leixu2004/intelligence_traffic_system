"""vLLM 推理服务客户端：OpenAI 兼容 `/chat/completions`。

只依赖 httpx，不引入 `openai` SDK —— 与 backend/vision/vl_analyzer.py 保持同一套调用方式，
好处是能直接指向任何一种 OpenAI 兼容端点：vLLM（--served-model-name qwen-7b）、百炼兼容模式、
或本地 Mock 服务（e2e 测试用）。

关于性能数字：本模块测量并返回**真实往返耗时**（latency_ms），但那是当前端点的耗时。
课件第 9 页的「vLLM 响应 1.2s / QPS 62」是 GPU 上的实测样例，本机没有 GPU 与权重，
任何对着 Mock 端点量出来的数字都不能拿去替代它（见 README「性能数字的边界」）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Sequence

import httpx

from .config import IntegrationSettings

SYSTEM_PROMPT = "你是智慧交通领域的分析专家，回答要给出可执行的处置建议，并说明依据。"


class VllmUnavailableError(RuntimeError):
    """端点不可达 / 未配置。上层据此返回 503，而不是伪造一段分析文本。"""


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    latency_ms: int
    endpoint: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class VllmClient:
    def __init__(self, settings: IntegrationSettings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=settings.vllm_base_url,
            timeout=settings.timeout_seconds,
            headers={"Authorization": f"Bearer {settings.vllm_api_key}"},
            limits=httpx.Limits(max_connections=settings.max_connections, max_keepalive_connections=settings.max_connections),
        )

    @property
    def configured(self) -> bool:
        return self.settings.vllm_configured

    def health(self) -> dict[str, Any]:
        """探测 `/models`（vLLM 起来后必定有），失败只报告原因不抛异常。"""
        if not self.configured:
            return {"configured": False, "reachable": False, "reason": "not configured"}
        try:
            response = self._client.get("/models")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return {"configured": True, "reachable": False, "reason": type(exc).__name__}
        data = payload.get("data") if isinstance(payload, dict) else None
        models = [item.get("id", "") for item in data or [] if isinstance(item, dict)]
        return {
            "configured": True,
            "reachable": True,
            "models": models,
            "served_model_expected": self.settings.vllm_model,
            "served_model_present": self.settings.vllm_model in models,
        }

    def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Completion:
        if not self.configured:
            raise VllmUnavailableError("vLLM 端点未配置（TRAFFIC_VLLM_BASE_URL / TRAFFIC_VLLM_MODEL）")
        body = {
            "model": self.settings.vllm_model,
            "messages": list(messages),
            "temperature": self.settings.temperature if temperature is None else temperature,
            "max_tokens": self.settings.max_output_tokens if max_tokens is None else max_tokens,
        }
        started = time.perf_counter()
        attempts = self.settings.max_retries + 1
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                response = self._client.post("/chat/completions", json=body)
                response.raise_for_status()
                payload = response.json()
                break
            except (httpx.HTTPError, ValueError) as exc:  # 连接失败/超时/5xx/非法 JSON 都重试
                last_error = exc
        else:
            raise VllmUnavailableError(f"调用 vLLM 失败（尝试 {attempts} 次）：{last_error}")

        latency_ms = max(1, round((time.perf_counter() - started) * 1000))
        return self._to_completion(payload, latency_ms=latency_ms)

    def analyze(self, prompt: str, *, system: str = SYSTEM_PROMPT) -> Completion:
        return self.chat([{"role": "system", "content": system}, {"role": "user", "content": prompt}])

    @staticmethod
    def _to_completion(payload: Any, *, latency_ms: int) -> Completion:
        if not isinstance(payload, dict):
            raise VllmUnavailableError("vLLM 返回结构无法解析（不是对象）")
        choices = payload.get("choices") or []
        message = choices[0].get("message", {}) if choices and isinstance(choices[0], dict) else {}
        text = (message or {}).get("content") or ""
        if not str(text).strip():
            raise VllmUnavailableError("vLLM 返回空 content，无法作为分析结果")
        usage = payload.get("usage") or {}
        return Completion(
            text=str(text).strip(),
            model=str(payload.get("model") or ""),
            latency_ms=latency_ms,
            endpoint="/chat/completions",
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "VllmClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
