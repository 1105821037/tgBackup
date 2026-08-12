from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, func, select, text

from .config import get_settings
from .db import engine
from .dependencies import Csrf, CurrentUser, Db, get_session_record
from .models import (
    TelegramAccount,
    TelegramEntity,
    TelegramEntityPhoto,
    User,
    WebSession,
)
from .schemas import Credentials
from .security import (
    SlidingWindowRateLimiter,
    clear_session_cookies,
    create_web_session,
    hash_password,
    verify_password,
)


router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()
login_account_limiter = SlidingWindowRateLimiter(
    settings.auth_login_attempts_per_account,
    settings.auth_login_window_seconds,
)
login_ip_limiter = SlidingWindowRateLimiter(
    settings.auth_login_attempts_per_ip,
    settings.auth_login_window_seconds,
)


@router.get("/bootstrap")
async def bootstrap_status(db: Db) -> dict[str, bool]:
    count = await db.scalar(select(func.count(User.id)))
    return {"needs_setup": count == 0}


@router.post("/setup", status_code=201)
async def setup_owner(
    payload: Credentials, response: Response, db: Db
) -> dict[str, object]:
    lock_connection = await engine.connect()
    try:
        lock = await lock_connection.scalar(
            text("SELECT GET_LOCK('tg_backup_bootstrap', 5)")
        )
        if lock != 1:
            raise HTTPException(status_code=503, detail="初始化繁忙，请稍后重试")
        count = await db.scalar(select(func.count(User.id)))
        if count:
            raise HTTPException(status_code=409, detail="系统已经完成初始化")
        user = User(
            username=payload.username,
            password_hash=hash_password(payload.password),
            is_owner=True,
        )
        db.add(user)
        await db.flush()
        csrf = await create_web_session(db, user, response)
        return {"user": {"id": user.id, "username": user.username, "is_owner": user.is_owner}, "csrf": csrf}
    finally:
        await lock_connection.execute(
            text("SELECT RELEASE_LOCK('tg_backup_bootstrap')")
        )
        await lock_connection.close()


@router.post("/login")
async def login(
    payload: Credentials, request: Request, response: Response, db: Db
) -> dict[str, object]:
    client_ip = request.client.host if request.client else "unknown"
    account_key = f"{client_ip}:{payload.username}"
    retry_after = login_ip_limiter.consume(client_ip)
    if retry_after is None:
        retry_after = login_account_limiter.consume(account_key)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="登录尝试过于频繁，请稍后重试",
            headers={"Retry-After": str(retry_after)},
        )
    user = await db.scalar(select(User).where(User.username == payload.username))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码不正确")
    login_account_limiter.clear(account_key)
    csrf = await create_web_session(db, user, response)
    return {"user": {"id": user.id, "username": user.username, "is_owner": user.is_owner}, "csrf": csrf}


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    db: Db,
    _: Csrf,
    session_record: WebSession = Depends(get_session_record),
) -> Response:
    await db.delete(session_record)
    await db.commit()
    clear_session_cookies(response)
    return response


@router.get("/me")
async def me(user: CurrentUser, db: Db) -> dict[str, object]:
    account = await db.scalar(
        select(TelegramAccount).where(TelegramAccount.user_id == user.id)
    )
    entity = None
    avatar_photo_id = None
    if account:
        entity = await db.scalar(
            select(TelegramEntity).where(
                TelegramEntity.telegram_account_id == account.id,
                TelegramEntity.peer_id == account.telegram_user_id,
            )
        )
        if entity and entity.photo_id:
            avatar_photo_id = await db.scalar(
                select(TelegramEntityPhoto.telegram_photo_id).where(
                    TelegramEntityPhoto.entity_id == entity.id,
                    TelegramEntityPhoto.telegram_photo_id == entity.photo_id,
                    TelegramEntityPhoto.variant == "small",
                    TelegramEntityPhoto.status == "available",
                )
            )
    return {
        "id": user.id,
        "username": user.username,
        "is_owner": user.is_owner,
        "telegram": None
        if not account
        else {
            "state": account.status,
            "telegram_user_id": account.telegram_user_id,
            "display_name": account.display_name,
            "username": account.username,
            "phone_masked": account.phone_masked,
            "immutable_binding": True,
            "entity_id": entity.id if entity else None,
            "photo_id": entity.photo_id if entity else None,
            "avatar_url": (
                f"/api/entities/{entity.id}/avatar/{avatar_photo_id}/small"
                if entity and avatar_photo_id
                else None
            ),
        },
    }
