from __future__ import annotations

import asyncio
import logging
import random
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.dialects.mysql import insert as mysql_insert
from telethon import TelegramClient, errors, types

from .chat_identity import chat_display_title
from .config import get_settings
from .db import SessionLocal
from .entity_service import discover_entity
from .models import (
    ChatBackupRule,
    ChatBackupState,
    HistoryUpdateState,
    TelegramAccount,
    TelegramDialog,
)
from .realtime import realtime_hub


logger = logging.getLogger(__name__)
settings = get_settings()


class TelegramAuthorizationError(Exception):
    """The persisted Telegram authorization can no longer be used."""


class TelegramConnectionUnavailable(Exception):
    """The account runtime did not become ready within the allowed time."""


def is_authorization_error(exc: BaseException) -> bool:
    return isinstance(exc, (errors.UnauthorizedError, errors.AuthKeyError)) or type(
        exc
    ).__name__ in {
        "AuthKeyDuplicatedError",
        "AuthKeyUnregisteredError",
        "SessionExpiredError",
        "SessionRevokedError",
        "UserDeactivatedBanError",
        "UserDeactivatedError",
    }


def create_client(
    session_stem: str | Path,
    *,
    receive_updates: bool = True,
) -> TelegramClient:
    return TelegramClient(
        str(session_stem),
        settings.telegram_api_id,
        settings.telegram_api_hash,
        proxy=settings.telegram_proxy(),
        auto_reconnect=True,
        connection_retries=5,
        request_retries=5,
        flood_sleep_threshold=60,
        receive_updates=receive_updates,
    )


def account_session_stem(accounts_root: Path, user_id: int) -> Path:
    """Derive an account's session location from its owning user."""
    return (accounts_root / f"user_{user_id}" / "telegram").resolve()


def candidate_session_stem(
    accounts_root: Path, user_id: int, candidate_key: str
) -> Path:
    """Resolve a validated login candidate key below the pending directory."""
    if not re.fullmatch(rf"user_{user_id}_[0-9a-f]{{32}}", candidate_key):
        raise ValueError("invalid Telegram login candidate key")
    return (accounts_root / "pending" / candidate_key).resolve()


def dialog_kind(entity: Any) -> str:
    if isinstance(entity, types.User):
        return "bot" if entity.bot else "private"
    if isinstance(entity, types.Channel):
        return "channel" if entity.broadcast else "supergroup"
    if isinstance(entity, types.Chat):
        return "group"
    return "unknown"


def media_stream_limit(is_premium: bool) -> int:
    """Return the number of concurrent streams on the primary connection."""
    configured = (
        settings.telegram_media_parallel_connections_premium
        if is_premium
        else settings.telegram_media_parallel_connections_regular
    )
    return max(1, min(configured, 6))


@dataclass(slots=True)
class AccountRuntime:
    account_id: int
    user_id: int
    telegram_user_id: int
    session_stem: Path
    client: TelegramClient
    state: str = "connecting"
    error: str | None = None
    connected_at: datetime | None = None
    is_premium: bool = False
    last_dialog_refresh_at: datetime | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    operation_slots: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(
            settings.telegram_account_operation_concurrency
        )
    )
    refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    supervisor_task: asyncio.Task[None] | None = None
    dialog_task: asyncio.Task[None] | None = None
    media_download_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    media_concurrency_disabled: bool = False

    def snapshot(self) -> dict[str, object]:
        return {
            "telegram_account_id": self.account_id,
            "connection": self.state,
            "connection_error": self.error,
            "connected_at": self.connected_at.isoformat() if self.connected_at else None,
            "is_premium": self.is_premium,
            "media_download_streams": media_stream_limit(self.is_premium),
            "last_dialog_refresh_at": (
                self.last_dialog_refresh_at.isoformat()
                if self.last_dialog_refresh_at
                else None
            ),
        }


