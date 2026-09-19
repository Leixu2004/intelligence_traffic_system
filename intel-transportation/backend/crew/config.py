"""Crew 配置装配：环境变量优先，其次 backend/crew/config.yaml，最后内置默认值。

密钥（LLM / 高德）只从环境变量读取，绝不写进配置文件，避免随仓库外泄。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..agent.config import BAILIAN_DEFAULT_BASE_URL, BAILIAN_PROVIDERS, INNER_ROOT

BAILIAN_DEFAULT_MODEL = "deepseek-v4-flash-0731"
VALID_PROCESSES = frozenset({"hierarchical", "sequential"})
VALID_ROUTE_MODES = frozenset({"amap", "static", "auto"})
TRUTHY = {"1", "true", "yes", "on"}
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

# config.yaml 中允许被环境变量覆盖的标量项：环境变量名 -> (段落, 键)。
YAML_KEYS: dict[str, tuple[str, str]] = {
    "TRAFFIC_CREW_PROVIDER": ("llm", "provider"),
    "TRAFFIC_CREW_LLM_MODEL": ("llm", "model"),
    "TRAFFIC_CREW_LLM_BASE_URL": ("llm", "base_url"),
    "TRAFFIC_CREW_TEMPERATURE": ("llm", "temperature"),
    "TRAFFIC_CREW_TIMEOUT_SECONDS": ("llm", "timeout_seconds"),
    "TRAFFIC_CREW_MAX_RETRIES": ("llm", "max_retries"),
    "TRAFFIC_CREW_MANAGER_MODEL": ("llm", "manager_model"),
    "TRAFFIC_CREW_PROCESS": ("crew", "process"),
    "TRAFFIC_CREW_VERBOSE": ("crew", "verbose"),
    "TRAFFIC_CREW_MEMORY": ("crew", "memory"),
}

_NO_FALLBACK: dict[str, Any] = {}


def _raw(name: str, fallbacks: dict[str, Any]) -> str | None:
    value = os.getenv(name)
    if value is not None and value.strip():
        return value.strip()
    alternate = fallbacks.get(name)
    return None if alternate is None or alternate == "" else str(alternate)


def _flag(name: str, default: bool = False, fallbacks: dict[str, Any] = _NO_FALLBACK) -> bool:
    value = _raw(name, fallbacks)
    return default if value is None else value.lower() in TRUTHY


def _integer(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
    fallbacks: dict[str, Any] = _NO_FALLBACK,
) -> int:
    raw = _raw(name, fallbacks)
    try:
        value = int(default if raw is None else raw)
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


def _number(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
    fallbacks: dict[str, Any] = _NO_FALLBACK,
) -> float:
    raw = _raw(name, fallbacks)
    try:
        value = float(default if raw is None else raw)
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


def _text(name: str, default: str, fallbacks: dict[str, Any] = _NO_FALLBACK) -> str:
    return _raw(name, fallbacks) or default


def _choice(
    name: str,
    default: str,
    allowed: frozenset[str],
    fallbacks: dict[str, Any] = _NO_FALLBACK,
) -> str:
    value = _text(name, default, fallbacks).strip().lower()
    return value if value in allowed else default


def _path(name: str, default: Path) -> Path:
    return Path(os.getenv(name) or str(default)).expanduser()


def load_yaml_fallbacks(config_path: Path) -> dict[str, Any]:
    """摊平 config.yaml 的可覆盖标量项。文件缺失或解析失败时返回空字典，不阻断启动。"""
    try:
        import yaml
    except ImportError:
        return {}
    try:
        document = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}
    if not isinstance(document, dict):
        return {}
    fallbacks: dict[str, Any] = {}
    for env_name, (section, key) in YAML_KEYS.items():
        block = document.get(section)
        if not isinstance(block, dict):
            continue
        value = block.get(key)
        if value is not None and not isinstance(value, (dict, list)):
            fallbacks[env_name] = value
    return fallbacks


@dataclass(frozen=True)
class CrewSettings:
    enabled: bool
    provider: str
    process: str
    model: str
    manager_model: str
    api_key: str
    base_url: str | None
    temperature: float
    timeout_seconds: float
    max_retries: int
    max_iter: int
    verbose: bool
    memory: bool
    audit_path: Path
    config_path: Path
    route_mode: str
    amap_key: str
    amap_security_code: str
    amap_timeout_seconds: float
    notify_path: Path

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.api_key and self.model)

    @property
    def effective_manager_model(self) -> str:
        return self.manager_model or self.model


def load_crew_settings() -> CrewSettings:
    config_path = _path("TRAFFIC_CREW_CONFIG_PATH", DEFAULT_CONFIG_PATH)
    fallbacks = load_yaml_fallbacks(config_path)

    provider = _text("TRAFFIC_CREW_PROVIDER", "openai-compatible", fallbacks).strip().lower()
    bailian = provider in BAILIAN_PROVIDERS
    # 密钥只认环境变量：Crew 专用项 -> 既有 Agent 项 -> 厂商项 -> 通用项。
    api_key = (
        os.getenv("TRAFFIC_CREW_LLM_API_KEY", "").strip()
        or os.getenv("TRAFFIC_AGENT_API_KEY", "").strip()
        or (os.getenv("DASHSCOPE_API_KEY", "").strip() if bailian else "")
        or os.getenv("OPENAI_API_KEY", "").strip()
    )
    default_model = BAILIAN_DEFAULT_MODEL if bailian else "gpt-4o-mini"
    default_base_url = BAILIAN_DEFAULT_BASE_URL if bailian else ""

    return CrewSettings(
        enabled=_flag("TRAFFIC_CREW_ENABLED", default=False, fallbacks=fallbacks),
        provider=provider,
        process=_choice("TRAFFIC_CREW_PROCESS", "hierarchical", VALID_PROCESSES, fallbacks),
        model=_text("TRAFFIC_CREW_LLM_MODEL", default_model, fallbacks),
        manager_model=_text("TRAFFIC_CREW_MANAGER_MODEL", "", fallbacks),
        api_key=api_key,
        base_url=_text("TRAFFIC_CREW_LLM_BASE_URL", default_base_url, fallbacks) or None,
        temperature=_number("TRAFFIC_CREW_TEMPERATURE", 0.3, 0.0, 1.0, fallbacks),
        timeout_seconds=_number("TRAFFIC_CREW_TIMEOUT_SECONDS", 60.0, 5.0, 300.0, fallbacks),
        max_retries=_integer("TRAFFIC_CREW_MAX_RETRIES", 1, 0, 5, fallbacks),
        max_iter=_integer("TRAFFIC_CREW_MAX_ITER", 6, 1, 20),
        verbose=_flag("TRAFFIC_CREW_VERBOSE", default=False, fallbacks=fallbacks),
        memory=_flag("TRAFFIC_CREW_MEMORY", default=False, fallbacks=fallbacks),
        audit_path=_path("TRAFFIC_CREW_AUDIT_PATH", INNER_ROOT / "data" / "crew" / "crew_runs.jsonl"),
        config_path=config_path,
        route_mode=_choice("TRAFFIC_CREW_ROUTE_MODE", "auto", VALID_ROUTE_MODES),
        amap_key=os.getenv("AMAP_KEY", "").strip(),
        amap_security_code=os.getenv("AMAP_SECURITY_CODE", "").strip(),
        amap_timeout_seconds=_number("TRAFFIC_CREW_AMAP_TIMEOUT_SECONDS", 8.0, 1.0, 60.0),
        notify_path=_path("TRAFFIC_CREW_NOTIFY_PATH", INNER_ROOT / "data" / "crew" / "public_notices.jsonl"),
    )
