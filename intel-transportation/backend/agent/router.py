"""FastAPI router for traffic assistant health and queries."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from .contracts import AssistantQueryRequest, AssistantResponse
from .service import AgentUnavailableError, TrafficAgentService

router = APIRouter(prefix="/api/v1", tags=["交通指挥 Agent"])


def _service(request: Request) -> TrafficAgentService:
    service = getattr(request.app.state, "traffic_agent_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="traffic agent is not initialised")
    return service


def _check_key(request: Request, supplied: str | None) -> None:
    expected = str(getattr(request.app.state, "api_key", "") or "")
    if expected and supplied != expected:
        raise HTTPException(status_code=401, detail="invalid X-API-Key")


@router.get("/agent/health")
def agent_health(request: Request) -> dict[str, Any]:
    return {"code": 200, "message": "ok", "data": _service(request).health()}


@router.post("/assistant/query", response_model=AssistantResponse)
def assistant_query(
    payload: AssistantQueryRequest,
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> AssistantResponse:
    _check_key(request, x_api_key)
    service = _service(request)
    try:
        data = service.query(
            question=payload.question,
            thread_id=payload.thread_id,
            checkpoint_id=payload.checkpoint_id,
        )
    except AgentUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    from time import time

    return AssistantResponse(data=data, timestamp=int(time()))
