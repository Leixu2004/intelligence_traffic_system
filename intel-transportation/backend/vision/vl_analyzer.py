"""Qwen-VL 图文理解与视觉问答（课件提交物 vl_analyzer.py）。

两种后端：
- remote：OpenAI 兼容的多模态端点（百炼 Qwen-VL 系列），只需一个 API Key，CPU 机器可用；
- local-transformers：本机加载 Qwen2-VL-7B（课件示例代码路径），需要 torch + ≥16GB 显存。

LLaVA 与 Qwen-VL 的架构差异见 README；本模块不实现 LLaVA 推理，
只在配置层保留 model 名可替换（同一 OpenAI 兼容协议下换模型名即可）。

任何后端不可用时抛 VLUnavailableError，由上层记录 degraded=true，
绝不会用模板文本冒充模型输出。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .config import LOCAL_INFERENCE_HINT, VisionSettings
from .prompt_store import PromptStore
from .video_processor import Frame

SEVERITY_CODES = {
    "none": ("无", "none", "no_accident", "正常"),
    "minor": ("轻微", "minor", "low", "轻度"),
    "medium": ("中等", "medium", "moderate"),
    "severe": ("严重", "severe", "high", "重大"),
}
# 匹配顺序即优先级：先具体后泛化，否则「碰撞行人」会被「碰撞」抢先判成车辆碰撞。
EVENT_TYPE_CODES = {
    "pedestrian_involved": ("行人", "非机动车", "pedestrian"),
    "vehicle_rollover": ("侧翻", "翻车", "rollover"),
    "abnormal_stop": ("异常停车", "违停", "stopped"),
    "congestion": ("拥堵", "排队", "congestion"),
    "vehicle_collision": ("追尾", "碰撞", "collision", "rear_end", "vehicle_collision", "剐蹭"),
    "unknown": (),
}
DEFAULT_EVENT_TYPE = "unknown"
JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
FENCE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)


class VLUnavailableError(RuntimeError):
    """视觉模型不可用（缺依赖 / 缺密钥 / 端点报错 / 返回不可解析）。"""


@runtime_checkable
class VisionClient(Protocol):
    name: str

    def complete(self, prompt: str, frame: Frame) -> str:
        ...


def normalise_severity(value: Any) -> str:
    text = str(value or "").strip().lower()
    for code, aliases in SEVERITY_CODES.items():
        if text in {alias.lower() for alias in aliases}:
            return code
    return "none" if text in {"", "null", "none"} else "medium"


def normalise_event_type(value: Any, accident: bool) -> str:
    text = str(value or "").strip().lower()
    for code, aliases in EVENT_TYPE_CODES.items():
        if any(alias.lower() in text for alias in aliases):
            return code
    return "unknown" if accident else "none"


def parse_model_json(text: str) -> dict[str, Any]:
    """从模型自由输出里取第一个 JSON 对象；失败退化为逐字段正则。"""
    cleaned = FENCE.sub("", text or "").strip()
    match = JSON_BLOCK.search(cleaned)
    if match:
        try:
            payload = json.loads(match.group(0))
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    recovered: dict[str, Any] = {}
    patterns = {
        "accident": r"事故\?*[:：]\s*(是|否|yes|no|true|false)",
        "accident_type": r"(?:事故类型|类型)\?*[:：]\s*([^\n,，]+)",
        "vehicle_count": r"(?:涉及车辆数|车辆数|车辆)\?*[:：]\s*(\d+)",
        "severity": r"(?:严重程度|严重度)\?*[:：]\s*([^\n,，]+)",
        "location": r"位置\?*[:：]\s*([^\n,，]+)",
        "lane": r"车道\?*[:：]\s*([^\n,，]+)",
        "actions": r"(?:建议|处置建议|措施)\?*[:：]\s*([^\n]+)",
    }
    for key, pattern in patterns.items():
        found = re.search(pattern, cleaned, re.IGNORECASE)
        if found:
            recovered[key] = found.group(1).strip()
    if recovered:
        recovered["_recovered_by_regex"] = True
    return recovered


@dataclass(frozen=True)
class AccidentAnalysis:
    accident: bool
    event_type: str
    severity: str
    accident_type_text: str = ""
    vehicle_count: int | None = None
    location: str | None = None
    lane: str | None = None
    actions: tuple[str, ...] = ()
    evidence: str = ""
    raw: str = ""
    model: str = ""
    latency_ms: int = 0
    verified: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def alert_level(self) -> str:
        if not self.accident:
            return "ok"
        return {"minor": "warn", "medium": "alert", "severe": "alert", "none": "ok"}.get(
            self.severity, "alert"
        )

    @classmethod
    def from_model_text(cls, text: str, *, model: str, latency_ms: int) -> "AccidentAnalysis":
        payload = parse_model_json(text)
        if not payload:
            raise VLUnavailableError(f"模型输出无法解析为事故结论: {text[:160]}")
        raw_accident = payload.get("accident", False)
        if isinstance(raw_accident, str):
            accident = raw_accident.strip().lower() in {"true", "yes", "是", "1"}
        else:
            accident = bool(raw_accident)
        actions = payload.get("actions") or []
        if isinstance(actions, str):
            actions = [part for part in re.split(r"[、,;，；]", actions) if part.strip()]
        notes: list[str] = []
        if payload.get("_recovered_by_regex"):
            notes.append("模型未按 JSON 输出，已用字段正则回收，结构化结果可能不完整")
        count = payload.get("vehicle_count")
        try:
            vehicle_count = int(count) if count is not None else None
        except (TypeError, ValueError):
            vehicle_count = None
            notes.append("vehicle_count 非整数，已置空")
        return cls(
            accident=accident,
            event_type=normalise_event_type(payload.get("accident_type"), accident),
            severity=normalise_severity(payload.get("severity")) if accident else "none",
            accident_type_text=str(payload.get("accident_type") or "").strip(),
            vehicle_count=vehicle_count,
            location=_text_or_none(payload.get("location")),
            lane=_text_or_none(payload.get("lane")),
            actions=tuple(str(action).strip() for action in actions if str(action).strip())[:6],
            evidence=str(payload.get("evidence") or "").strip(),
            raw=text,
            model=model,
            latency_ms=latency_ms,
            verified=True,
            notes=tuple(notes),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "accident": self.accident,
            "event_type": self.event_type,
            "severity": self.severity,
            "accident_type": self.accident_type_text,
            "vehicle_count": self.vehicle_count,
            "location": self.location,
            "lane": self.lane,
            "actions": list(self.actions),
            "evidence": self.evidence,
            "alert_level": self.alert_level,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "verified": self.verified,
            "notes": list(self.notes),
        }


def _text_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"null", "none", "无法判断", "未知"}:
        return None
    return text


class RemoteVisionClient:
    """OpenAI 兼容的多模态聊天端点（百炼 Qwen-VL / 任意同协议服务）。"""

    name = "remote-openai-compatible"

    def __init__(self, settings: VisionSettings):
        if not settings.vl_configured:
            raise VLUnavailableError(
                "remote 后端需要 TRAFFIC_VL_API_KEY（或 DASHSCOPE_API_KEY）与 base_url/model"
            )
        import httpx  # 延迟导入，未装 requests/httpx 时不影响其他模块

        self.settings = settings
        # httpx 的 base_url 必须以 / 结尾，且请求路径不能以 / 开头，否则 /v1 前缀会被丢掉
        base_url = (settings.base_url or "").rstrip("/") + "/"
        self._http = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(settings.timeout_seconds),
            headers={"Authorization": f"Bearer {settings.api_key}"},
        )

    def complete(self, prompt: str, frame: Frame) -> str:
        payload = {
            "model": self.settings.model,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_output_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": frame.to_data_url(
                                    quality=self.settings.jpeg_quality,
                                    max_side=self.settings.image_max_side,
                                )
                            },
                        },
                    ],
                }
            ],
        }
        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                response = self._http.post("/chat/completions", json=payload)
                response.raise_for_status()
                body = response.json()
            except Exception as exc:  # noqa: BLE001 - 统一转成可读降级原因
                last_error = exc
                if attempt < self.settings.max_retries:
                    time.sleep(0.6 * (attempt + 1))
                    continue
                break
            else:
                return self._extract_text(body)
        detail = str(last_error)[:200] if last_error else "未知错误"
        raise VLUnavailableError(f"视觉端点调用失败: {type(last_error).__name__}: {detail}")

    @staticmethod
    def _extract_text(body: dict[str, Any]) -> str:
        choices = body.get("choices") or []
        if not choices:
            raise VLUnavailableError(f"视觉端点返回缺少 choices: {str(body)[:160]}")
        content = (choices[0].get("message") or {}).get("content")
        if isinstance(content, list):  # 部分服务把 content 拆成多段
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        text = str(content or "").strip()
        if not text:
            raise VLUnavailableError("视觉端点返回空内容")
        return text

    def close(self) -> None:
        self._http.close()


class TransformersVisionClient:
    """本机 transformers 推理 Qwen2-VL（课件第 4 页代码路径）。"""

    name = "local-transformers"

    def __init__(self, settings: VisionSettings):
        try:
            import torch
            from transformers import AutoProcessor, Qwen2VLForConditionalGeneration
        except ImportError as exc:
            raise VLUnavailableError(f"local-transformers 后端不可用：{exc}。{LOCAL_INFERENCE_HINT}") from exc
        self.settings = settings
        self._torch = torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            self.model = Qwen2VLForConditionalGeneration.from_pretrained(
                settings.local_model_path, torch_dtype="auto", device_map=device
            )
            self.processor = AutoProcessor.from_pretrained(settings.local_model_path)
        except Exception as exc:  # noqa: BLE001 - 权重下载失败/显存不足都要能读出来
            raise VLUnavailableError(f"加载 {settings.local_model_path} 失败: {exc}") from exc

    def complete(self, prompt: str, frame: Frame) -> str:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": frame.image},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.processor(text=[text], images=[frame.image], padding=True, return_tensors="pt")
        inputs = {key: value.to(self.model.device) for key, value in inputs.items()}
        with self._torch.no_grad():
            generated = self.model.generate(**inputs, max_new_tokens=self.settings.max_output_tokens)
        trimmed = [
            out[len(inputs.input_ids[0]) :] for out in generated
        ]
        return str(self.processor.batch_decode(trimmed, skip_special_tokens=True)[0]).strip()


class VLAnalyzer:
    """把 Prompt 模板 + 帧 送进视觉模型，并给出结构化结果。"""

    def __init__(self, settings: VisionSettings, prompts: PromptStore, client: VisionClient | None = None):
        self.settings = settings
        self.prompts = prompts
        self._client = client
        self._client_error = ""

    @property
    def client(self) -> VisionClient:
        if self._client is None:
            try:
                self._client = (
                    TransformersVisionClient(self.settings)
                    if self.settings.backend == "local-transformers"
                    else RemoteVisionClient(self.settings)
                )
            except VLUnavailableError as exc:
                self._client_error = str(exc)
                raise
        return self._client

    @property
    def model_label(self) -> str:
        return f"{self.settings.backend}:{self.settings.model}"

    def _ask(self, prompt: str, frame: Frame) -> tuple[str, int]:
        started = time.perf_counter()
        text = self.client.complete(prompt, frame)
        return text, int((time.perf_counter() - started) * 1000)

    def describe_scene(self, frame: Frame) -> str:
        prompt = self.prompts.render("scene_description")
        text, _ = self._ask(prompt, frame)
        return text

    def answer_question(self, frame: Frame, question: str) -> str:
        """视觉问答（VQA）：场景描述模板 + 追加问题，与课件 visual_qa 同构。"""
        base = self.prompts.render("scene_description")
        prompt = f"{base}\n\n在上述画面基础上，只回答这个问题，不超过 60 字：{question}"
        text, _ = self._ask(prompt, frame)
        return text

    def analyze_accident(self, frame: Frame) -> AccidentAnalysis:
        prompt = self.prompts.render("accident_analysis")
        text, latency = self._ask(prompt, frame)
        return AccidentAnalysis.from_model_text(text, model=self.model_label, latency_ms=latency)

    def narrate(self, facts: str, frame: Frame) -> str:
        """把时间线事实交给模型，生成自然语言解说（课件第 7 页的多模态融合步骤）。

        远程端点要求消息里带 image_url，因此解说也要一张视觉锚点帧：由调用方
        传入最能代表当前状态的关键帧，避免隐式状态导致误用。
        """
        prompt = self.prompts.render("narration", facts=facts, frame_time=frame.time_label)
        text, _ = self._ask(prompt, frame)
        return text

    def build_alert(self, analysis: AccidentAnalysis, frame: Frame) -> dict[str, Any]:
        """生成对外推送文案。模型不可用时用已确定的结构化字段兜底（verified=False）。"""
        prompt = self.prompts.render("alert", analysis=json.dumps(analysis.as_dict(), ensure_ascii=False))
        try:
            text, _ = self._ask(prompt, frame)
        except VLUnavailableError as exc:
            return self._fallback_alert(analysis, reason=str(exc))
        payload = parse_model_json(text)
        if not payload:
            return self._fallback_alert(analysis, reason=f"告警输出无法解析: {text[:120]}")
        actions = payload.get("actions") or list(analysis.actions)
        if isinstance(actions, str):
            actions = [part for part in re.split(r"[、,;，；]", actions) if part.strip()]
        return {
            "title": str(payload.get("title") or "").strip(),
            "level": str(payload.get("level") or analysis.alert_level).strip().lower(),
            "summary": str(payload.get("summary") or "").strip(),
            "actions": [str(action).strip() for action in actions if str(action).strip()][:6],
            "push_text": str(payload.get("push_text") or "").strip(),
            "verified": True,
            "reason": "",
        }

    @staticmethod
    def _fallback_alert(analysis: AccidentAnalysis, *, reason: str) -> dict[str, Any]:
        """告警不能因为模型故障而丢失：事件类型/严重度/措施直接沿用已确认的结构化结论。"""
        type_label = analysis.accident_type_text or analysis.event_type
        return {
            "title": f"{type_label}告警" if analysis.accident else "无需告警",
            "level": analysis.alert_level,
            "summary": analysis.evidence,
            "actions": list(analysis.actions),
            "push_text": "；".join(
                part
                for part in (
                    f"检测到{type_label}" if analysis.accident else "画面正常",
                    f"严重程度 {analysis.severity}" if analysis.severity != "none" else "",
                    analysis.evidence,
                )
                if part
            ),
            "verified": False,
            "reason": reason,
        }

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.settings.backend,
            "model": self.settings.model,
            "configured": self.settings.vl_configured,
            "last_error": self._client_error,
        }
