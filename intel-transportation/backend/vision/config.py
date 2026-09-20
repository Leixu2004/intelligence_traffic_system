"""环境变量驱动的多模态子系统配置。

密钥链与 backend/crew、backend/rag 共用同一来源（先专用后通用），
凭证只从环境变量读取，绝不写进 config 文件或仓库。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..agent.config import BAILIAN_DEFAULT_BASE_URL, BAILIAN_PROVIDERS, INNER_ROOT

VISION_ROOT = INNER_ROOT / "data" / "vision"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# remote = OpenAI 兼容视觉端点（百炼 Qwen-VL 走这条）；local = transformers 本机推理 Qwen2-VL
VL_BACKENDS = {"remote", "local-transformers"}
# 本地 transformers 推理需要的重型依赖，缺失时给出可读提示而不是 ImportError 栈
LOCAL_INFERENCE_HINT = "pip install torch transformers accelerate（显存 ≥ 16GB，或先用 remote 后端）"

DEFAULT_VL_MODEL = "qwen-vl-max"
DEFAULT_LOCAL_MODEL = "Qwen/Qwen2-VL-7B-Instruct"

PROMPT_NAMES = ("scene_description", "accident_analysis", "narration", "alert")


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


@dataclass(frozen=True)
class VisionSettings:
    enabled: bool
    backend: str
    model: str
    api_key: str
    base_url: str | None
    temperature: float
    timeout_seconds: float
    max_retries: int
    max_output_tokens: int
    local_model_path: str
    frame_interval_seconds: float
    max_frames_per_video: int
    keyframe_threshold: float
    jpeg_quality: int
    image_max_side: int
    prompts_dir: Path
    output_dir: Path
    frames_dir: Path
    runs_path: Path
    alerts_path: Path

    @property
    def vl_configured(self) -> bool:
        if self.backend == "local-transformers":
            return True  # 取决于重型依赖，实例化时再判定
        return bool(self.api_key and self.model and self.base_url)

    @property
    def keyframe_filtering(self) -> bool:
        return self.keyframe_threshold > 0.0

    def describe(self) -> dict[str, object]:
        """健康探针用的非敏感摘要（不含密钥值）。"""
        return {
            "enabled": self.enabled,
            "backend": self.backend,
            "model": self.model,
            "base_url": self.base_url or "",
            "api_key_present": bool(self.api_key),
            "frame_interval_seconds": self.frame_interval_seconds,
            "max_frames_per_video": self.max_frames_per_video,
            "keyframe_threshold": self.keyframe_threshold,
            "temperature": self.temperature,
        }


def load_vision_settings() -> VisionSettings:
    backend = os.getenv("TRAFFIC_VL_BACKEND", "remote").strip().lower() or "remote"
    if backend not in VL_BACKENDS:
        backend = "remote"

    api_key = (
        os.getenv("TRAFFIC_VL_API_KEY", "").strip()
        or os.getenv("TRAFFIC_AGENT_API_KEY", "").strip()
        or os.getenv("DASHSCOPE_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )
    provider = (
        os.getenv("TRAFFIC_VL_PROVIDER", "").strip().lower()
        or os.getenv("TRAFFIC_AGENT_PROVIDER", "").strip().lower()
        or "aliyun-bailian"
    )
    base_url = (
        os.getenv("TRAFFIC_VL_BASE_URL", "").strip() or os.getenv("TRAFFIC_AGENT_BASE_URL", "").strip()
    ) or None
    if base_url is None and provider in BAILIAN_PROVIDERS:
        base_url = BAILIAN_DEFAULT_BASE_URL

    default_model = DEFAULT_VL_MODEL if provider in BAILIAN_PROVIDERS else "gpt-4o"
    model = os.getenv("TRAFFIC_VL_MODEL", "").strip() or default_model
    if backend == "local-transformers":
        model = os.getenv("TRAFFIC_VL_LOCAL_MODEL", DEFAULT_LOCAL_MODEL).strip() or DEFAULT_LOCAL_MODEL

    output_dir = _path("TRAFFIC_VISION_OUTPUT_DIR", VISION_ROOT)
    return VisionSettings(
        enabled=_flag("TRAFFIC_VISION_ENABLED", default=False),
        backend=backend,
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=_number("TRAFFIC_VL_TEMPERATURE", 0.2, 0.0, 1.0),
        timeout_seconds=_number("TRAFFIC_VL_TIMEOUT_SECONDS", 60.0, 5.0, 300.0),
        max_retries=_integer("TRAFFIC_VL_MAX_RETRIES", 1, 0, 3),
        max_output_tokens=_integer("TRAFFIC_VL_MAX_TOKENS", 800, 64, 4096),
        local_model_path=model,
        frame_interval_seconds=_number("TRAFFIC_VISION_FRAME_INTERVAL", 2.0, 0.2, 60.0),
        max_frames_per_video=_integer("TRAFFIC_VISION_MAX_FRAMES", 30, 1, 600),
        keyframe_threshold=_number("TRAFFIC_VISION_KEYFRAME_THRESHOLD", 0.02, 0.0, 1.0),
        jpeg_quality=_integer("TRAFFIC_VISION_JPEG_QUALITY", 85, 40, 100),
        image_max_side=_integer("TRAFFIC_VISION_MAX_SIDE", 1024, 256, 4096),
        prompts_dir=_path("TRAFFIC_VISION_PROMPTS_DIR", PROMPTS_DIR),
        output_dir=output_dir,
        frames_dir=output_dir / "frames",
        runs_path=output_dir / "runs.jsonl",
        alerts_path=output_dir / "alerts.jsonl",
    )
