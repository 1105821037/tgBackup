from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient, errors

from .config import get_settings
from .models import TelegramAccount, TelegramLoginAttempt, User
from .telegram_runtime import create_client, runtime_manager


settings = get_settings()


def client_for(session_stem: str | Path) -> TelegramClient:
    """Create an isolated client for the interactive login flow only."""
    return create_client(session_stem, receive_updates=False)


def safe_remove_candidate(session_stem: str) -> None:
    root = settings.account_sessions_root.resolve()
    stem = Path(session_stem).resolve()
    if not stem.is_relative_to(root):
        return
    for suffix in (".session", ".session-journal"):
        path = stem.with_suffix(suffix)
        if path.exists():
            path.unlink()


def cleanup_orphaned_candidates(user_id: int) -> None:
    pending = settings.account_sessions_root / "pending"
    if not pending.exists():
        return
    for path in pending.glob(f"user_{user_id}_*.session*"):
        stem_name = path.name.split(".session", 1)[0]
        safe_remove_candidate(str(pending / stem_name))


def mask_phone(phone: str) -> str:
    return f"{phone[:2]}••••{phone[-4:]}" if len(phone) >= 7 else "••••"


async def get_account(db: AsyncSession, user_id: int) -> TelegramAccount | None:
    return await db.scalar(
        select(TelegramAccount).where(TelegramAccount.user_id == user_id)
    )


async def live_status(db: AsyncSession, user: User) -> dict[str, object]:
    account = await get_account(db, user.id)
    if not account:
        return {"state": "unbound", "immutable_binding": True}

    session_file = Path(account.session_path).with_suffix(".session")
    if not session_file.exists():
        account.status = "login_required"
        account.last_checked_at = datetime.now(timezone.utc)
        await db.commit()
        return account_payload(account)

    await db.commit()
    payload = account_payload(account)
    snapshot = await runtime_manager.snapshot_for_user(user.id)
    if snapshot:
        payload.update(snapshot)
        if snapshot.get("connection") in {"login_required", "identity_mismatch"}:
            payload["state"] = snapshot["connection"]
    return payload


def account_payload(account: TelegramAccount) -> dict[str, object]:
    return {
        "state": account.status,
        "immutable_binding": True,
        "telegram_user_id": account.telegram_user_id,
        "username": account.username,
        "display_name": account.display_name,
        "phone_masked": account.phone_masked,
        "bound_at": account.bound_at,
        "last_checked_at": account.last_checked_at,
    }


