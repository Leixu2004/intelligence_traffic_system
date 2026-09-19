"""公众通告落地：只写本地 JSONL，不接任何真实短信 / APP / 广播通道。

课件第 11 页的“短信推送 3.2 万用户”是演示样例。本模块产出的每条记录都带
``simulation=true`` 与 ``delivered=false``，因此不会被误当成真实触达证据。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

LOGGER = logging.getLogger(__name__)

DEFAULT_CHANNELS = ("短信", "APP推送", "交通广播")


class PublicNoticeSink:
    def __init__(self, path: Path | str, *, channels: tuple[str, ...] = DEFAULT_CHANNELS):
        self.path = Path(path)
        self.channels = channels
        self._lock = Lock()

    def publish(
        self,
        *,
        event_id: str,
        content: str,
        channels: list[str] | None = None,
        audience_hint: str = "",
    ) -> dict[str, Any]:
        selected = [str(c) for c in (channels or self.channels) if str(c).strip()]
        record = {
            "notice_id": f"NT-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
            "event_id": event_id,
            "channels": selected,
            "audience_hint": audience_hint or "未指定",
            "content": content,
            "channel_mode": "local_file",
            "delivered": False,
            "simulation": True,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "note": "通告文本已生成并落盘，未接入真实发布通道，不构成触达证据",
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            LOGGER.exception("Unable to persist public notice")
            return {**record, "ok": False, "error": f"通告落盘失败：{type(exc).__name__}"}
        return {**record, "ok": True}
