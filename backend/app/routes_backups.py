from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from .backup_scheduler import coordinator
from .dependencies import Csrf, CurrentUser, Db
from .history_update_service import history_sweep_progress
from .models import (
    BackupRun,
    ChatBackupRule,
    ChatBackupState,
    HistoryUpdateRun,
    HistoryUpdateState,
)


router = APIRouter(prefix="/api/backups", tags=["backups"])


@router.get("")
async def backup_status(user: CurrentUser, db: Db) -> dict[str, object]:
    rules = (
        await db.scalars(
            select(ChatBackupRule).where(
                ChatBackupRule.user_id == user.id,
                ChatBackupRule.removed_at.is_(None),
            )
        )
    ).all()
    items: list[dict[str, object]] = []
    for rule in rules:
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
        history_progress = await history_sweep_progress(db, history_state, latest_history)
        items.append(
            {
                "peer_id": rule.peer_id,
                "chat_title": rule.chat_title,
                "enabled": rule.enabled,
                "state": (
                    {
                        "status": state.status,
                        "last_message_id": state.last_message_id,
                        "consecutive_failures": state.consecutive_failures,
                        "last_error_code": state.last_error_code,
                        "last_error": state.last_error,
                        "retry_after_at": state.retry_after_at,
                        "last_started_at": state.last_started_at,
                        "last_completed_at": state.last_completed_at,
                    }
                    if state
                    else None
                ),
                "latest_run": (
                    {
                        "id": latest.id,
                        "trigger": latest.trigger,
                        "status": latest.status,
                        "start_cursor": latest.start_cursor,
                        "end_cursor": latest.end_cursor,
                        "fetched_count": latest.fetched_count,
                        "stored_count": latest.stored_count,
                        "skipped_count": latest.skipped_count,
                        "media_count": latest.media_count,
                        "error_code": latest.error_code,
                        "error_message": latest.error_message,
                        "started_at": latest.started_at,
                        "finished_at": latest.finished_at,
                    }
                    if latest
                    else None
                ),
                "history_update": {
                    "enabled": rule.history_enabled,
                    "available": bool(state and state.last_completed_at),
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
    return {"items": items}


@router.post("/{peer_id}/run", status_code=202)
async def run_backup(
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
        raise HTTPException(status_code=404, detail="请先为该会话保存备份规则")
    launched = coordinator.launch(rule.id, "manual")
    if not launched:
        raise HTTPException(status_code=409, detail="该会话已有备份任务正在运行")
    return {"accepted": True, "rule_id": rule.id}


@router.post("/{peer_id}/history/run", status_code=202)
async def run_history_update(
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
        raise HTTPException(status_code=404, detail="请先为该会话保存备份规则")
    if not rule.history_enabled:
        raise HTTPException(status_code=409, detail="该规则未开启历史消息更新")
    state = await db.scalar(
        select(ChatBackupState).where(ChatBackupState.rule_id == rule.id)
    )
    if state is None or state.last_completed_at is None:
        raise HTTPException(status_code=409, detail="请先完成一次自动备份")
    launched = coordinator.launch_history(rule.id, "manual")
    if not launched:
        raise HTTPException(status_code=409, detail="该会话当前有任务正在运行")
    return {"accepted": True, "rule_id": rule.id}