async def begin_login(db: AsyncSession, user: User, phone: str) -> dict[str, object]:
    account = await get_account(db, user.id)
    if account and account.status == "active":
        raise HTTPException(status_code=409, detail="Telegram 当前已登录，绑定不可更换")
    if account and account.status == "identity_mismatch":
        raise HTTPException(status_code=409, detail="本地凭据身份异常，请检查服务器文件")

    previous = await db.scalar(
        select(TelegramLoginAttempt).where(TelegramLoginAttempt.user_id == user.id)
    )
    if previous:
        safe_remove_candidate(previous.candidate_session_path)
        await db.delete(previous)
        await db.commit()

    cleanup_orphaned_candidates(user.id)
    await db.commit()

    pending_dir = settings.account_sessions_root / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    candidate = pending_dir / f"user_{user.id}_{uuid.uuid4().hex}"
    client = client_for(candidate)
    try:
        async with asyncio.timeout(45):
            await client.connect()
            sent = await client.send_code_request(phone)
    except TimeoutError as exc:
        safe_remove_candidate(str(candidate))
        raise HTTPException(
            status_code=504,
            detail="Telegram 发码超时，请检查代理后重试",
        ) from exc
    except errors.PhoneNumberInvalidError as exc:
        safe_remove_candidate(str(candidate))
        raise HTTPException(status_code=422, detail="手机号格式无效") from exc
    except errors.FloodWaitError as exc:
        safe_remove_candidate(str(candidate))
        raise HTTPException(
            status_code=429, detail=f"Telegram 限流，请在 {exc.seconds} 秒后重试"
        ) from exc
    except (OSError, errors.RPCError) as exc:
        safe_remove_candidate(str(candidate))
        raise HTTPException(status_code=502, detail="无法连接 Telegram，请检查代理") from exc
    finally:
        await client.disconnect()

    attempt = TelegramLoginAttempt(
        user_id=user.id,
        candidate_session_path=str(candidate.resolve()),
        phone=phone,
        phone_code_hash=sent.phone_code_hash,
        stage="code_sent",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db.add(attempt)
    await db.commit()
    return {"stage": "code_sent", "phone_masked": mask_phone(phone)}


async def get_valid_attempt(db: AsyncSession, user_id: int) -> TelegramLoginAttempt:
    attempt = await db.scalar(
        select(TelegramLoginAttempt).where(TelegramLoginAttempt.user_id == user_id)
    )
    if not attempt:
        raise HTTPException(status_code=409, detail="请先请求 Telegram 验证码")
    expires = attempt.expires_at.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        safe_remove_candidate(attempt.candidate_session_path)
        await db.delete(attempt)
        await db.commit()
        raise HTTPException(status_code=410, detail="验证码已过期，请重新发送")
    return attempt


async def verify_code(
    db: AsyncSession, user: User, code: str
) -> dict[str, object]:
    attempt = await get_valid_attempt(db, user.id)
    await db.commit()
    client = client_for(attempt.candidate_session_path)
    try:
        async with asyncio.timeout(45):
            await client.connect()
            try:
                await client.sign_in(
                    phone=attempt.phone,
                    code=code.replace(" ", "").replace("-", ""),
                    phone_code_hash=attempt.phone_code_hash,
                )
            except errors.SessionPasswordNeededError:
                attempt.stage = "password_required"
                await db.commit()
                return {"stage": "password_required"}
            me = await client.get_me()
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Telegram 验证超时，请稍后重试") from exc
    except errors.PhoneCodeInvalidError as exc:
        raise HTTPException(status_code=422, detail="验证码不正确") from exc
    except errors.PhoneCodeExpiredError as exc:
        raise HTTPException(status_code=410, detail="验证码已过期，请重新发送") from exc
    except errors.FloodWaitError as exc:
        raise HTTPException(
            status_code=429, detail=f"Telegram 限流，请在 {exc.seconds} 秒后重试"
        ) from exc
    finally:
        await client.disconnect()
    return await finalize_binding(db, user, attempt, me)


async def verify_two_factor(
    db: AsyncSession, user: User, password: str
) -> dict[str, object]:
    attempt = await get_valid_attempt(db, user.id)
    if attempt.stage != "password_required":
        raise HTTPException(status_code=409, detail="当前登录流程不需要两步验证密码")
    await db.commit()
    client = client_for(attempt.candidate_session_path)
    try:
        async with asyncio.timeout(45):
            await client.connect()
            await client.sign_in(password=password)
            me = await client.get_me()
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Telegram 验证超时，请稍后重试") from exc
    except errors.PasswordHashInvalidError as exc:
        raise HTTPException(status_code=422, detail="两步验证密码不正确") from exc
    finally:
        await client.disconnect()
    return await finalize_binding(db, user, attempt, me)


async def finalize_binding(
    db: AsyncSession, user: User, attempt: TelegramLoginAttempt, me: object
) -> dict[str, object]:
    account = await db.scalar(
        select(TelegramAccount)
        .where(TelegramAccount.user_id == user.id)
        .with_for_update()
    )
    telegram_user_id = int(getattr(me, "id"))
    if account and account.telegram_user_id != telegram_user_id:
        safe_remove_candidate(attempt.candidate_session_path)
        await db.delete(attempt)
        await db.commit()
        raise HTTPException(
            status_code=409,
            detail="该系统用户已绑定其他 Telegram 账号，禁止换绑",
        )

    final_stem = settings.account_sessions_root / f"user_{user.id}" / "telegram"
    final_stem.parent.mkdir(parents=True, exist_ok=True)
    source = Path(attempt.candidate_session_path).with_suffix(".session")
    destination = final_stem.with_suffix(".session")
    if not source.exists():
        raise HTTPException(status_code=500, detail="Telegram session 文件未生成")
    # Windows does not reliably allow replacing an SQLite file that another
    # client still has open. Stop the old runtime before promoting the new login.
    await runtime_manager.stop_for_user(user.id)
    os.replace(source, destination)

    display_name = " ".join(
        value
        for value in (getattr(me, "first_name", None), getattr(me, "last_name", None))
        if value
    ) or str(telegram_user_id)
    if not account:
        account = TelegramAccount(
            user_id=user.id,
            telegram_user_id=telegram_user_id,
            username=getattr(me, "username", None),
            display_name=display_name,
            phone_masked=mask_phone(attempt.phone),
            session_path=str(final_stem.resolve()),
            status="active",
        )
        db.add(account)
    else:
        account.username = getattr(me, "username", None)
        account.display_name = display_name
        account.phone_masked = mask_phone(attempt.phone)
        account.session_path = str(final_stem.resolve())
        account.status = "active"
        account.last_checked_at = datetime.now(timezone.utc)
    await db.delete(attempt)
    await db.commit()
    await db.refresh(account)
    await runtime_manager.restart_for_user(user.id)
    return {"stage": "complete", "account": account_payload(account)}
