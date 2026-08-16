from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import types

from .backup_service import (
    DownloadedMedia,
    PipelineFailure,
    classify_exception,
    content_hash,
    detect_media_type,
    download_media,
    message_metadata,
    utcnow,
    volatile_metadata,
)
from .config import get_settings
from .db import SessionLocal
from .entity_service import (
    discover_message_forward_sender,
    discover_message_sender,
    require_message_sender_link,
)
from .models import (
    ArchivedMessage,
    ChatBackupRule,
    ChatBackupState,
    HistoryUpdateItemEvent,
    HistoryUpdateRun,
    HistoryUpdateState,
    MediaAsset,
    MessageMetricDaily,
    MessageVersion,
    TelegramAccount,
)
from .realtime import realtime_hub
from .telegram_runtime import runtime_manager


settings = get_settings()


async def publish_history_event(
    user_id: int,
    event_type: str,
    *,
    rule: ChatBackupRule,
    run_id: int,
    status: str,
    candidate_count: int,
    checked_count: int = 0,
    changed_count: int = 0,
    deleted_count: int = 0,
    media_completed_count: int = 0,
    error_count: int = 0,
    has_remaining: bool = False,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    await realtime_hub.publish(
        user_id,
        event_type,
        {
            "rule_id": rule.id,
            "peer_id": rule.peer_id,
            "chat_title": rule.chat_title,
            "run_id": run_id,
            "status": status,
            "candidate_count": candidate_count,
            "checked_count": checked_count,
            "changed_count": changed_count,
            "deleted_count": deleted_count,
            "media_completed_count": media_completed_count,
            "error_count": error_count,
            "has_remaining": has_remaining,
            "error_code": error_code,
            "error_message": error_message,
        },
    )


def history_range_conditions(rule: ChatBackupRule, now: datetime) -> list[Any]:
    local_now = now.astimezone()
    conditions: list[Any] = []
    if rule.history_start_kind == "days_ago":
        start_days = rule.history_start_days_ago or 0
        start_edge = (local_now - timedelta(days=start_days)).astimezone(timezone.utc)
        conditions.append(ArchivedMessage.sent_at >= start_edge)
    if rule.history_end_kind == "days_ago":
        end_days = rule.history_end_days_ago or 0
        end_edge = (local_now - timedelta(days=end_days)).astimezone(timezone.utc)
        conditions.append(ArchivedMessage.sent_at <= end_edge)
    return conditions


async def ensure_history_state(rule_id: int) -> HistoryUpdateState:
    async with SessionLocal() as db:
        state = await db.scalar(
            select(HistoryUpdateState).where(HistoryUpdateState.rule_id == rule_id)
        )
        if state is None:
            state = HistoryUpdateState(rule_id=rule_id, status="idle")
            db.add(state)
            await db.commit()
            await db.refresh(state)
        return state


async def history_sweep_progress(
    db: AsyncSession,
    state: HistoryUpdateState | None,
    latest: HistoryUpdateRun | None,
) -> dict[str, Any] | None:
    """Return progress for the whole sweep rather than only its latest chunk."""
    if latest is None:
        return None
    runs = [latest]
    if state and state.sweep_cutoff_at:
        runs = list(
            (
                await db.scalars(
                    select(HistoryUpdateRun)
                    .where(
                        HistoryUpdateRun.rule_id == latest.rule_id,
                        HistoryUpdateRun.started_at >= state.sweep_cutoff_at,
                    )
                    .order_by(HistoryUpdateRun.id)
                )
            ).all()
        ) or [latest]
    return summarize_history_sweep(runs, state)


def summarize_history_sweep(
    runs: list[HistoryUpdateRun],
    state: HistoryUpdateState | None,
) -> dict[str, Any] | None:
    if not runs:
        return None
    current = runs[-1]
    prior_checked = sum(run.checked_count for run in runs[:-1])
    has_remaining = bool(state and state.next_run_at)
    status = state.status if state and state.status in {"running", "error", "paused"} else current.status
    if has_remaining and status not in {"running", "error", "paused", "failed"}:
        status = "continuing"
    return {
        "id": current.id,
        "status": status,
        "candidate_count": prior_checked + current.candidate_count,
        "checked_count": sum(run.checked_count for run in runs),
        "changed_count": sum(run.changed_count for run in runs),
        "deleted_count": sum(run.deleted_count for run in runs),
        "media_completed_count": sum(run.media_completed_count for run in runs),
        "error_count": sum(run.error_count for run in runs),
        "has_remaining": has_remaining,
    }


def deletion_hash(message_id: int) -> str:
    return hashlib.sha256(f"deleted:{message_id}".encode()).hexdigest()


async def upsert_daily_metrics(
    db: Any,
    archived: ArchivedMessage,
    message: Any,
    now: datetime,
) -> None:
    metrics = volatile_metadata(message)
    archived.volatile_metadata_json = metrics
    sample_date = now.astimezone().date()
    metric = await db.scalar(
        select(MessageMetricDaily).where(
            MessageMetricDaily.archived_message_id == archived.id,
            MessageMetricDaily.sample_date == sample_date,
        )
    )
    replies = getattr(getattr(message, "replies", None), "replies", None)
    if metric is None:
        metric = MessageMetricDaily(
            archived_message_id=archived.id,
            sample_date=sample_date,
            views=getattr(message, "views", None),
            forwards=getattr(message, "forwards", None),
            replies=replies,
            reactions_json=metrics["reactions"],
            observed_at=now,
        )
        db.add(metric)
    else:
        metric.views = getattr(message, "views", None)
        metric.forwards = getattr(message, "forwards", None)
        metric.replies = replies
        metric.reactions_json = metrics["reactions"]
        metric.observed_at = now


async def upsert_media_asset(
    db: Any,
    version_id: int,
    downloaded: DownloadedMedia,
) -> bool:
    asset = await db.scalar(
        select(MediaAsset).where(
            MediaAsset.message_version_id == version_id,
            MediaAsset.media_type == downloaded.media_type,
        )
    )
    created = asset is None
    if asset is None:
        asset = MediaAsset(
            message_version_id=version_id,
            media_type=downloaded.media_type,
            telegram_media_id=downloaded.telegram_media_id,
            relative_path=downloaded.relative_path,
            size_bytes=downloaded.size_bytes,
            sha256=downloaded.sha256,
            mime_type=downloaded.mime_type,
            original_name=downloaded.original_name,
        )
        db.add(asset)
    else:
        asset.telegram_media_id = downloaded.telegram_media_id
        asset.relative_path = downloaded.relative_path
        asset.size_bytes = downloaded.size_bytes
        asset.sha256 = downloaded.sha256
        asset.mime_type = downloaded.mime_type
        asset.original_name = downloaded.original_name
    return created or downloaded.downloaded_now


async def inspect_remote_message(
    rule: ChatBackupRule,
    run_id: int,
    archived_id: int,
    remote: Any | None,
    downloaded: DownloadedMedia | None,
) -> tuple[bool, bool, bool]:
    now = utcnow()
    changed = False
    deleted = False
    media_completed = False
    async with SessionLocal.begin() as db:
        archived = await db.scalar(
            select(ArchivedMessage)
            .where(ArchivedMessage.id == archived_id)
            .with_for_update()
        )
        run = await db.get(HistoryUpdateRun, run_id)
        if archived is None or run is None:
            raise RuntimeError("历史消息更新记录不存在")

        missing = remote is None or isinstance(remote, types.MessageEmpty)
        if missing:
            if not archived.is_deleted and archived.history_update_count < rule.history_max_updates:
                next_version = archived.current_version + 1
                digest = deletion_hash(archived.message_id)
                previous_version = await db.scalar(
                    select(MessageVersion).where(
                        MessageVersion.archived_message_id == archived.id,
                        MessageVersion.version == archived.current_version,
                    )
                )
                if archived.sender_id is not None and (
                    previous_version is None
                    or previous_version.sender_entity_version_id is None
                ):
                    raise RuntimeError("删除版本无法继承消息发送者实体关联")
                db.add(
                    MessageVersion(
                        archived_message_id=archived.id,
                        sender_entity_version_id=(
                            previous_version.sender_entity_version_id
                            if previous_version
                            else None
                        ),
                        version=next_version,
                        content_hash=digest,
                        text=None,
                        content_kind="deleted",
                        content_json={"deleted": True},
                        edit_date=None,
                        is_deleted=True,
                        metadata_json={"deleted": True},
                        observed_at=now,
                    )
                )
                archived.current_version = next_version
                archived.current_content_hash = digest
                archived.history_update_count += 1
                archived.is_deleted = True
                changed = True
                deleted = True
        else:
            sender_entity, sender_version_id = await discover_message_sender(
                db,
                rule.telegram_account_id,
                remote,
                source="history_update",
                priority=85,
            )
            require_message_sender_link(remote, sender_entity, sender_version_id)
            await discover_message_forward_sender(
                db,
                rule.telegram_account_id,
                remote,
                source="history_forward",
                priority=85,
            )
            archived.sender_id = getattr(remote, "sender_id", None)
            if sender_entity:
                archived.sender_entity_id = sender_entity.id
            stable = message_metadata(remote, detect_media_type(remote))
            digest = content_hash(remote, stable)
            if (
                archived.current_content_hash != digest
                and archived.history_update_count < rule.history_max_updates
            ):
                next_version = archived.current_version + 1
                version = MessageVersion(
                    archived_message_id=archived.id,
                    sender_entity_version_id=sender_version_id,
                    version=next_version,
                    content_hash=digest,
                    text=getattr(remote, "message", None),
                    content_kind=str(stable.get("content_kind") or "unsupported"),
                    content_json=stable.get("content") or {},
                    edit_date=getattr(remote, "edit_date", None),
                    is_deleted=False,
                    metadata_json=stable,
                    observed_at=now,
                )
                db.add(version)
                await db.flush()
                archived.current_content_hash = digest
                archived.current_version = next_version
                archived.history_update_count += 1
                archived.is_deleted = False
                changed = True
            else:
                current = await db.scalar(
                    select(MessageVersion).where(
                        MessageVersion.archived_message_id == archived.id,
                        MessageVersion.version == archived.current_version,
                    )
                )
                if current:
                    current.content_kind = str(stable.get("content_kind") or "unsupported")
                    current.content_json = stable.get("content") or {}
                    current.metadata_json = stable
                    if sender_version_id and current.sender_entity_version_id is None:
                        current.sender_entity_version_id = sender_version_id
            archived.last_observed_at = now
            await upsert_daily_metrics(db, archived, remote, now)

            if downloaded and archived.current_content_hash == digest:
                version = await db.scalar(
                    select(MessageVersion).where(
                        MessageVersion.archived_message_id == archived.id,
                        MessageVersion.version == archived.current_version,
                    )
                )
                if version:
                    media_completed = await upsert_media_asset(db, version.id, downloaded)

        archived.last_history_checked_at = now
        run.checked_count += 1
        if changed:
            run.changed_count += 1
        if deleted:
            run.deleted_count += 1
        if media_completed:
            run.media_completed_count += 1
    return changed, deleted, media_completed


async def record_item_error(
    run_id: int,
    archived_id: int,
    message_id: int,
    failure: PipelineFailure,
) -> None:
    now = utcnow()
    async with SessionLocal.begin() as db:
        archived = await db.get(ArchivedMessage, archived_id)
        run = await db.get(HistoryUpdateRun, run_id)
        if archived:
            archived.last_history_checked_at = now
        if run:
            run.checked_count += 1
            run.error_count += 1
        db.add(
            HistoryUpdateItemEvent(
                run_id=run_id,
                archived_message_id=archived_id,
                message_id=message_id,
                code=failure.code,
                detail=failure.detail[:4000],
            )
        )


async def finish_history_run(
    rule_id: int,
    run_id: int,
    has_remaining: bool,
) -> None:
    now = utcnow()
    async with SessionLocal.begin() as db:
        state = await db.scalar(
            select(HistoryUpdateState)
            .where(HistoryUpdateState.rule_id == rule_id)
            .with_for_update()
        )
        run = await db.get(HistoryUpdateRun, run_id)
        if not state or not run:
            return
        state.status = "idle"
        state.last_completed_at = now
        state.next_run_at = now + timedelta(minutes=5) if has_remaining else None
        state.last_error_code = None
        state.last_error = None
        run.status = "partial" if run.error_count else "success"
        run.finished_at = now


async def fail_history_run(
    rule_id: int,
    run_id: int,
    failure: PipelineFailure,
) -> None:
    now = utcnow()
    async with SessionLocal.begin() as db:
        state = await db.scalar(
            select(HistoryUpdateState)
            .where(HistoryUpdateState.rule_id == rule_id)
            .with_for_update()
        )
        run = await db.get(HistoryUpdateRun, run_id)
        if state:
            state.status = "paused" if failure.action in {"pause_account", "pause_rule"} else "error"
            state.next_run_at = None if state.status == "paused" else now + timedelta(minutes=30)
            state.last_error_code = failure.code
            state.last_error = failure.detail[:4000]
        if run:
            run.status = "failed"
            run.error_code = failure.code
            run.error_message = failure.detail[:4000]
            run.finished_at = now


async def history_update_rule(
    rule_id: int,
    trigger: str = "manual",
    schedule_key: str | None = None,
) -> int:
    state = await ensure_history_state(rule_id)
    started = utcnow()
    async with SessionLocal.begin() as db:
        rule = await db.get(ChatBackupRule, rule_id)
        if rule is None:
            raise PipelineFailure("rule_missing", "备份规则不存在", "stop")
        if rule.removed_at is not None:
            raise PipelineFailure("rule_removed", "备份规则已移除", "stop")
        backup_state = await db.scalar(
            select(ChatBackupState).where(ChatBackupState.rule_id == rule_id)
        )
        if backup_state is None or backup_state.last_completed_at is None:
            raise PipelineFailure(
                "backup_not_started",
                "请先完成一次自动备份，再运行历史消息更新",
                "stop",
            )
        account = await db.get(TelegramAccount, rule.telegram_account_id)
        if account is None or account.status != "active":
            raise PipelineFailure("telegram_auth_invalid", "Telegram 账号未登录", "pause_account")
        state_row = await db.scalar(
            select(HistoryUpdateState)
            .where(HistoryUpdateState.rule_id == rule_id)
            .with_for_update()
        )
        if state_row is None:
            raise RuntimeError("历史消息更新状态不存在")
        state_row.status = "running"
        state_row.last_started_at = started
        if schedule_key:
            state_row.last_schedule_key = schedule_key
        state_row.last_error_code = None
        state_row.last_error = None
        continuing_sweep = (
            state_row.sweep_cutoff_at is not None
            and state_row.next_run_at is not None
        )
        sweep_cutoff = state_row.sweep_cutoff_at if continuing_sweep else started
        state_row.sweep_cutoff_at = sweep_cutoff
        conditions = history_range_conditions(rule, started)
        candidate_count = await db.scalar(
            select(func.count(ArchivedMessage.id)).where(
                ArchivedMessage.telegram_account_id == account.id,
                ArchivedMessage.peer_id == rule.peer_id,
                or_(
                    ArchivedMessage.last_history_checked_at.is_(None),
                    ArchivedMessage.last_history_checked_at < sweep_cutoff,
                ),
                *conditions,
            )
        )
        run = HistoryUpdateRun(
            rule_id=rule_id,
            trigger=trigger,
            status="running",
            candidate_count=candidate_count or 0,
            started_at=started,
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        previous_runs = list(
            (
                await db.scalars(
                    select(HistoryUpdateRun).where(
                        HistoryUpdateRun.rule_id == rule_id,
                        HistoryUpdateRun.id != run_id,
                        HistoryUpdateRun.started_at >= sweep_cutoff,
                    )
                )
            ).all()
        )

    processed = 0
    changed_count = 0
    deleted_count = 0
    media_completed_count = 0
    error_count = 0
    total_candidates = candidate_count or 0
    prior_checked = sum(item.checked_count for item in previous_runs)
    prior_changed = sum(item.changed_count for item in previous_runs)
    prior_deleted = sum(item.deleted_count for item in previous_runs)
    prior_media_completed = sum(item.media_completed_count for item in previous_runs)
    prior_errors = sum(item.error_count for item in previous_runs)
    sweep_candidates = prior_checked + total_candidates
    await publish_history_event(
        account.user_id,
        "telegram.history.started",
        rule=rule,
        run_id=run_id,
        status="running",
        candidate_count=sweep_candidates,
        checked_count=prior_checked,
        changed_count=prior_changed,
        deleted_count=prior_deleted,
        media_completed_count=prior_media_completed,
        error_count=prior_errors,
    )
    try:
        async with runtime_manager.client(account.id) as client:
            entity = await client.get_entity(rule.peer_id)
            while processed < settings.history_update_max_messages_per_run:
                async with SessionLocal() as db:
                    conditions = history_range_conditions(rule, started)
                    candidates = (
                        await db.scalars(
                            select(ArchivedMessage)
                            .where(
                                ArchivedMessage.telegram_account_id == account.id,
                                ArchivedMessage.peer_id == rule.peer_id,
                                or_(
                                    ArchivedMessage.last_history_checked_at.is_(None),
                                    ArchivedMessage.last_history_checked_at < sweep_cutoff,
                                ),
                                *conditions,
                            )
                            .order_by(
                                case(
                                    (ArchivedMessage.last_history_checked_at.is_(None), 0),
                                    else_=1,
                                ),
                                ArchivedMessage.last_history_checked_at,
                                ArchivedMessage.message_id,
                            )
                            .limit(
                                min(
                                    settings.history_update_batch_size,
                                    settings.history_update_max_messages_per_run - processed,
                                )
                            )
                        )
                    ).all()
                if not candidates:
                    break
                ids = [candidate.message_id for candidate in candidates]
                async with asyncio.timeout(60):
                    remotes = await client.get_messages(entity, ids=ids)
                remote_list = list(remotes)
                for index, candidate in enumerate(candidates):
                    remote = remote_list[index] if index < len(remote_list) else None
                    try:
                        downloaded = None
                        if remote is not None and not isinstance(remote, types.MessageEmpty):
                            downloaded = await download_media(
                                client,
                                remote,
                                rule.user_id,
                                rule.peer_id,
                                rule.media_types or [],
                                telegram_account_id=account.id,
                            )
                        changed, deleted, media_completed = await inspect_remote_message(
                            rule,
                            run_id,
                            candidate.id,
                            remote,
                            downloaded,
                        )
                        if changed:
                            changed_count += 1
                        if deleted:
                            deleted_count += 1
                        if media_completed:
                            media_completed_count += 1
                    except BaseException as exc:
                        failure = classify_exception(exc)
                        if failure.action in {"pause_account", "pause_rule"}:
                            raise failure
                        await record_item_error(
                            run_id,
                            candidate.id,
                            candidate.message_id,
                            failure,
                        )
                        error_count += 1
                    processed += 1
                    await publish_history_event(
                        account.user_id,
                        "telegram.history.progress",
                        rule=rule,
                        run_id=run_id,
                        status="running",
                        candidate_count=sweep_candidates,
                        checked_count=prior_checked + processed,
                        changed_count=prior_changed + changed_count,
                        deleted_count=prior_deleted + deleted_count,
                        media_completed_count=prior_media_completed + media_completed_count,
                        error_count=prior_errors + error_count,
                    )
        async with SessionLocal() as db:
            remaining = await db.scalar(
                select(func.count(ArchivedMessage.id)).where(
                    ArchivedMessage.telegram_account_id == account.id,
                    ArchivedMessage.peer_id == rule.peer_id,
                    or_(
                        ArchivedMessage.last_history_checked_at.is_(None),
                        ArchivedMessage.last_history_checked_at < sweep_cutoff,
                    ),
                    *history_range_conditions(rule, started),
                )
            )
        await finish_history_run(
            rule_id,
            run_id,
            bool(remaining),
        )
        await publish_history_event(
            account.user_id,
            "telegram.history.completed",
            rule=rule,
            run_id=run_id,
            status="continuing" if remaining else ("partial" if error_count else "success"),
            candidate_count=sweep_candidates,
            checked_count=prior_checked + processed,
            changed_count=prior_changed + changed_count,
            deleted_count=prior_deleted + deleted_count,
            media_completed_count=prior_media_completed + media_completed_count,
            error_count=prior_errors + error_count,
            has_remaining=bool(remaining),
        )
        return run_id
    except BaseException as exc:
        failure = classify_exception(exc)
        await fail_history_run(rule_id, run_id, failure)
        await publish_history_event(
            account.user_id,
            "telegram.history.failed",
            rule=rule,
            run_id=run_id,
            status="failed",
            candidate_count=sweep_candidates,
            checked_count=prior_checked + processed,
            changed_count=prior_changed + changed_count,
            deleted_count=prior_deleted + deleted_count,
            media_completed_count=prior_media_completed + media_completed_count,
            error_count=prior_errors + error_count,
            error_code=failure.code,
            error_message=failure.detail,
        )
        raise failure from exc
