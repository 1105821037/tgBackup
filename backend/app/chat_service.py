from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import errors

from .chat_identity import chat_display_title
from .models import (
    ChatBackupRule,
    ChatBackupState,
    TelegramAccount,
    TelegramDialog,
    TelegramEntity,
    TelegramEntityPhoto,
    User,
)
from .realtime import realtime_hub
from .schemas import ChatBackupRuleInput
from .telegram_runtime import (
    TelegramAuthorizationError,
    TelegramConnectionUnavailable,
    dialog_kind,
    runtime_manager,
)


def chat_kind(entity: Any) -> str:
    return dialog_kind(entity)


def serialize_rule(
    rule: ChatBackupRule | None,
    state: ChatBackupState | None = None,
) -> dict[str, object] | None:
    if not rule:
        return None
    return {
        "enabled": rule.enabled,
        "schedule_kind": rule.schedule_kind,
        "backup_time": rule.backup_time.strftime("%H:%M"),
        "weekdays": rule.weekdays or [],
        "cron_expression": rule.cron_expression,
        "media_types": rule.media_types or [],
        "history_enabled": rule.history_enabled,
        "history_available": bool(state and state.last_completed_at),
        "history_schedule_kind": rule.history_schedule_kind,
        "history_time": rule.history_time.strftime("%H:%M"),
        "history_weekdays": rule.history_weekdays or [],
        "history_cron_expression": rule.history_cron_expression,
        "history_max_updates": rule.history_max_updates,
        "history_start_kind": rule.history_start_kind,
        "history_start_days_ago": rule.history_start_days_ago,
        "history_end_kind": rule.history_end_kind,
        "history_end_days_ago": rule.history_end_days_ago,
        "updated_at": rule.updated_at,
    }


async def require_account(db: AsyncSession, user_id: int) -> TelegramAccount:
    account = await db.scalar(
        select(TelegramAccount).where(TelegramAccount.user_id == user_id)
    )
    if not account:
        raise HTTPException(status_code=409, detail="请先连接 Telegram")
    if account.status != "active":
        raise HTTPException(status_code=409, detail="Telegram 登录已失效，请先重新登录")
    return account


async def list_chats(
    db: AsyncSession, user: User, limit: int
) -> dict[str, object]:
    account = await require_account(db, user.id)
    rules = (
        await db.scalars(
            select(ChatBackupRule).where(
                ChatBackupRule.telegram_account_id == account.id,
                ChatBackupRule.removed_at.is_(None),
            )
        )
    ).all()
    rules_by_peer = {rule.peer_id: rule for rule in rules}
    states = (
        await db.scalars(
            select(ChatBackupState).where(
                ChatBackupState.rule_id.in_([rule.id for rule in rules])
            )
        )
    ).all() if rules else []
    states_by_rule = {state.rule_id: state for state in states}
    await db.commit()

    dialog_rows = (
        await db.execute(
            select(TelegramDialog, TelegramEntity)
            .outerjoin(TelegramEntity, TelegramEntity.id == TelegramDialog.entity_id)
            .where(
                TelegramDialog.telegram_account_id == account.id,
                TelegramDialog.is_available.is_(True),
            )
            .order_by(TelegramDialog.last_message_date.desc())
            .limit(limit)
        )
    ).all()
    if not dialog_rows:
        await db.commit()
        try:
            await runtime_manager.refresh_dialogs(account.id, timeout=45)
        except TelegramAuthorizationError as exc:
            raise HTTPException(status_code=409, detail="Telegram 登录已失效，请重新登录") from exc
        except (TelegramConnectionUnavailable, TimeoutError) as exc:
            raise HTTPException(status_code=504, detail="读取 Telegram 会话超时") from exc
        except (OSError, errors.RPCError) as exc:
            raise HTTPException(status_code=502, detail="无法读取 Telegram 会话") from exc
        dialog_rows = (
            await db.execute(
                select(TelegramDialog, TelegramEntity)
                .outerjoin(TelegramEntity, TelegramEntity.id == TelegramDialog.entity_id)
                .where(
                    TelegramDialog.telegram_account_id == account.id,
                    TelegramDialog.is_available.is_(True),
                )
                .order_by(TelegramDialog.last_message_date.desc())
                .limit(limit)
            )
        ).all()

    entity_ids = [entity.id for _, entity in dialog_rows if entity]
    avatar_photo_ids: dict[int, int] = {}
    if entity_ids:
        photo_rows = await db.execute(
            select(
                TelegramEntityPhoto.entity_id,
                TelegramEntityPhoto.telegram_photo_id,
            )
            .join(TelegramEntity, TelegramEntity.id == TelegramEntityPhoto.entity_id)
            .where(
                TelegramEntityPhoto.entity_id.in_(entity_ids),
                TelegramEntityPhoto.telegram_photo_id == TelegramEntity.photo_id,
                TelegramEntityPhoto.variant == "small",
                TelegramEntityPhoto.status == "available",
            )
        )
        avatar_photo_ids = {
            entity_id: photo_id for entity_id, photo_id in photo_rows.all()
        }

    chats: list[dict[str, object]] = []
    for dialog, entity in dialog_rows:
        rule = rules_by_peer.get(dialog.peer_id)
        is_self = dialog.peer_id == account.telegram_user_id
        avatar_photo_id = avatar_photo_ids.get(entity.id) if entity else None
        entity_title = entity.display_name if entity else None
        if entity_title == str(dialog.peer_id):
            entity_title = None
        chats.append(
            {
                "peer_id": dialog.peer_id,
                "entity_id": entity.id if entity else None,
                "entity_version": entity.current_version if entity else None,
                "photo_id": entity.photo_id if entity else None,
                "avatar_url": (
                    f"/api/entities/{entity.id}/avatar/{avatar_photo_id}/small"
                    if entity and avatar_photo_id
                    else None
                ),
                "title": chat_display_title(
                    account.telegram_user_id,
                    dialog.peer_id,
                    entity_title or dialog.title,
                ),
                "is_self": is_self,
                "username": entity.username if entity else dialog.username,
                "kind": dialog.kind,
                "archived": dialog.archived,
                "unread_count": dialog.unread_count,
                "unread_mentions_count": dialog.unread_mentions_count,
                "last_message_date": (
                    dialog.last_message_date.isoformat()
                    if dialog.last_message_date
                    else None
                ),
                "rule": serialize_rule(
                    rule,
                    states_by_rule.get(rule.id) if rule else None,
                ),
            }
        )

    return {
        "items": chats,
        "count": len(chats),
        "configured_count": len(rules),
    }


