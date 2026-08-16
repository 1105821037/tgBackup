from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from .backup_service import (
    PipelineFailure,
    backup_rule,
    ensure_state,
    expire_stalled_run,
    utcnow,
)
from .config import get_settings
from .db import SessionLocal
from .history_update_service import (
    ensure_history_state,
    history_update_rule,
)
from .models import (
    BackupRun,
    ChatBackupRule,
    ChatBackupState,
    HistoryUpdateRun,
    HistoryUpdateState,
    TelegramAccount,
)
from .schedule_utils import due_schedule_slot


settings = get_settings()
logger = logging.getLogger(__name__)


def due_schedule_key(rule: ChatBackupRule, now: datetime | None = None) -> str | None:
    local_now = now or datetime.now().astimezone()
    if not rule.enabled or rule.removed_at is not None:
        return None
    return due_schedule_slot(
        rule.schedule_kind,
        rule.backup_time,
        rule.weekdays,
        rule.cron_expression,
        local_now,
    )


def due_history_schedule_key(
    rule: ChatBackupRule, now: datetime | None = None
) -> str | None:
    local_now = now or datetime.now().astimezone()
    if not rule.enabled or not rule.history_enabled or rule.removed_at is not None:
        return None
    return due_schedule_slot(
        rule.history_schedule_kind,
        rule.history_time,
        rule.history_weekdays,
        rule.history_cron_expression,
        local_now,
    )


