from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from time import perf_counter
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .backup_scheduler import coordinator
from .db import engine
from .entity_service import entity_refresh_coordinator
from .media_preview import start_media_preview_workers, stop_media_preview_workers
from .origin_utils import is_allowed_browser_origin
from .routes_auth import router as auth_router
from .routes_archive import router as archive_router
from .routes_backups import router as backups_router
from .routes_chats import router as chats_router
from .routes_entities import router as entities_router
from .routes_overview import router as overview_router
from .routes_realtime import router as realtime_router
from .routes_rules import router as rules_router
from .routes_telegram import router as telegram_router
from .routes_users import router as users_router
from .telegram_runtime import runtime_manager


settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        await start_media_preview_workers(
            settings.media_preview_worker_count,
            settings.media_preview_queue_size,
        )
        await runtime_manager.start()
        await coordinator.start()
        await entity_refresh_coordinator.start()
        yield
    finally:
        # Stop producers before their shared Telegram clients, then release the
        # database pool while the event loop is still alive.
        try:
            await entity_refresh_coordinator.stop()
        finally:
            try:
                await coordinator.stop()
            finally:
                try:
                    await runtime_manager.stop()
                finally:
                    try:
                        await stop_media_preview_workers()
                    finally:
                        await engine.dispose()


app = FastAPI(title="tgBackup", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_frontend_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


@app.middleware("http")
async def reject_foreign_browser_origins(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin and not is_allowed_browser_origin(
            origin,
            request.headers.get("host"),
            settings.allowed_frontend_origins,
        ):
            return JSONResponse(status_code=403, content={"detail": "不允许的请求来源"})
    started = perf_counter()
    response = await call_next(request)
    elapsed_ms = (perf_counter() - started) * 1000
    response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
    if elapsed_ms >= max(0, settings.slow_request_log_seconds) * 1000:
        logger.warning(
            "Slow request %s %s completed in %.1f ms",
            request.method,
            request.url.path,
            elapsed_ms,
        )
    return response


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(archive_router)
app.include_router(telegram_router)
app.include_router(chats_router)
app.include_router(entities_router)
app.include_router(overview_router)
app.include_router(backups_router)
app.include_router(rules_router)
app.include_router(realtime_router)
app.include_router(users_router)


# Vite is only a build-time dependency in production. When a build exists,
# FastAPI serves its fingerprinted assets and the SPA entry point directly.
frontend_dist = settings.frontend_dist.resolve()
frontend_assets = frontend_dist / "assets"
if frontend_assets.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=frontend_assets),
        name="frontend-assets",
    )


@app.get("/", include_in_schema=False)
async def frontend_index() -> FileResponse:
    index = frontend_dist / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="前端尚未构建")
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


@app.get("/{full_path:path}", include_in_schema=False)
async def frontend_spa(full_path: str) -> FileResponse:
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="接口不存在")
    root = frontend_dist
    candidate = (root / full_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="静态资源不存在") from exc
    if candidate.is_file():
        return FileResponse(candidate)
    index = root / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="前端尚未构建")
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
