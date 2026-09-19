"""FastAPI router for the traffic law RAG subsystem (health / query / rebuild)."""

from __future__ import annotations

from time import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from .contracts import (
    RagQueryRequest,
    RagQueryResponse,
    RagRebuildData,
    RagRebuildRequest,
    RagRebuildResponse,
)
from .retriever import RagUnavailableError

router = APIRouter(prefix="/api/v1/rag", tags=["法规RAG问答"])


def _service(request: Request):
    service = getattr(request.app.state, "rag_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="rag service is not initialised")
    return service


def _check_key(request: Request, supplied: str | None) -> None:
    expected = str(getattr(request.app.state, "api_key", "") or "")
    if expected and supplied != expected:
        raise HTTPException(status_code=401, detail="invalid X-API-Key")


@router.get("/health")
def rag_health(request: Request) -> dict[str, Any]:
    return {"code": 200, "message": "ok", "data": _service(request).health()}


@router.post("/query", response_model=RagQueryResponse)
def rag_query(
    payload: RagQueryRequest,
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> RagQueryResponse:
    _check_key(request, x_api_key)
    service = _service(request)
    try:
        data = service.query(payload.question, payload.top_k)
    except RagUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RagQueryResponse(data=data, timestamp=int(time()))


@router.post("/rebuild", response_model=RagRebuildResponse)
def rag_rebuild(
    payload: RagRebuildRequest,
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> RagRebuildResponse:
    _check_key(request, x_api_key)
    service = _service(request)
    try:
        result = service.rebuild(payload.doc_id)
    except RagUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RagRebuildResponse(data=RagRebuildData(**result), timestamp=int(time()))
