from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
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
from .history_update_service import history_sweep_progress
from .realtime import realtime_hub
from .schemas import ChatBackupRuleInput
from .schedule_utils import is_valid_five_field_cron, next_cron_runs, normalize_cron


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
    if not rules:
        return {"items": [], "count": 0}

    rule_ids = [rule.id for rule in rules]
    peer_ids = [rule.peer_id for rule in rules]
    account_ids = {rule.telegram_account_id for rule in rules}
    message_counts = {
        (account_id, peer_id): count
        for account_id, peer_id, count in (
            await db.execute(
                select(
                    ArchivedMessage.telegram_account_id,
                    ArchivedMessage.peer_id,
                    func.count(ArchivedMessage.id),
                )
                .where(
                    ArchivedMessage.telegram_account_id.in_(account_ids),
                    ArchivedMessage.peer_id.in_(peer_ids),
                )
                .group_by(
                    ArchivedMessage.telegram_account_id,
                    ArchivedMessage.peer_id,
                )
            )
        ).all()
    }
    unique_media = (
        select(
            ArchivedMessage.telegram_account_id.label("account_id"),
            ArchivedMessage.peer_id.label("peer_id"),
            MediaAsset.relative_path.label("relative_path"),
            func.max(MediaAsset.size_bytes).label("size_bytes"),
        )
        .join(MessageVersion, MessageVersion.id == MediaAsset.message_version_id)
        .join(ArchivedMessage, ArchivedMessage.id == MessageVersion.archived_message_id)
        .where(
            ArchivedMessage.telegram_account_id.in_(account_ids),
            ArchivedMessage.peer_id.in_(peer_ids),
        )
        .group_by(
            ArchivedMessage.telegram_account_id,
            ArchivedMessage.peer_id,
            MediaAsset.relative_path,
        )
        .subquery()
    )
    media_stats = {
        (account_id, peer_id): (count, size_bytes)
        for account_id, peer_id, count, size_bytes in (
            await db.execute(
                select(
                    unique_media.c.account_id,
                    unique_media.c.peer_id,
                    func.count(unique_media.c.relative_path),
                    func.coalesce(func.sum(unique_media.c.size_bytes), 0),
                ).group_by(unique_media.c.account_id, unique_media.c.peer_id)
            )
        ).all()
    }
    states = {
        state.rule_id: state
        for state in (
            await db.scalars(
                select(ChatBackupState).where(ChatBackupState.rule_id.in_(rule_ids))
            )
        ).all()
    }
    history_states = {
        state.rule_id: state
        for state in (
            await db.scalars(
                select(HistoryUpdateState).where(HistoryUpdateState.rule_id.in_(rule_ids))
            )
        ).all()
    }
    latest_run_ids = (
        select(BackupRun.rule_id, func.max(BackupRun.id).label("run_id"))
        .where(BackupRun.rule_id.in_(rule_ids))
        .group_by(BackupRun.rule_id)
        .subquery()
    )
    latest_runs = {
        run.rule_id: run
        for run in (
            await db.scalars(
                select(BackupRun).join(
                    latest_run_ids, latest_run_ids.c.run_id == BackupRun.id
                )
            )
        ).all()
    }
    latest_history_ids = (
        select(
            HistoryUpdateRun.rule_id,
            func.max(HistoryUpdateRun.id).label("run_id"),
        )
        .where(HistoryUpdateRun.rule_id.in_(rule_ids))
        .group_by(HistoryUpdateRun.rule_id)
        .subquery()
    )
    latest_history_runs = {
        run.rule_id: run
        for run in (
            await db.scalars(
                select(HistoryUpdateRun).join(
                    latest_history_ids,
                    latest_history_ids.c.run_id == HistoryUpdateRun.id,
                )
            )
        ).all()
    }
    dialog_rows = (
        await db.execute(
            select(TelegramDialog, TelegramEntity)
            .outerjoin(TelegramEntity, TelegramEntity.id == TelegramDialog.entity_id)
            .where(
                TelegramDialog.telegram_account_id.in_(account_ids),
                TelegramDialog.peer_id.in_(peer_ids),
            )
        )
    ).all()
    dialogs = {
        (dialog.telegram_account_id, dialog.peer_id): (dialog, entity)
        for dialog, entity in dialog_rows
    }
    entity_ids = {entity.id for _, entity in dialog_rows if entity and entity.photo_id}
    avatar_photo_ids = {
        entity_id: photo_id
        for entity_id, photo_id in (
            await db.execute(
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
        ).all()
    } if entity_ids else {}

    items: list[dict[str, object]] = []
    for rule in rules:
        key = (rule.telegram_account_id, rule.peer_id)
        state = states.get(rule.id)
        latest = latest_runs.get(rule.id)
        history_state = history_states.get(rule.id)
        latest_history = latest_history_runs.get(rule.id)
        history_progress = await history_sweep_progress(db, history_state, latest_history)
        dialog, entity = dialogs.get(key, (None, None))
        is_self = bool(account and rule.peer_id == account.telegram_user_id)
        avatar_photo_id = avatar_photo_ids.get(entity.id) if entity else None
        message_count = message_counts.get(key, 0)
        media_count, media_size_bytes = media_stats.get(key, (0, 0))
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
                "media_count": media_count,
                "media_size_bytes": media_size_bytes,
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
                    "status": history_progress["status"] if history_progress else (history_state.status if history_state else "idle"),
                    "has_remaining": history_progress["has_remaining"] if history_progress else False,
                    "next_run_at": history_state.next_run_at if history_state else None,
                    "last_completed_at": (
                        history_state.last_completed_at if history_state else None
                    ),
                    "latest_run": history_progress,
                },
            }
        )
    return {"items": items, "count": len(items)}


@router.get("/cron-preview")
async def preview_cron(
    user: CurrentUser,
    expression: str = Query(min_length=1, max_length=100),
) -> dict[str, object]:
    del user  # Authentication is required even though the preview is user-independent.
    normalized = normalize_cron(expression)
    local_now = datetime.now().astimezone()
    offset = local_now.strftime("%z")
    offset_label = f"UTC{offset[:3]}:{offset[3:]}" if offset else "服务器本地时间"
    if not is_valid_five_field_cron(normalized):
        return {
            "valid": False,
            "expression": normalized,
            "timezone": offset_label,
            "runs": [],
        }
    return {
        "valid": True,
        "expression": normalized,
        "timezone": offset_label,
        "runs": [value.isoformat() for value in next_cron_runs(normalized, local_now)],
    }


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
