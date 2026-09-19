"""Crew 测试共用的装配辅助。"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from backend.agent.tools import TrafficToolGateway
from backend.crew.config import DEFAULT_CONFIG_PATH, load_crew_settings
from backend.crew.contracts import EmergencyEvent
from backend.crew.service import CrewService

SAMPLE_EVENT = EmergencyEvent(
    event_id="EV-TEST-1",
    title="高速 K128 处多车追尾",
    description="3 车追尾，道路阻断",
    occurred_at="2026-08-23 14:35",
    location_text="G2 京沪高速 K128+200",
    checkpoint_id="CP-NORTH-01",
    lanes_blocked=2,
)

CREW_ENV = {
    "TRAFFIC_CREW_ENABLED": "true",
    "TRAFFIC_CREW_PROVIDER": "aliyun-bailian",
    "TRAFFIC_CREW_LLM_API_KEY": "test-key",
    "TRAFFIC_CREW_CONFIG_PATH": str(DEFAULT_CONFIG_PATH),
}


def make_gateway(rows: list[dict] | None = None):
    records = rows if rows is not None else [
        {"checkpoint_id": "CP-NORTH-01", "passenger_vehicle_flow_number": 80, "time": "2026-08-23 14:00:00"}
    ]
    return TrafficToolGateway(
        traffic_records=lambda checkpoint_id, limit: (records, "TimescaleDB", ""),
        detection_records=lambda checkpoint_id, limit: (records, "TimescaleDB", ""),
        predict_checkpoint=lambda checkpoint_id, steps: {"predicted": [{"step": 1, "flow": 90}]},
        law_search=lambda question, top_k: {"citations": [{"article": "第47条"}], "answer": "需在来车方向设警告标志"},
    )


def make_settings(root: str, **extra: str):
    values = dict(CREW_ENV)
    values.update(
        {
            "TRAFFIC_CREW_AUDIT_PATH": str(Path(root) / "crew_runs.jsonl"),
            "TRAFFIC_CREW_NOTIFY_PATH": str(Path(root) / "notices.jsonl"),
        }
    )
    values.update(extra)
    with patch.dict(os.environ, values, clear=True):
        return load_crew_settings()


def make_service(root: str, **extra: str) -> CrewService:
    return CrewService.build(make_settings(root, **extra), gateway=make_gateway())
