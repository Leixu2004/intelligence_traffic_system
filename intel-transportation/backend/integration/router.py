"""FastAPI 路由：车路云一体化集成（9/22 课件第 6 页的接口封装）。

失败口径与 backend/vision 一致：
  * 未启用 / 依赖没配 → 503，并给出缺哪一段；
  * 配置齐全但链路一段都没跑出结果 → 502，带 degradations 明细。
"""

from __future__ import annotations

from time import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from .contracts import IntegrationEnvelope, IntegrationRunModel, IntegrationRunResponse
from .service import IntegrationService, read_pushes

router = APIRouter(prefix="/api/v1/integration", tags=["车路云一体化集成"])


def _service(request: Request) -> IntegrationService:
    service = getattr(request.app.state, "integration_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="integration service is not initialised")
    return service


def _require_enabled(service: IntegrationService) -> IntegrationService:
    if not service.settings.enabled:
        raise HTTPException(status_code=503, detail="集成层未启用（设 TRAFFIC_INTEGRATION_ENABLED=true）")
    return service


def _envelope(data: dict[str, Any]) -> IntegrationEnvelope:
    return IntegrationEnvelope(data=data, timestamp=int(time()))


@router.get("/health")
def integration_health(request: Request) -> IntegrationEnvelope:
    service = _service(request)
    return _envelope({**service.health(), "settings": service.settings.describe()})


@router.post("/run", response_model=IntegrationRunResponse)
def run_pipeline(
    request: Request,
    checkpoint_id: str | None = Query(default=None, max_length=64),
) -> IntegrationRunResponse:
    """跑一次完整五段链路；不传 checkpoint_id 时取最近窗口里最严重的一路。"""
    service = _require_enabled(_service(request))
    run = service.run(checkpoint_id)
    if run.query is None:
        raise HTTPException(status_code=502, detail="；".join(run.degradations))
    return IntegrationRunResponse(data=IntegrationRunModel.model_validate(run.as_dict()), timestamp=int(time()))


@router.get("/latest", response_model=IntegrationRunResponse)
def latest_run(request: Request) -> IntegrationRunResponse:
    """大屏轮询入口：返回最近一次集成结果（进程重启后回读推送日志）。"""
    service = _service(request)
    run = service.latest()
    if run is None:
        raise HTTPException(status_code=404, detail="还没有集成结果，先 POST /api/v1/integration/run")
    return IntegrationRunResponse(data=IntegrationRunModel.model_validate(run.as_dict()), timestamp=int(time()))


@router.get("/pushes")
def pushes(
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
) -> IntegrationEnvelope:
    service = _service(request)
    return _envelope({"push_path": str(service.push_path), "records": read_pushes(service.push_path, limit=limit)})


@router.get("/vllm/health")
def vllm_health(request: Request) -> IntegrationEnvelope:
    service = _service(request)
    payload = service.health()["vllm"]
    return _envelope({**payload, "endpoint": service.settings.vllm_base_url, "served_model_expected": service.settings.vllm_model})


@router.get("/report")
def report(request: Request) -> IntegrationEnvelope:
    """课件要求的 `integration_report.md`：由 `system_integration.py` 生成，这里只负责读出来。"""
    service = _service(request)
    path = service.settings.report_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="报告尚未生成：运行 python -m backend.integration.system_integration")
    return _envelope({"path": str(path), "markdown": path.read_text(encoding="utf-8")})
