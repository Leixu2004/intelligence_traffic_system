"""Append-only audit records without private reasoning or secrets."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

LOGGER = logging.getLogger(__name__)


class JsonlAuditLog:
    def __init__(self, path: Path):
        self.path = path
        self._lock = Lock()

    def append(self, record: dict[str, Any]) -> None:
        payload = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            **record,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            LOGGER.exception("Unable to append traffic agent audit record")
