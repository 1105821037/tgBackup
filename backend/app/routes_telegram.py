from __future__ import annotations

from fastapi import APIRouter

from .dependencies import Csrf, CurrentUser, Db
from .schemas import (
    TelegramCodeRequest,
    TelegramPasswordRequest,
    TelegramPhoneRequest,
)
from .telegram_auth import begin_login, live_status, verify_code, verify_two_factor


router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.get("/status")
async def status(user: CurrentUser, db: Db) -> dict[str, object]:
    return await live_status(db, user)


@router.post("/login/start")
async def start_login(
    payload: TelegramPhoneRequest, user: CurrentUser, db: Db, _: Csrf
) -> dict[str, object]:
    return await begin_login(db, user, payload.phone)


@router.post("/login/code")
async def submit_code(
    payload: TelegramCodeRequest, user: CurrentUser, db: Db, _: Csrf
) -> dict[str, object]:
    return await verify_code(db, user, payload.code)


@router.post("/login/password")
async def submit_password(
    payload: TelegramPasswordRequest, user: CurrentUser, db: Db, _: Csrf
) -> dict[str, object]:
    return await verify_two_factor(db, user, payload.password)

