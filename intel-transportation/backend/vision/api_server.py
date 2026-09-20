"""独立 FastAPI 服务（课件提交物 api_server.py）。

接口实现与路由定义在同包的 router.py（大屏后端 backend/dashboard_api.py 复用同一个
router，因此不存在两份接口逻辑）。本文件只负责：装配生命周期 + 跨域 + 启动。

启动：
    python -m backend.vision.api_server            # 127.0.0.1:8600
    uvicorn backend.vision.api_server:app --port 8600
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import VisionSettings, load_vision_settings
from .router import router
from .service import VisionService

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8600


def _origins() -> list[str]:
    raw = os.getenv("VISION_CORS_ORIGINS", "http://localhost:8501,http://127.0.0.1:8501")
    return [item.strip() for item in raw.split(",") if item.strip() and item.strip() != "*"]


def create_app(settings: VisionSettings | None = None, service: VisionService | None = None) -> FastAPI:
    """装配独立服务。

    ``service`` 注入点给测试和「同一进程内复用已装配好的分析系统」用；
    缺省时按环境变量装配（VisionService.build 不抛异常，缺依赖只在 health() 里报原因）。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.vision_service = service or VisionService.build(settings or load_vision_settings())
        yield

    app = FastAPI(
        title="多模态监控视频解说系统",
        version="1.0.0",
        description="Qwen-VL 图文理解 + 视频抽帧分析 + 智能解说与事故告警",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    app.include_router(router)
    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover - 手动启动入口
    import uvicorn

    uvicorn.run(
        "backend.vision.api_server:app",
        host=os.getenv("VISION_HOST", DEFAULT_HOST),
        port=int(os.getenv("VISION_PORT", str(DEFAULT_PORT))),
        reload=False,
    )
