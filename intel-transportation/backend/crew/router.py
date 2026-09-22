"""FastAPI router：应急处置多 Agent 协作入口。"""

from __future__ import annotations

import json
from time import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from .contracts import (
    CrewRunResponse,
    CrewRunResult,
    EmergencyEvent,
    RoutePlanRequest,
    RoutePlanResponse,
)
from .service import CrewService

router = APIRouter(prefix="/api/v1/crew", tags=["多 Agent 应急处置"])


def _service(request: Request) -> CrewService:
    service = getattr(request.app.state, "crew_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="emergency crew is not initialised")
    return service


def _check_key(request: Request, supplied: str | None) -> None:
    expected = str(getattr(request.app.state, "api_key", "") or "")
    if expected and supplied != expected:
        raise HTTPException(status_code=401, detail="invalid X-API-Key")


@router.get("/health")
def crew_health(request: Request) -> dict[str, Any]:
    return {"code": 200, "message": "ok", "data": _service(request).health()}


@router.post("/emergency/response", response_model=CrewRunResponse)
def emergency_response(
    payload: EmergencyEvent,
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> CrewRunResponse:
    _check_key(request, x_api_key)
    service = _service(request)
    result: CrewRunResult = service.respond(payload)
    if not result.ok and result.degradation and service.available:
        raise HTTPException(status_code=502, detail=result.degradation)
    return CrewRunResponse(data=result, timestamp=int(time()))


@router.post("/route/plan", response_model=RoutePlanResponse)
def route_plan(
    payload: RoutePlanRequest,
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> RoutePlanResponse:
    """独立的路径规划入口：复用 Agent 的 plan_route 工具，HTTP 与工具链共用同一份校验和证据口径。"""
    _check_key(request, x_api_key)
    service = _service(request)
    destination = payload.destination_gps or ("", "")
    result = json.loads(
        service.toolbox.plan_route(
            str(payload.origin_gps[0]),
            str(payload.origin_gps[1]),
            str(destination[0]),
            str(destination[1]),
        )
    )
    if not result.get("ok"):
        status = 422 if result.get("source") == "validation" else 503
        raise HTTPException(status_code=status, detail=result.get("message") or "路径规划失败")
    return RoutePlanResponse(data=result, timestamp=int(time()))
