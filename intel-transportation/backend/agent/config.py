"""Environment-driven configuration for the traffic assistant."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

INNER_ROOT = Path(__file__).resolve().parents[2]
BAILIAN_PROVIDERS = {"aliyun-bailian", "bailian", "dashscope"}
BAILIAN_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
BAILIAN_DEFAULT_MODEL = "deepseek-v4-flash-0731"


def _flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


def _number(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


@dataclass(frozen=True)
class AgentSettings:
    enabled: bool
    provider: str
    model: str
    api_key: str
    base_url: str | None
    temperature: float
    timeout_seconds: float
    max_retries: int
    max_iterations: int
    audit_path: Path

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.api_key and self.model)


def load_agent_settings() -> AgentSettings:
    provider = (
        os.getenv("TRAFFIC_AGENT_PROVIDER", "openai-compatible").strip().lower()
        or "openai-compatible"
    )
    api_key = os.getenv("TRAFFIC_AGENT_API_KEY", "").strip()
    if not api_key and provider in BAILIAN_PROVIDERS:
        api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
    enabled = _flag("TRAFFIC_AGENT_ENABLED", default=False)
    base_url = os.getenv("TRAFFIC_AGENT_BASE_URL", "").strip() or None
    if base_url is None and provider in BAILIAN_PROVIDERS:
        base_url = BAILIAN_DEFAULT_BASE_URL
    default_model = (
        BAILIAN_DEFAULT_MODEL if provider in BAILIAN_PROVIDERS else "gpt-4o-mini"
    )
    audit_path = Path(
        os.getenv(
            "TRAFFIC_AGENT_AUDIT_PATH",
            str(INNER_ROOT / "data" / "agent" / "agent_runs.jsonl"),
        )
    ).expanduser()
    return AgentSettings(
        enabled=enabled,
        provider=provider,
        model=os.getenv("TRAFFIC_AGENT_MODEL", default_model).strip() or default_model,
        api_key=api_key,
        base_url=base_url,
        temperature=_number("TRAFFIC_AGENT_TEMPERATURE", 0.1, 0.0, 1.0),
        timeout_seconds=_number("TRAFFIC_AGENT_TIMEOUT_SECONDS", 30.0, 3.0, 120.0),
        max_retries=_integer("TRAFFIC_AGENT_MAX_RETRIES", 2, 0, 5),
        max_iterations=_integer("TRAFFIC_AGENT_MAX_ITERATIONS", 6, 1, 12),
        audit_path=audit_path.resolve(),
    )
