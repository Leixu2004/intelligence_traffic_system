"""公众通告工具：包装 PublicNoticeSink，输出带 simulation 标记的落盘记录。"""

from __future__ import annotations

import json
import time
from typing import Any

from crewai.tools import BaseTool

from ...agent.tools import record_tool_evidence


def _channels(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value]
    elif isinstance(value, dict):
        items = [str(key).strip() for key in value]
    else:
        items = [part.strip() for part in str(value).split(",")]
    selected = [item for item in items if item]
    return selected or None


class PublishPublicNoticeTool(BaseTool):
    name: str = "publish_public_notice"
    description: str = (
        "发布公众通告：生成并落地多渠道公众交通通告。输入：event_id、content（通告正文）、"
        "可选 channels（逗号分隔，如 短信,APP推送）。返回 JSON 含 notice_id、channels、simulation 标记。"
    )
    sink: Any = None
    default_event_id: str = ""

    def _run(self, event_id: str = "", content: str = "", channels: Any = "") -> str:
        started = time.perf_counter()
        selected = _channels(channels) or []
        try:
            text = str(content or "").strip()
            if not text:
                record: dict[str, Any] = {"ok": False, "message": "通告正文为空", "source": "input_invalid"}
            else:
                record = self.sink.publish(
                    event_id=str(event_id or "").strip() or self.default_event_id,
                    content=text,
                    channels=selected,
                )
        except Exception as exc:  # noqa: BLE001 - 通告失败不应让整条调度链路中止
            record = {
                "ok": False,
                "message": f"通告生成失败：{type(exc).__name__}: {exc}",
                "source": "notice_error",
            }
        published = bool(record.get("ok", True))
        record_tool_evidence(
            name=self.name,
            arguments={"event_id": str(event_id or "")[:64], "channels": selected},
            summary=json.dumps(record, ensure_ascii=False, default=str),
            source=str(
                record.get("source") or (record.get("channel_mode") if published else "notice_error") or "local_file"
            ),
            started=started,
            ok=published,
        )
        return self._payload(record)

    def _payload(self, extra: dict[str, Any]) -> str:
        return json.dumps({"tool": self.name, **extra}, ensure_ascii=False)
