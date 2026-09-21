"""9/22 模型服务化 · 集成层配置。

与 backend/agent、backend/vision 同一套约定：`TRAFFIC_*` 环境变量驱动、
frozen dataclass + `load_integration_settings()`、默认 `enabled=False`（未配置就整体降级为
不可用，而不是假装能跑）。密钥只从环境变量读取，绝不写进文件或仓库。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..agent.config import INNER_ROOT

INTEGRATION_ROOT = INNER_ROOT / "data" / "integration"
DEPLOY_DIR = Path(__file__).resolve().parent / "deploy"

# vLLM 默认不需要真实密钥（服务端 --api-key 不配时客户端传 EMPTY 即可），
# 但允许指向百炼等 OpenAI 兼容端点，那时密钥链与 agent/vision 同源。
VLLM_PLACEHOLDER_KEY = "EMPTY"
DEFAULT_VLLM_BASE_URL = "http://localhost:8000/v1"
DEFAULT_VLLM_MODEL = "qwen-7b"  # 课件 vllm_deploy 的 --served-model-name

# 课件第 5 页的数据流转：预测给出「未来 5 分钟」流量，故默认 5 步（步长取模型 bin_seconds）
DEFAULT_FORECAST_STEPS = 5
# 「连续 N 个低速窗口」判定拥堵：与 9/21 CEP 的红级口径（10 分钟 / 5 分钟步长 = 2 个窗口）对齐
DEFAULT_CONGESTION_WINDOWS = 2
# 大屏推送落地的 JSONL（Streamlit 轮询 /api/v1/integration/latest，见 README「大屏推送」）
PUSH_LOG_NAME = "screen_push.jsonl"


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


def _path(name: str, default: Path) -> Path:
    raw = os.getenv(name, "").strip()
    return Path(raw).expanduser() if raw else default


def _api_key() -> str:
    return (
        os.getenv("TRAFFIC_VLLM_API_KEY", "").strip()
        or os.getenv("TRAFFIC_AGENT_API_KEY", "").strip()
        or os.getenv("DASHSCOPE_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
        or VLLM_PLACEHOLDER_KEY
    )


@dataclass(frozen=True)
class IntegrationSettings:
    enabled: bool
    vllm_base_url: str
    vllm_api_key: str
    vllm_model: str
    temperature: float
    timeout_seconds: float
    max_retries: int
    max_output_tokens: int
    max_connections: int
    forecast_steps: int
    congestion_windows: int
    screen_url: str | None
    timescaledb_dsn: str
    output_dir: Path
    push_path: Path
    report_path: Path
    deploy_dir: Path
    monitor_config_path: Path

    @property
    def vllm_configured(self) -> bool:
        return bool(self.vllm_base_url and self.vllm_model and self.vllm_api_key)

    def describe(self) -> dict[str, object]:
        """健康探针用的非敏感摘要（不含密钥值）。"""
        return {
            "enabled": self.enabled,
            "vllm_base_url": self.vllm_base_url,
            "vllm_model": self.vllm_model,
            "api_key_kind": "placeholder" if self.vllm_api_key == VLLM_PLACEHOLDER_KEY else "real",
            "temperature": self.temperature,
            "timeout_seconds": self.timeout_seconds,
            "forecast_steps": self.forecast_steps,
            "congestion_windows": self.congestion_windows,
            "screen_transport": "http_polling",  # 大屏是 Streamlit，不持 WS；后端另有 /api/v1/vision/stream
            "screen_polling_url": "/api/v1/integration/latest",
        }


def load_integration_settings() -> IntegrationSettings:
    output_dir = _path("TRAFFIC_INTEGRATION_OUTPUT_DIR", INTEGRATION_ROOT)
    base_url = os.getenv("TRAFFIC_VLLM_BASE_URL", DEFAULT_VLLM_BASE_URL).strip().rstrip("/") or DEFAULT_VLLM_BASE_URL
    return IntegrationSettings(
        enabled=_flag("TRAFFIC_INTEGRATION_ENABLED", default=False),
        vllm_base_url=base_url,
        vllm_api_key=_api_key(),
        vllm_model=os.getenv("TRAFFIC_VLLM_MODEL", DEFAULT_VLLM_MODEL).strip() or DEFAULT_VLLM_MODEL,
        temperature=_number("TRAFFIC_VLLM_TEMPERATURE", 0.7, 0.0, 2.0),
        timeout_seconds=_number("TRAFFIC_VLLM_TIMEOUT_SECONDS", 30.0, 2.0, 300.0),
        max_retries=_integer("TRAFFIC_VLLM_MAX_RETRIES", 1, 0, 3),
        max_output_tokens=_integer("TRAFFIC_VLLM_MAX_TOKENS", 600, 64, 4096),
        max_connections=_integer("TRAFFIC_VLLM_MAX_CONNECTIONS", 16, 1, 256),
        forecast_steps=_integer("TRAFFIC_INTEGRATION_FORECAST_STEPS", DEFAULT_FORECAST_STEPS, 1, 60),
        congestion_windows=_integer("TRAFFIC_INTEGRATION_CONGESTION_WINDOWS", DEFAULT_CONGESTION_WINDOWS, 1, 20),
        screen_url=(os.getenv("TRAFFIC_INTEGRATION_SCREEN_URL", "").strip() or None),
        timescaledb_dsn=os.getenv("TIMESCALEDB_DSN", "").strip(),
        output_dir=output_dir,
        push_path=output_dir / PUSH_LOG_NAME,
        report_path=_path("TRAFFIC_INTEGRATION_REPORT_PATH", Path(__file__).resolve().parent / "integration_report.md"),
        deploy_dir=DEPLOY_DIR,
        monitor_config_path=DEPLOY_DIR / "monitor_config.yaml",
    )
