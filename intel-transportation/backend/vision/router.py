"""FastAPI 路由：多模态图文理解与视频解说（课件第 9 页的 API 封装）。

同时被两处使用：
- backend/dashboard_api.py 直接 include_router，与预测/RAG/Crew 共用大屏后端；
- backend/vision/api_server.py 的独立服务（课件提交物要求的 api_server.py）。

失败口径：未启用或缺密钥 → 503 并给出原因；配置齐全但模型链路跑完没有结果 → 502 带降级说明。
"""

from __future__ import annotations

import asyncio
import json
from time import time
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, WebSocket

from .contracts import VisionAnalysisData, VisionAnalysisResponse
from .plate_service import PlateService
from .service import VisionService

router = APIRouter(prefix="/api/v1/vision", tags=["多模态视频解说"])

WS_TIMEOUT_SECONDS = 2.0
DEFAULT_FILENAMES = {"image": "image.jpg", "video": "clip.mp4"}


def _service(request: Request) -> VisionService:
    service = getattr(request.app.state, "vision_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="vision service is not initialised")
    return service


def _require_available(service: VisionService) -> VisionService:
    """未启用/缺密钥时直接 503，不去调用注定失败的模型。"""
    if not service.available:
        raise HTTPException(status_code=503, detail=service.health()["error"] or "多模态服务不可用")
    return service


def _unwrap(payload: dict[str, Any]) -> VisionAnalysisResponse:
    data = VisionAnalysisData.model_validate(payload)
    if not data.timeline and data.degradations:
        raise HTTPException(status_code=502, detail="；".join(data.degradations))
    return VisionAnalysisResponse(data=data, timestamp=int(time()))


async def _read_upload(file: UploadFile, fallback: str) -> tuple[str, bytes]:
    payload = await file.read()
    return file.filename or fallback, payload


@router.get("/health")
def vision_health(request: Request) -> dict[str, Any]:
    return {"code": 200, "message": "ok", "data": _service(request).health()}


@router.post("/analyze", response_model=VisionAnalysisResponse)
async def analyze_image(
    request: Request,
    file: UploadFile = File(...),
    question: str = Form(default=""),
) -> VisionAnalysisResponse:
    """图片描述 + 结构化标签；带 question 时追加一次视觉问答（VQA）。"""
    service = _require_available(_service(request))
    name, payload = await _read_upload(file, DEFAULT_FILENAMES["image"])
    try:
        if question.strip():
            result = await asyncio.to_thread(service.answer, name, payload, question.strip())
        else:
            result = await asyncio.to_thread(service.analyze_upload, name, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _unwrap(result)


@router.post("/video", response_model=VisionAnalysisResponse)
async def analyze_video(
    request: Request,
    file: UploadFile = File(...),
) -> VisionAnalysisResponse:
    """上传监控视频：抽帧 → 关键帧筛选 → 逐帧分析 → 解说 + 告警。"""
    service = _require_available(_service(request))
    name, payload = await _read_upload(file, DEFAULT_FILENAMES["video"])
    try:
        result = await asyncio.to_thread(service.analyze_upload, name, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _unwrap(result)


def _plate_service(request: Request) -> PlateService:
    service = getattr(request.app.state, "plate_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="plate service is not initialised")
    return service


@router.get("/plate/health")
def plate_health(request: Request) -> dict[str, Any]:
    return {"code": 200, "message": "ok", "data": _plate_service(request).health()}


@router.post("/plate")
async def recognize_plate(
    request: Request,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """上传一张车辆图片：车牌检测 → 透视校正 → OCR → 号牌校验，返回文本、置信度与框坐标。

    与 /analyze 的区别：这里走的是项目自己的 LPR 流水线（结构化号牌），
    不调用多模态大模型，因此不需要密钥，也不产生大模型费用。
    """
    service = _plate_service(request)
    if not service.available:
        raise HTTPException(
            status_code=503,
            detail=service.health()["error"] or "车牌取证不可用",
        )
    name, payload = await _read_upload(file, DEFAULT_FILENAMES["image"])
    try:
        result = await asyncio.to_thread(service.recognize, name, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"code": 200, "message": "ok", "data": result, "timestamp": int(time())}


@router.get("/results")
def analysis_results(
    request: Request,
    limit: int = 50,
    alerts_only: bool = False,
) -> dict[str, Any]:
    """历史分析结果：默认读 runs.jsonl，alerts_only=true 读告警流水。"""
    data = _service(request).results(limit=limit, alerts_only=alerts_only)
    return {"code": 200, "message": "ok", "data": data, "timestamp": int(time())}


@router.websocket("/stream")
async def stream_video(websocket: WebSocket) -> None:
    """WebSocket 推流：客户端发 {"source": "视频路径或图片目录"}，逐帧回推分析结果。"""
    await websocket.accept()
    service: VisionService | None = getattr(websocket.app.state, "vision_service", None)
    if service is None or not service.available:
        reason = "vision service is not available" if service is None else str(service.health()["error"])
        await _send_error(websocket, reason)
        return

    source = await _read_source(websocket)
    if not source:
        await _send_error(websocket, '需要 JSON: {"source": "视频路径或图片目录"}')
        return

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    system = service.system

    def push(item: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, item)

    system.subscribe(push)
    await websocket.send_json({"type": "start", "source": source, "model": system.analyzer.model_label})
    task = asyncio.create_task(asyncio.to_thread(service.analyze_source, source))
    try:
        while not task.done() or not queue.empty():
            try:
                item = await asyncio.wait_for(queue.get(), timeout=WS_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                continue
            await websocket.send_json({"type": "frame", "data": item})
        await websocket.send_json({"type": "result", "data": await task})
    except asyncio.CancelledError:  # pragma: no cover - 客户端断开
        raise
    except Exception as exc:  # noqa: BLE001 - 断链/推理失败都要回一条 error 而不是静默关闭
        await _send_error(websocket, f"{type(exc).__name__}: {exc}")
    finally:
        system.unsubscribe(push)
        if not task.done():
            task.cancel()
        await websocket.close()


async def _send_error(websocket: WebSocket, message: str) -> None:
    await websocket.send_json({"type": "error", "message": message})
    await websocket.close()


async def _read_source(websocket: WebSocket) -> str:
    try:
        request = json.loads(await websocket.receive_text())
    except (json.JSONDecodeError, TypeError):
        return ""
    return str(request.get("source") or "").strip() if isinstance(request, dict) else ""