async def refresh_chats(
    db: AsyncSession, user: User
) -> dict[str, object]:
    account = await require_account(db, user.id)
    await db.commit()
    try:
        count = await runtime_manager.refresh_dialogs(account.id, timeout=45)
    except TelegramAuthorizationError as exc:
        raise HTTPException(status_code=409, detail="Telegram 登录已失效，请重新登录") from exc
    except (TelegramConnectionUnavailable, TimeoutError) as exc:
        raise HTTPException(status_code=504, detail="刷新 Telegram 会话超时") from exc
    except (OSError, errors.RPCError) as exc:
        raise HTTPException(status_code=502, detail="无法刷新 Telegram 会话") from exc
    return {"refreshed": True, "count": count}


async def resolve_chat(
    account: TelegramAccount, user_id: int, peer_id: int
) -> tuple[str, str]:
    try:
        async with runtime_manager.client(account.id, timeout=30) as client:
            async with asyncio.timeout(45):
                entity = await client.get_entity(peer_id)
                title = (
                    getattr(entity, "title", None)
                    or " ".join(
                        value
                        for value in (
                            getattr(entity, "first_name", None),
                            getattr(entity, "last_name", None),
                        )
                        if value
                    )
                    or getattr(entity, "username", None)
                    or str(peer_id)
                )
                return (
                    chat_display_title(account.telegram_user_id, peer_id, title),
                    chat_kind(entity),
                )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="确认 Telegram 会话超时") from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Telegram 会话不存在") from exc
    except TelegramAuthorizationError as exc:
        raise HTTPException(status_code=409, detail="Telegram 登录已失效") from exc
    except HTTPException:
        raise
    except (TelegramConnectionUnavailable, OSError, errors.RPCError) as exc:
        raise HTTPException(status_code=502, detail="无法确认 Telegram 会话") from exc


async def save_rule(
    db: AsyncSession,
    user: User,
    peer_id: int,
    payload: ChatBackupRuleInput,
) -> dict[str, object]:
    account = await require_account(db, user.id)
    await db.commit()
    title, kind = await resolve_chat(account, user.id, peer_id)

    rule = await db.scalar(
        select(ChatBackupRule).where(
            ChatBackupRule.telegram_account_id == account.id,
            ChatBackupRule.peer_id == peer_id,
        )
    )
    values = payload.model_dump()
    if payload.history_enabled:
        if rule is None:
            raise HTTPException(
                status_code=409,
                detail="请先完成一次自动备份，再开启历史消息更新",
            )
        state = await db.scalar(
            select(ChatBackupState).where(ChatBackupState.rule_id == rule.id)
        )
        if state is None or state.last_completed_at is None:
            raise HTTPException(
                status_code=409,
                detail="请先完成一次自动备份，再开启历史消息更新",
            )
    if not rule:
        rule = ChatBackupRule(
            user_id=user.id,
            telegram_account_id=account.id,
            peer_id=peer_id,
            chat_title=title,
            chat_kind=kind,
            **values,
        )
        db.add(rule)
    else:
        rule.removed_at = None
        rule.chat_title = title
        rule.chat_kind = kind
        for key, value in values.items():
            setattr(rule, key, value)
    await db.commit()
    await db.refresh(rule)
    state = await db.scalar(
        select(ChatBackupState).where(ChatBackupState.rule_id == rule.id)
    )
    result = {"peer_id": peer_id, "title": title, "rule": serialize_rule(rule, state)}
    await realtime_hub.publish(
        user.id,
        "telegram.rule.updated",
        {"rule_id": rule.id, "peer_id": peer_id},
    )
    return result