class BackupCoordinator:
    def __init__(self) -> None:
        self._active_rule_ids: set[int] = set()
        self._active_history_rule_ids: set[int] = set()
        self._history_tasks: dict[int, asyncio.Task[None]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._stop = asyncio.Event()
        self._scheduler_task: asyncio.Task[None] | None = None

    async def recover_interrupted(self) -> None:
        now = utcnow()
        async with SessionLocal.begin() as db:
            await db.execute(
                update(BackupRun)
                .where(BackupRun.status == "running")
                .values(
                    status="interrupted",
                    error_code="process_restarted",
                    error_message="服务重启，任务将从已提交游标恢复",
                    finished_at=now,
                )
            )
            await db.execute(
                update(HistoryUpdateRun)
                .where(HistoryUpdateRun.status == "running")
                .values(
                    status="interrupted",
                    error_code="process_restarted",
                    error_message="服务重启，未完成的消息将在下次巡检重试",
                    finished_at=now,
                )
            )
            await db.execute(
                update(HistoryUpdateState)
                .where(HistoryUpdateState.status == "running")
                .values(
                    status="error",
                    last_error_code="process_restarted",
                    last_error="服务重启，等待继续历史消息更新",
                    next_run_at=now,
                )
            )
            await db.execute(
                update(ChatBackupState)
                .where(ChatBackupState.status == "running")
                .values(
                    status="error",
                    last_error_code="process_restarted",
                    last_error="服务重启，等待从已提交游标恢复",
                    retry_after_at=now,
                )
            )

    async def start(self) -> None:
        await self.recover_interrupted()
        self._stop.clear()
        self._scheduler_task = asyncio.create_task(
            self._scheduler_loop(), name="tg-backup-scheduler"
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._scheduler_task:
            self._scheduler_task.cancel()
            await asyncio.gather(self._scheduler_task, return_exceptions=True)
            self._scheduler_task = None
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._history_tasks.clear()

    def launch(
        self,
        rule_id: int,
        trigger: str,
        schedule_key: str | None = None,
    ) -> bool:
        if rule_id in self._active_rule_ids:
            return False
        history_task = self._history_tasks.get(rule_id)
        if history_task:
            history_task.cancel()
        self._active_rule_ids.add(rule_id)
        task = asyncio.create_task(
            self._run(rule_id, trigger, schedule_key),
            name=f"tg-backup-rule-{rule_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    def launch_history(
        self, rule_id: int, trigger: str, schedule_key: str | None = None
    ) -> bool:
        if rule_id in self._active_rule_ids or rule_id in self._active_history_rule_ids:
            return False
        self._active_history_rule_ids.add(rule_id)
        task = asyncio.create_task(
            self._run_history(rule_id, trigger, schedule_key),
            name=f"tg-history-update-rule-{rule_id}",
        )
        self._tasks.add(task)
        self._history_tasks[rule_id] = task
        task.add_done_callback(self._tasks.discard)
        return True

    async def _run(self, rule_id: int, trigger: str, schedule_key: str | None) -> None:
        activity = asyncio.Event()
        pipeline = asyncio.create_task(
            backup_rule(rule_id, trigger, schedule_key, activity.set),
            name=f"tg-backup-pipeline-{rule_id}",
        )
        activity_waiter: asyncio.Task[bool] | None = None
        deadline_waiter: asyncio.Task[None] | None = None
        try:
            while not pipeline.done():
                activity.clear()
                activity_waiter = asyncio.create_task(activity.wait())
                deadline_waiter = asyncio.create_task(
                    asyncio.sleep(settings.backup_stage_timeout_seconds)
                )
                done, _ = await asyncio.wait(
                    {pipeline, activity_waiter, deadline_waiter},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not activity_waiter.done():
                    activity_waiter.cancel()
                if pipeline in done:
                    deadline_waiter.cancel()
                    await pipeline
                    break
                if activity_waiter in done:
                    deadline_waiter.cancel()
                    continue
                if deadline_waiter not in done:
                    deadline_waiter.cancel()
                    continue
                detail = (
                    f"备份阶段超过 {settings.backup_stage_timeout_seconds} 秒没有进展，"
                    "已终止并保留最后成功游标"
                )
                logger.warning(
                    "Backup watchdog for rule %s expiring stalled run", rule_id
                )
                await expire_stalled_run(rule_id, detail)
                logger.warning(
                    "Backup watchdog for rule %s persisted expiration", rule_id
                )
                pipeline.cancel()
                break
        except (PipelineFailure, asyncio.CancelledError):
            pass
        finally:
            # Cancelling this coordinator task does not automatically cancel the
            # pipeline and watchdog waiters it created.  Always join them before
            # shutdown continues to the Telegram clients; otherwise Telethon's
            # connection loops can outlive the application event loop.
            child_tasks = [
                task
                for task in (activity_waiter, deadline_waiter, pipeline)
                if task is not None
            ]
            for task in child_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*child_tasks, return_exceptions=True)
            self._active_rule_ids.discard(rule_id)

    async def _run_history(
        self, rule_id: int, trigger: str, schedule_key: str | None
    ) -> None:
        try:
            await history_update_rule(rule_id, trigger, schedule_key)
        except (PipelineFailure, asyncio.CancelledError):
            pass
        finally:
            self._active_history_rule_ids.discard(rule_id)
            self._history_tasks.pop(rule_id, None)

    async def _scheduler_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A scheduler query failure must not stop future backup checks.
                pass
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=settings.backup_scheduler_interval_seconds,
                )
            except TimeoutError:
                pass

    async def tick(self) -> None:
        local_now = datetime.now().astimezone()
        now_utc = datetime.now(timezone.utc)
        async with SessionLocal() as db:
            rules = (
                await db.scalars(
                    select(ChatBackupRule)
                    .join(
                        TelegramAccount,
                        TelegramAccount.id == ChatBackupRule.telegram_account_id,
                    )
                    .where(
                        ChatBackupRule.enabled.is_(True),
                        ChatBackupRule.removed_at.is_(None),
                        TelegramAccount.status == "active",
                    )
                )
            ).all()

        for rule in rules:
            state = await ensure_state(rule.id)
            if state.status == "paused":
                continue
            if state.status == "error":
                if (
                    state.retry_after_at is not None
                    and state.retry_after_at.replace(tzinfo=timezone.utc) <= now_utc
                ):
                    self.launch(rule.id, "retry")
                continue
            key = due_schedule_key(rule, local_now)
            if key and state.last_schedule_key != key:
                self.launch(rule.id, "scheduled", key)

        for rule in rules:
            if not rule.history_enabled or rule.id in self._active_rule_ids:
                continue
            history_state = await ensure_history_state(rule.id)
            if history_state.status == "paused":
                continue
            continuation_due = history_state.next_run_at is not None and (
                history_state.next_run_at.replace(tzinfo=timezone.utc) <= now_utc
            )
            if continuation_due:
                self.launch_history(
                    rule.id,
                    "retry" if history_state.status == "error" else "scheduled",
                )
                continue
            key = due_history_schedule_key(rule, local_now)
            if key and history_state.last_schedule_key != key:
                self.launch_history(rule.id, "scheduled", key)


coordinator = BackupCoordinator()
