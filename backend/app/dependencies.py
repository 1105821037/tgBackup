from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .models import User, WebSession
from .security import CSRF_COOKIE, SESSION_COOKIE, digest


Db = Annotated[AsyncSession, Depends(get_db)]


async def get_session_record(
    db: Db,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> WebSession:
    if not session_token:
        raise HTTPException(status_code=401, detail="请先登录")
    record = await db.scalar(
        select(WebSession).where(WebSession.token_hash == digest(session_token))
    )
    now = datetime.now(timezone.utc)
    if not record or record.expires_at.replace(tzinfo=timezone.utc) <= now:
        raise HTTPException(status_code=401, detail="登录已过期")
    return record


async def get_current_user(
    db: Db, session_record: Annotated[WebSession, Depends(get_session_record)]
) -> User:
    user = await db.get(User, session_record.user_id)
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_csrf(
    session_record: Annotated[WebSession, Depends(get_session_record)],
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    if not csrf_cookie or not csrf_header:
        raise HTTPException(status_code=403, detail="缺少安全校验")
    if not secrets.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status_code=403, detail="安全校验失败")
    if not secrets.compare_digest(digest(csrf_header), session_record.csrf_hash):
        raise HTTPException(status_code=403, detail="安全校验失败")


Csrf = Annotated[None, Depends(require_csrf)]
