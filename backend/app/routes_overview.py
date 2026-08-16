from __future__ import annotations

import asyncio
from time import monotonic

from fastapi import APIRouter
from sqlalchemy import case, func, select

from .chat_identity import chat_display_title
from .config import get_settings
from .dependencies import CurrentUser, Db
from .models import (
    ArchivedMessage,
    BackupRun,
    ChatBackupRule,
    ChatBackupState,
    HistoryUpdateRun,
    HistoryUpdateState,
    MediaAsset,
    MessageVersion,
    TelegramAccount,
)


router = APIRouter(prefix="/api/overview", tags=["overview"])
settings = get_settings()
overview_cache: dict[int, tuple[float, dict[str, object]]] = {}
overview_cache_locks: dict[int, asyncio.Lock] = {}


def empty_overview() -> dict[str, object]:
    return {
        "account_bound": False,
        "message_count": 0,
        "media_count": 0,
        "media_size_bytes": 0,
        "archive_chat_count": 0,
        "rule_count": 0,
        "active_rule_count": 0,
        "paused_rule_count": 0,
        "running_task_count": 0,
        "attention_task_count": 0,
        "last_completed_at": None,
        "activities": [],
    }


async def build_overview(user: CurrentUser, db: Db) -> dict[str, object]:
    account = await db.scalar(
        select(TelegramAccount).where(TelegramAccount.user_id == user.id)
    )
    if account is None:
        return empty_overview()

    message_count, archive_chat_count = (
        await db.execute(
            select(
                func.count(ArchivedMessage.id),
                func.count(func.distinct(ArchivedMessage.peer_id)),
            ).where(ArchivedMessage.telegram_account_id == account.id)
        )
    ).one()

    unique_media = (
        select(
            MediaAsset.relative_path.label("relative_path"),
            func.max(MediaAsset.size_bytes).label("size_bytes"),
        )
        .join(MessageVersion, MessageVersion.id == MediaAsset.message_version_id)
        .join(ArchivedMessage, ArchivedMessage.id == MessageVersion.archived_message_id)
        .where(ArchivedMessage.telegram_account_id == account.id)
        .group_by(MediaAsset.relative_path)
        .subquery()
    )
    media_count, media_size_bytes = (
        await db.execute(
            select(
                func.count(unique_media.c.relative_path),
                func.coalesce(func.sum(unique_media.c.size_bytes), 0),
            )
        )
    ).one()

    rule_filter = (
        ChatBackupRule.user_id == user.id,
        ChatBackupRule.removed_at.is_(None),
    )
    rule_count, active_rule_count, paused_rule_count = (
        await db.execute(
            select(
                func.count(ChatBackupRule.id),
                func.coalesce(
                    func.sum(case((ChatBackupRule.enabled.is_(True), 1), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(case((ChatBackupRule.enabled.is_(False), 1), else_=0)),
                    0,
                ),
            ).where(*rule_filter)
        )
    ).one()

    backup_running = await db.scalar(
        select(func.count(ChatBackupState.id))
        .join(ChatBackupRule, ChatBackupRule.id == ChatBackupState.rule_id)
        .where(*rule_filter, ChatBackupState.status == "running")
    )
    history_running = await db.scalar(
        select(func.count(HistoryUpdateState.id))
        .join(ChatBackupRule, ChatBackupRule.id == HistoryUpdateState.rule_id)
        .where(*rule_filter, HistoryUpdateState.status == "running")
    )
    attention_statuses = ("failed", "error", "interrupted")
    backup_attention = await db.scalar(
        select(func.count(ChatBackupState.id))
        .join(ChatBackupRule, ChatBackupRule.id == ChatBackupState.rule_id)
        .where(*rule_filter, ChatBackupState.status.in_(attention_statuses))
    )
    history_attention = await db.scalar(
        select(func.count(HistoryUpdateState.id))
        .join(ChatBackupRule, ChatBackupRule.id == HistoryUpdateState.rule_id)
        .where(*rule_filter, HistoryUpdateState.status.in_(attention_statuses))
    )

    backup_latest = await db.scalar(
        select(func.max(BackupRun.finished_at))
        .join(ChatBackupRule, ChatBackupRule.id == BackupRun.rule_id)
        .where(*rule_filter, BackupRun.status.in_(("success", "partial")))
    )
    history_latest = await db.scalar(
        select(func.max(HistoryUpdateRun.finished_at))
        .join(ChatBackupRule, ChatBackupRule.id == HistoryUpdateRun.rule_id)
        .where(*rule_filter, HistoryUpdateRun.status.in_(("success", "partial")))
    )
    completed_times = [value for value in (backup_latest, history_latest) if value]

    activities: list[dict[str, object]] = []
    backup_rows = (
        await db.execute(
            select(BackupRun, ChatBackupRule)
            .join(ChatBackupRule, ChatBackupRule.id == BackupRun.rule_id)
            .where(*rule_filter)
            .order_by(BackupRun.started_at.desc())
            .limit(6)
        )
    ).all()
    for run, rule in backup_rows:
        activities.append(
            {
                "id": f"backup-{run.id}",
                "kind": "backup",
                "chat_title": chat_display_title(
                    account.telegram_user_id, rule.peer_id, rule.chat_title
                ),
                "status": run.status,
                "message_count": run.stored_count,
                "media_count": run.media_count,
                "changed_count": 0,
                "deleted_count": 0,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
            }
        )

    history_rows = (
        await db.execute(
            select(HistoryUpdateRun, ChatBackupRule)
            .join(ChatBackupRule, ChatBackupRule.id == HistoryUpdateRun.rule_id)
            .where(*rule_filter)
            .order_by(HistoryUpdateRun.started_at.desc())
            .limit(6)
        )
    ).all()
    for run, rule in history_rows:
        activities.append(
            {
                "id": f"history-{run.id}",
                "kind": "history",
                "chat_title": chat_display_title(
                    account.telegram_user_id, rule.peer_id, rule.chat_title
                ),
                "status": run.status,
                "message_count": 0,
                "media_count": run.media_completed_count,
                "changed_count": run.changed_count,
                "deleted_count": run.deleted_count,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
            }
        )

    activities.sort(key=lambda item: item["started_at"], reverse=True)
    return {
        "account_bound": True,
        "message_count": message_count or 0,
        "media_count": media_count or 0,
        "media_size_bytes": media_size_bytes or 0,
        "archive_chat_count": archive_chat_count or 0,
        "rule_count": rule_count or 0,
        "active_rule_count": active_rule_count or 0,
        "paused_rule_count": paused_rule_count or 0,
        "running_task_count": (backup_running or 0) + (history_running or 0),
        "attention_task_count": (backup_attention or 0) + (history_attention or 0),
        "last_completed_at": max(completed_times) if completed_times else None,
        "activities": activities[:6],
    }


def clear_overview_cache(user_id: int | None = None) -> None:
    if user_id is None:
        overview_cache.clear()
        return
    overview_cache.pop(user_id, None)


@router.get("")
async def overview(user: CurrentUser, db: Db) -> dict[str, object]:
    ttl = max(0.0, settings.overview_cache_seconds)
    cached = overview_cache.get(user.id)
    now = monotonic()
    if cached and now - cached[0] < ttl:
        return cached[1]

    lock = overview_cache_locks.setdefault(user.id, asyncio.Lock())
    async with lock:
        cached = overview_cache.get(user.id)
        now = monotonic()
        if cached and now - cached[0] < ttl:
            return cached[1]
        payload = await build_overview(user, db)
        if ttl > 0:
            overview_cache[user.id] = (monotonic(), payload)
        return payload
