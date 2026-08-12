from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from .chat_identity import chat_display_title
from .chat_service import save_rule, serialize_rule
from .dependencies import Csrf, CurrentUser, Db
from .models import (
    BackupRun,
    ArchivedMessage,
    ChatBackupRule,
    ChatBackupState,
    HistoryUpdateRun,
    HistoryUpdateState,
    MediaAsset,
    MessageVersion,
    TelegramAccount,
    TelegramDialog,
    TelegramEntity,
    TelegramEntityPhoto,
)
from .realtime import realtime_hub
from .schemas import ChatBackupRuleInput


router = APIRouter(prefix="/api/rules", tags=["rules"])


def serialize_run(run: BackupRun | None) -> dict[str, object] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "trigger": run.trigger,
        "status": run.status,
        "start_cursor": run.start_cursor,
        "end_cursor": run.end_cursor,
        "fetched_count": run.fetched_count,
        "stored_count": run.stored_count,
        "skipped_count": run.skipped_count,
        "media_count": run.media_count,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


@router.get("")
async def list_rules(user: CurrentUser, db: Db) -> dict[str, object]:
    account = await db.scalar(
        select(TelegramAccount).where(TelegramAccount.user_id == user.id)
    )
    rules = (
        await db.scalars(
            select(ChatBackupRule)
            .where(
                ChatBackupRule.user_id == user.id,
                ChatBackupRule.removed_at.is_(None),
            )
            .order_by(ChatBackupRule.updated_at.desc())
        )
    ).all()
    items: list[dict[str, object]] = []
    for rule in rules:
        message_count = await db.scalar(
            select(func.count(ArchivedMessage.id)).where(
                ArchivedMessage.telegram_account_id == rule.telegram_account_id,
                ArchivedMessage.peer_id == rule.peer_id,
            )
        )
        media_rows = (
            await db.execute(
                select(MediaAsset.relative_path, MediaAsset.size_bytes)
                .join(MessageVersion, MessageVersion.id == MediaAsset.message_version_id)
                .join(
                    ArchivedMessage,
                    ArchivedMessage.id == MessageVersion.archived_message_id,
                )
                .where(
                    ArchivedMessage.telegram_account_id == rule.telegram_account_id,
                    ArchivedMessage.peer_id == rule.peer_id,
                )
            )
        ).all()
        unique_media = {relative_path: size_bytes for relative_path, size_bytes in media_rows}
        state = await db.scalar(
            select(ChatBackupState).where(ChatBackupState.rule_id == rule.id)
        )
        latest = await db.scalar(
            select(BackupRun)
            .where(BackupRun.rule_id == rule.id)
            .order_by(BackupRun.started_at.desc())
            .limit(1)
        )
        history_state = await db.scalar(
            select(HistoryUpdateState).where(HistoryUpdateState.rule_id == rule.id)
        )
        latest_history = await db.scalar(
            select(HistoryUpdateRun)
            .where(HistoryUpdateRun.rule_id == rule.id)
            .order_by(HistoryUpdateRun.started_at.desc())
            .limit(1)
        )
        dialog_row = (
            await db.execute(
                select(TelegramDialog, TelegramEntity)
                .outerjoin(TelegramEntity, TelegramEntity.id == TelegramDialog.entity_id)
                .where(
                    TelegramDialog.telegram_account_id == rule.telegram_account_id,
                    TelegramDialog.peer_id == rule.peer_id,
                )
                .limit(1)
            )
        ).first()
        dialog, entity = dialog_row if dialog_row else (None, None)
        is_self = bool(account and rule.peer_id == account.telegram_user_id)
        avatar_photo_id = None
        if entity and entity.photo_id:
            avatar_photo_id = await db.scalar(
                select(TelegramEntityPhoto.telegram_photo_id).where(
                    TelegramEntityPhoto.entity_id == entity.id,
                    TelegramEntityPhoto.telegram_photo_id == entity.photo_id,
                    TelegramEntityPhoto.variant == "small",
                    TelegramEntityPhoto.status == "available",
                )
            )
        items.append(
            {
                "id": rule.id,
                "peer_id": rule.peer_id,
                "chat_title": chat_display_title(
                    account.telegram_user_id if account else -1,
                    rule.peer_id,
                    entity.display_name if entity and entity.display_name else rule.chat_title,
                ),
                "is_self": is_self,
                "chat_kind": dialog.kind if dialog else rule.chat_kind,
                "entity_id": entity.id if entity else None,
                "photo_id": entity.photo_id if entity else None,
                "avatar_url": (
                    f"/api/entities/{entity.id}/avatar/{avatar_photo_id}/small"
                    if entity and avatar_photo_id
                    else None
                ),
                "message_count": message_count or 0,
                "media_count": len(unique_media),
                "media_size_bytes": sum(unique_media.values()),
                "rule": serialize_rule(rule, state),
                "state": {
                    "status": state.status if state else "idle",
                    "last_message_id": state.last_message_id if state else 0,
                    "last_error_code": state.last_error_code if state else None,
                    "last_error": state.last_error if state else None,
                    "last_started_at": state.last_started_at if state else None,
                    "last_completed_at": state.last_completed_at if state else None,
                },
                "latest_run": serialize_run(latest),
                "history_update": {
                    "enabled": rule.history_enabled,
                    "status": history_state.status if history_state else "idle",
                    "last_completed_at": (
                        history_state.last_completed_at if history_state else None
                    ),
                    "latest_run": (
                        {
                            "id": latest_history.id,
                            "status": latest_history.status,
                            "candidate_count": latest_history.candidate_count,
                            "checked_count": latest_history.checked_count,
                            "changed_count": latest_history.changed_count,
                            "deleted_count": latest_history.deleted_count,
                            "media_completed_count": latest_history.media_completed_count,
                            "error_count": latest_history.error_count,
                        }
                        if latest_history
                        else None
                    ),
                },
            }
        )
    return {"items": items, "count": len(items)}


@router.put("/{peer_id}")
async def update_rule(
    peer_id: int,
    payload: ChatBackupRuleInput,
    user: CurrentUser,
    db: Db,
    _: Csrf,
) -> dict[str, object]:
    return await save_rule(db, user, peer_id, payload)


@router.delete("/{peer_id}")
async def remove_rule(
    peer_id: int,
    user: CurrentUser,
    db: Db,
    _: Csrf,
) -> dict[str, object]:
    rule = await db.scalar(
        select(ChatBackupRule).where(
            ChatBackupRule.user_id == user.id,
            ChatBackupRule.peer_id == peer_id,
            ChatBackupRule.removed_at.is_(None),
        )
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="备份规则不存在")
    state = await db.scalar(
        select(ChatBackupState).where(ChatBackupState.rule_id == rule.id)
    )
    history_state = await db.scalar(
        select(HistoryUpdateState).where(HistoryUpdateState.rule_id == rule.id)
    )
    if (state and state.status == "running") or (
        history_state and history_state.status == "running"
    ):
        raise HTTPException(status_code=409, detail="任务运行中，暂时不能移除规则")
    rule.enabled = False
    rule.history_enabled = False
    rule.removed_at = datetime.now(timezone.utc)
    await db.commit()
    await realtime_hub.publish(
        user.id,
        "telegram.rule.removed",
        {"rule_id": rule.id, "peer_id": rule.peer_id},
    )
    return {
        "removed": True,
        "peer_id": rule.peer_id,
        "archived_messages_preserved": True,
        "cursor_preserved": True,
    }