class TelegramRuntimeManager:
    def __init__(self) -> None:
        self._runtimes: dict[int, AccountRuntime] = {}
        self._user_accounts: dict[int, int] = {}
        self._lock = asyncio.Lock()
        self._started = False
        self._stopping = False
        self._instance_lock_connection: AsyncConnection | None = None
        self._instance_lock_engine: AsyncEngine | None = None

    async def start(self) -> None:
        if self._started:
            return
        # GET_LOCK must live for the whole process. Keep that permanent
        # connection outside the request/worker pool so it cannot consume one
        # of the web application's scarce reusable slots.
        lock_engine = create_async_engine(
            settings.database_url,
            poolclass=NullPool,
            pool_pre_ping=True,
        )
        lock_connection = await lock_engine.connect()
        acquired = await lock_connection.scalar(
            text("SELECT GET_LOCK(:name, 0)"),
            {"name": f"tg_backup_runtime:{settings.mysql_database}"},
        )
        if acquired != 1:
            await lock_connection.close()
            await lock_engine.dispose()
            raise RuntimeError(
                "已有 tgBackup 进程持有 Telegram 账号运行时；请使用单进程 Uvicorn"
            )
        self._instance_lock_connection = lock_connection
        self._instance_lock_engine = lock_engine
        self._started = True
        self._stopping = False
        try:
            async with SessionLocal() as db:
                account_ids = list(await db.scalars(select(TelegramAccount.id)))
            # Account recovery must not delay the web server becoming available.
            for account_id in account_ids:
                await self.start_account(account_id)
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        if not self._started:
            return
        self._stopping = True
        async with self._lock:
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()
            self._user_accounts.clear()
        await asyncio.gather(
            *(self._stop_runtime(runtime) for runtime in runtimes),
            return_exceptions=True,
        )
        if self._instance_lock_connection:
            try:
                await self._instance_lock_connection.execute(
                    text("SELECT RELEASE_LOCK(:name)"),
                    {"name": f"tg_backup_runtime:{settings.mysql_database}"},
                )
            finally:
                await self._instance_lock_connection.close()
                self._instance_lock_connection = None
        if self._instance_lock_engine:
            await self._instance_lock_engine.dispose()
            self._instance_lock_engine = None
        self._started = False

    async def start_account(self, account_id: int) -> None:
        async with SessionLocal() as db:
            account = await db.get(TelegramAccount, account_id)
            if account is None:
                return
            session_stem = account_session_stem(
                settings.account_sessions_root,
                account.user_id,
            )
            session_stem.parent.mkdir(parents=True, exist_ok=True)
            values = (
                account.id,
                account.user_id,
                account.telegram_user_id,
                session_stem,
            )

        previous: AccountRuntime | None
        async with self._lock:
            previous = self._runtimes.pop(account_id, None)
            if previous:
                self._user_accounts.pop(previous.user_id, None)
        if previous:
            await self._stop_runtime(previous)

        runtime = AccountRuntime(
            account_id=values[0],
            user_id=values[1],
            telegram_user_id=values[2],
            session_stem=values[3],
            client=create_client(values[3]),
        )
        async with self._lock:
            if self._stopping:
                await runtime.client.disconnect()
                return
            self._runtimes[account_id] = runtime
            self._user_accounts[runtime.user_id] = account_id
        runtime.supervisor_task = asyncio.create_task(
            self._supervise(runtime),
            name=f"tg-account-{account_id}-connection",
        )
        runtime.dialog_task = asyncio.create_task(
            self._dialog_refresh_loop(runtime),
            name=f"tg-account-{account_id}-dialogs",
        )
        await self._publish_runtime(runtime)

    async def restart_for_user(self, user_id: int) -> None:
        async with SessionLocal() as db:
            account_id = await db.scalar(
                select(TelegramAccount.id).where(TelegramAccount.user_id == user_id)
            )
        if account_id is not None:
            await self.start_account(account_id)

    async def stop_for_user(self, user_id: int) -> None:
        async with self._lock:
            account_id = self._user_accounts.pop(user_id, None)
            runtime = self._runtimes.pop(account_id, None) if account_id else None
        if runtime:
            await self._stop_runtime(runtime)

    async def _stop_runtime(self, runtime: AccountRuntime) -> None:
        runtime.stop.set()
        runtime.ready.clear()
        tasks = [
            task
            for task in (runtime.dialog_task, runtime.supervisor_task)
            if task is not None and task is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await runtime.client.disconnect()
        except Exception:
            logger.exception("Failed to disconnect Telegram account %s", runtime.account_id)
        runtime.state = "stopped"
        await self._publish_runtime(runtime)

    async def _publish_runtime(self, runtime: AccountRuntime) -> None:
        await realtime_hub.publish(
            runtime.user_id,
            "telegram.runtime.changed",
            runtime.snapshot(),
        )

    async def _supervise(self, runtime: AccountRuntime) -> None:
        attempt = 0
        try:
            while not runtime.stop.is_set():
                runtime.ready.clear()
                runtime.state = "connecting" if attempt == 0 else "reconnecting"
                runtime.error = None
                await self._publish_runtime(runtime)
                try:
                    if not runtime.session_stem.with_suffix(".session").exists():
                        await self._mark_invalid(
                            runtime, "login_required", "Telegram Session 文件不存在"
                        )
                        return
                    await runtime.client.connect()
                    if not await runtime.client.is_user_authorized():
                        await self._mark_invalid(
                            runtime, "login_required", "Telegram 登录凭据已失效"
                        )
                        return
                    me = await runtime.client.get_me()
                    if int(me.id) != runtime.telegram_user_id:
                        await self._mark_invalid(
                            runtime,
                            "identity_mismatch",
                            "Telegram Session 与已绑定账号身份不一致",
                        )
                        return
                    now = datetime.now(timezone.utc)
                    runtime.state = "connected"
                    runtime.is_premium = bool(getattr(me, "premium", False))
                    runtime.connected_at = now
                    runtime.error = None
                    attempt = 0
                    await self._mark_active(runtime, me, now)
                    runtime.ready.set()
                    await self._publish_runtime(runtime)
                    await runtime.client.disconnected
                    runtime.ready.clear()
                    if runtime.stop.is_set() or runtime.state in {
                        "login_required",
                        "identity_mismatch",
                    }:
                        return
                    runtime.state = "reconnecting"
                    runtime.error = "连接已中断"
                    await self._publish_runtime(runtime)
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:
                    runtime.ready.clear()
                    if is_authorization_error(exc):
                        await self._mark_invalid(runtime, "login_required", type(exc).__name__)
                        return
                    runtime.state = "reconnecting"
                    runtime.error = type(exc).__name__
                    await self._publish_runtime(runtime)
                    logger.warning(
                        "Telegram account %s connection failed: %s",
                        runtime.account_id,
                        type(exc).__name__,
                    )
                if runtime.stop.is_set():
                    return
                attempt += 1
                delay = min(2 ** min(attempt, 6), 60) + random.uniform(0, 1)
                try:
                    await asyncio.wait_for(runtime.stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
        finally:
            runtime.ready.clear()
            if runtime.client.is_connected():
                await runtime.client.disconnect()

    async def _mark_active(self, runtime: AccountRuntime, me: Any, now: datetime) -> None:
        display_name = " ".join(
            value
            for value in (getattr(me, "first_name", None), getattr(me, "last_name", None))
            if value
        ) or str(runtime.telegram_user_id)
        async with SessionLocal.begin() as db:
            account = await db.get(TelegramAccount, runtime.account_id)
            if account:
                account.status = "active"
                account.username = getattr(me, "username", None)
                account.display_name = display_name
                account.last_checked_at = now
            rule_ids = list(
                await db.scalars(
                    select(ChatBackupRule.id).where(
                        ChatBackupRule.telegram_account_id == runtime.account_id
                    )
                )
            )
            if rule_ids:
                await db.execute(
                    update(ChatBackupState)
                    .where(
                        ChatBackupState.rule_id.in_(rule_ids),
                        ChatBackupState.status == "paused",
                        ChatBackupState.last_error_code == "telegram_auth_invalid",
                    )
                    .values(status="error", retry_after_at=now)
                )
                await db.execute(
                    update(HistoryUpdateState)
                    .where(
                        HistoryUpdateState.rule_id.in_(rule_ids),
                        HistoryUpdateState.status == "paused",
                        HistoryUpdateState.last_error_code == "telegram_auth_invalid",
                    )
                    .values(status="error", next_run_at=now)
                )

    async def _mark_invalid(
        self,
        runtime: AccountRuntime,
        state: str,
        detail: str,
    ) -> None:
        runtime.ready.clear()
        runtime.state = state
        runtime.error = detail
        async with SessionLocal.begin() as db:
            account = await db.get(TelegramAccount, runtime.account_id)
            if account:
                account.status = state
                account.last_checked_at = datetime.now(timezone.utc)
            rule_ids = list(
                await db.scalars(
                    select(ChatBackupRule.id).where(
                        ChatBackupRule.telegram_account_id == runtime.account_id
                    )
                )
            )
            if rule_ids:
                await db.execute(
                    update(ChatBackupState)
                    .where(ChatBackupState.rule_id.in_(rule_ids))
                    .values(
                        status="paused",
                        retry_after_at=None,
                        last_error_code="telegram_auth_invalid",
                        last_error=detail[:4000],
                    )
                )
                await db.execute(
                    update(HistoryUpdateState)
                    .where(HistoryUpdateState.rule_id.in_(rule_ids))
                    .values(
                        status="paused",
                        next_run_at=None,
                        last_error_code="telegram_auth_invalid",
                        last_error=detail[:4000],
                    )
                )
        if runtime.client.is_connected():
            await runtime.client.disconnect()
        await self._publish_runtime(runtime)

    async def invalidate_account(self, account_id: int, detail: str) -> None:
        runtime = self._runtimes.get(account_id)
        if runtime:
            await self._mark_invalid(runtime, "login_required", detail)

    async def ensure_account(self, account_id: int) -> AccountRuntime:
        runtime = self._runtimes.get(account_id)
        if runtime:
            return runtime
        await self.start_account(account_id)
        runtime = self._runtimes.get(account_id)
        if runtime is None:
            raise TelegramAuthorizationError("Telegram 账号不存在")
        return runtime

    async def snapshot_for_user(self, user_id: int) -> dict[str, object] | None:
        account_id = self._user_accounts.get(user_id)
        if account_id is None:
            async with SessionLocal() as db:
                account_id = await db.scalar(
                    select(TelegramAccount.id).where(TelegramAccount.user_id == user_id)
                )
            if account_id is None:
                return None
            runtime = await self.ensure_account(account_id)
        else:
            runtime = self._runtimes.get(account_id)
            if runtime is None:
                return None
        return runtime.snapshot()

    async def _wait_ready(self, runtime: AccountRuntime, timeout: float | None) -> None:
        if runtime.state in {"login_required", "identity_mismatch"}:
            raise TelegramAuthorizationError(runtime.error or "Telegram 登录凭据已失效")
        try:
            async with asyncio.timeout(
                timeout or settings.telegram_runtime_ready_timeout_seconds
            ):
                while not runtime.ready.is_set():
                    if runtime.state in {"login_required", "identity_mismatch"}:
                        raise TelegramAuthorizationError(
                            runtime.error or "Telegram 登录凭据已失效"
                        )
                    await asyncio.sleep(0.1)
        except TimeoutError as exc:
            raise TelegramConnectionUnavailable(
                runtime.error or "等待 Telegram 连接超时"
            ) from exc

    @asynccontextmanager
    async def client(
        self,
        account_id: int,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[TelegramClient]:
        runtime = await self.ensure_account(account_id)
        await self._wait_ready(runtime, timeout)
        slot_timeout = timeout or settings.telegram_runtime_ready_timeout_seconds
        acquired = False
        try:
            try:
                async with asyncio.timeout(slot_timeout):
                    await runtime.operation_slots.acquire()
                acquired = True
            except TimeoutError as exc:
                raise TelegramConnectionUnavailable(
                    "等待 Telegram 客户端空闲超时"
                ) from exc
            await self._wait_ready(runtime, timeout)
            try:
                yield runtime.client
            except BaseException as exc:
                if is_authorization_error(exc):
                    await self._mark_invalid(
                        runtime, "login_required", type(exc).__name__
                    )
                raise
        finally:
            if acquired:
                runtime.operation_slots.release()

    async def disable_parallel_media(self, account_id: int) -> None:
        """Disable concurrent media streams until the account runtime restarts."""
        runtime = await self.ensure_account(account_id)
        runtime.media_concurrency_disabled = True

    @asynccontextmanager
    async def media_download_slot(
        self,
        account_id: int,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[int]:
        """Cap an account to one ranged transfer on its primary client."""
        runtime = await self.ensure_account(account_id)
        await self._wait_ready(runtime, timeout)
        async with runtime.media_download_lock:
            yield (
                1
                if runtime.media_concurrency_disabled
                else media_stream_limit(runtime.is_premium)
            )

    async def refresh_dialogs(
        self,
        account_id: int,
        *,
        timeout: float | None = None,
    ) -> int:
        runtime = await self.ensure_account(account_id)
        async with runtime.refresh_lock:
            await self._wait_ready(runtime, timeout)
            async with runtime.operation_slots:
                await self._wait_ready(runtime, timeout)
                now = datetime.now(timezone.utc)
                discovered_dialogs: list[tuple[dict[str, object], object]] = []
                try:
                    async with asyncio.timeout(
                        timeout or settings.telegram_runtime_ready_timeout_seconds
                    ):
                        async for dialog in runtime.client.iter_dialogs(
                            limit=settings.telegram_dialog_refresh_limit
                        ):
                            entity = dialog.entity
                            peer_id = await runtime.client.get_peer_id(entity)
                            discovered_dialogs.append(
                                ({
                                    "telegram_account_id": runtime.account_id,
                                    "peer_id": peer_id,
                                    "title": chat_display_title(
                                        runtime.telegram_user_id,
                                        peer_id,
                                        dialog.name or str(peer_id),
                                    ),
                                    "username": getattr(entity, "username", None),
                                    "kind": dialog_kind(entity),
                                    "archived": bool(dialog.archived),
                                    "unread_count": dialog.unread_count or 0,
                                    "unread_mentions_count": dialog.unread_mentions_count or 0,
                                    "last_message_date": dialog.date,
                                    "is_available": True,
                                    "last_synced_at": now,
                                }, entity)
                            )
                except BaseException as exc:
                    if is_authorization_error(exc):
                        await self._mark_invalid(
                            runtime, "login_required", type(exc).__name__
                        )
                    raise

                if discovered_dialogs:
                    async with SessionLocal.begin() as db:
                        rows: list[dict[str, object]] = []
                        for row, entity in discovered_dialogs:
                            stored, _ = await discover_entity(
                                db,
                                runtime.account_id,
                                entity,
                                source="dialog_refresh",
                                priority=70,
                            )
                            row["entity_id"] = stored.id
                            rows.append(row)
                        statement = mysql_insert(TelegramDialog).values(rows)
                        statement = statement.on_duplicate_key_update(
                            entity_id=statement.inserted.entity_id,
                            title=statement.inserted.title,
                            username=statement.inserted.username,
                            kind=statement.inserted.kind,
                            archived=statement.inserted.archived,
                            unread_count=statement.inserted.unread_count,
                            unread_mentions_count=statement.inserted.unread_mentions_count,
                            last_message_date=statement.inserted.last_message_date,
                            is_available=True,
                            last_synced_at=statement.inserted.last_synced_at,
                        )
                        await db.execute(statement)
                runtime.last_dialog_refresh_at = now
                await self._publish_runtime(runtime)
                return len(discovered_dialogs)

    async def _dialog_refresh_loop(self, runtime: AccountRuntime) -> None:
        while not runtime.stop.is_set():
            try:
                await self.refresh_dialogs(runtime.account_id)
            except asyncio.CancelledError:
                raise
            except TelegramAuthorizationError:
                return
            except Exception as exc:
                logger.warning(
                    "Telegram account %s dialog refresh failed: %s",
                    runtime.account_id,
                    type(exc).__name__,
                )
            try:
                await asyncio.wait_for(
                    runtime.stop.wait(),
                    timeout=settings.telegram_dialog_refresh_seconds,
                )
            except TimeoutError:
                pass


runtime_manager = TelegramRuntimeManager()
