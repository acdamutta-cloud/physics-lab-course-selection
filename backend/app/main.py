import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.endpoints import api_v1_router
from app.core.config.settings import get_settings
from app.db.redis_client import close_redis_client
from app.db.session import dispose_database_engine
from app.services import selection_service

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_task = asyncio.create_task(selection_service.consume_selection_queue())
    yield
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    await dispose_database_engine()
    await close_redis_client()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 路由必须先注册
app.include_router(api_v1_router)

# 挂载前端静态文件（仅 assets 目录 + index.html）
frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if frontend_dist.is_dir():
    assets_dir = frontend_dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    from starlette.responses import FileResponse
    index_path = frontend_dist / "index.html"

    @app.get("/")
    async def serve_index():
        return FileResponse(str(index_path))


@app.get("/api/health")
async def root():
    return {"message": settings.app_name, "version": settings.app_version}
