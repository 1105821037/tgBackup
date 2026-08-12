from __future__ import annotations

import hashlib
import secrets
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from fastapi import Response
from pwdlib import PasswordHash
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .models import User, WebSession


SESSION_COOKIE = "tg_backup_session"
CSRF_COOKIE = "tg_backup_csrf"
password_hash = PasswordHash.recommended()


class SlidingWindowRateLimiter:
    """Small in-process limiter suitable for the required single-worker deployment."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = max(1, limit)
        self.window_seconds = max(1, window_seconds)
        self._attempts: dict[str, deque[float]] = {}
        self._consume_count = 0

    def _discard_expired_keys(self, cutoff: float) -> None:
        for key, attempts in tuple(self._attempts.items()):
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if not attempts:
                self._attempts.pop(key, None)

    def consume(self, key: str, now: float | None = None) -> int | None:
        current = time.monotonic() if now is None else now
        cutoff = current - self.window_seconds
        self._consume_count += 1
        if self._consume_count % 256 == 0:
            self._discard_expired_keys(cutoff)
        attempts = self._attempts.setdefault(key, deque())
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if len(attempts) >= self.limit:
            return max(1, int(self.window_seconds - (current - attempts[0]) + 0.999))
        attempts.append(current)
        return None

    def clear(self, key: str) -> None:
        self._attempts.pop(key, None)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    return password_hash.verify(password, encoded)


async def create_web_session(
    db: AsyncSession, user: User, response: Response
) -> str:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    expires = datetime.now(timezone.utc) + timedelta(days=settings.session_days)
    db.add(
        WebSession(
            user_id=user.id,
            token_hash=digest(token),
            csrf_hash=digest(csrf),
            expires_at=expires,
        )
    )
    await db.commit()
    max_age = settings.session_days * 24 * 60 * 60
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return csrf


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
