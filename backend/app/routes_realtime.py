from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import monotonic
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal
from .models import WebSession
from .realtime import realtime_hub
from .security import SESSION_COOKIE, digest
from .telegram_runtime import runtime_manager


router = APIRouter(tags=["realtime"])
settings = get_settings()


async def authenticated_user_id(session_token: str | None) -> int | None:
    if not session_token:
        return None
    async with SessionLocal() as db:
        record = await db.scalar(
            select(WebSession).where(WebSession.token_hash == digest(session_token))
        )
        if record is None:
            return None
        expires_at = record.expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            return None
        return record.user_id


async def receive_message(websocket: WebSocket) -> dict[str, Any]:
    value = await websocket.receive_json()
    return value if isinstance(value, dict) else {}


@router.websocket("/api/ws")
async def realtime_socket(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin", "").rstrip("/")
    if origin not in settings.allowed_frontend_origins:
        await websocket.close(code=4403, reason="origin_not_allowed")
        return
    session_token = websocket.cookies.get(SESSION_COOKIE)
    user_id = await authenticated_user_id(session_token)
    if user_id is None:
        await websocket.close(code=4401, reason="authentication_required")
        return

    await websocket.accept()
    subscription = await realtime_hub.subscribe(user_id)
    receive_task: asyncio.Task[dict[str, Any]] | None = None
    event_task: asyncio.Task[dict[str, object]] | None = None
    last_session_check = monotonic()
    try:
        await websocket.send_json(
            realtime_hub.event(
                "system.ready",
                {"protocol_version": 1, "heartbeat_seconds": 20},
            )
        )
        runtime_snapshot = await runtime_manager.snapshot_for_user(user_id)
        await websocket.send_json(
            realtime_hub.event(
                "telegram.runtime.changed",
                runtime_snapshot or {"connection": "unbound"},
            )
        )
        receive_task = asyncio.create_task(receive_message(websocket))
        event_task = asyncio.create_task(subscription.queue.get())
        while True:
            done, _ = await asyncio.wait(
                {receive_task, event_task},
                timeout=25,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                await websocket.send_json(realtime_hub.event("system.ping", {}))
            if receive_task in done:
                message = receive_task.result()
                if message.get("type") == "client.ping":
                    await websocket.send_json(realtime_hub.event("system.pong", {}))
                receive_task = asyncio.create_task(receive_message(websocket))
            if event_task in done:
                await websocket.send_json(event_task.result())
                event_task = asyncio.create_task(subscription.queue.get())

            if monotonic() - last_session_check >= 60:
                if await authenticated_user_id(session_token) != user_id:
                    await websocket.close(code=4401, reason="session_expired")
                    return
                last_session_check = monotonic()
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        for task in (receive_task, event_task):
            if task:
                task.cancel()
        await asyncio.gather(
            *(task for task in (receive_task, event_task) if task),
            return_exceptions=True,
        )
        await realtime_hub.unsubscribe(subscription)
