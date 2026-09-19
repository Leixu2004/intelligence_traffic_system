"""HTTP client for the optional traffic command assistant."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import requests

DEFAULT_API_URL = os.getenv("DASHBOARD_API_URL", "http://127.0.0.1:8000")
API_KEY = os.getenv("PREDICTION_API_KEY", "").strip()


class AgentClientError(RuntimeError):
    """Stable dashboard-facing error for Agent API failures."""


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY} if API_KEY else {}


@dataclass(frozen=True)
class AgentHealth:
    available: bool
    enabled: bool
    configured: bool
    provider: str = ""
    model: str = ""
    error: str = ""


@dataclass(frozen=True)
class AgentReply:
    answer: str
    trace_id: str
    provider: str
    model: str
    tool_evidence: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    requires_approval: bool = False


def get_agent_health(api_url: str = DEFAULT_API_URL, timeout: float = 3.0) -> AgentHealth:
    try:
        response = requests.get(
            f"{api_url.rstrip('/')}/api/v1/agent/health",
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json().get("data", {})
    except (requests.RequestException, ValueError) as exc:
        raise AgentClientError("无法读取 Agent 健康状态") from exc
    return AgentHealth(
        available=bool(data.get("available")),
        enabled=bool(data.get("enabled")),
        configured=bool(data.get("configured")),
        provider=str(data.get("provider") or ""),
        model=str(data.get("model") or ""),
        error=str(data.get("error") or ""),
    )


def query_assistant(
    *,
    question: str,
    thread_id: str,
    checkpoint_id: str | None = None,
    api_url: str = DEFAULT_API_URL,
    timeout: float = 60.0,
) -> AgentReply:
    payload: dict[str, Any] = {
        "question": question,
        "thread_id": thread_id,
    }
    if checkpoint_id:
        payload["checkpoint_id"] = checkpoint_id
    try:
        response = requests.post(
            f"{api_url.rstrip('/')}/api/v1/assistant/query",
            json=payload,
            headers=_headers(),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise AgentClientError("无法连接交通问答 API") from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        raise AgentClientError(
            str(detail or f"Agent API 返回 HTTP {response.status_code}")
        )
    try:
        data = response.json().get("data", {})
    except ValueError as exc:
        raise AgentClientError("Agent API 返回了无效 JSON") from exc
    return AgentReply(
        answer=str(data.get("answer") or ""),
        trace_id=str(data.get("trace_id") or ""),
        provider=str(data.get("provider") or ""),
        model=str(data.get("model") or ""),
        tool_evidence=list(data.get("tool_evidence") or []),
        limitations=[str(item) for item in data.get("limitations") or []],
        requires_approval=bool(data.get("requires_approval")),
    )
