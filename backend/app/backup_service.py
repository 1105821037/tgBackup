from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from telethon import errors, types
from telethon.requestiter import RequestIter

from .config import get_settings
from .db import SessionLocal
from .entity_service import (
    discover_message_forward_sender,
    discover_message_sender,
    discover_message_via_bot,
    require_message_sender_link,
)
from .models import (
    ArchivedMessage,
    BackupItemEvent,
    BackupRun,
    ChatBackupRule,
    ChatBackupState,
    MediaAsset,
    MessageMetricDaily,
    MessageVersion,
    TelegramAccount,
)
from .message_content import serialize_message_content
from .media_downloader import discard_parallel_download, parallel_download_file
from .media_preview import preview_cache_path, schedule_media_preview, supports_preview
from .realtime import realtime_hub
from .telegram_runtime import (
    TelegramAuthorizationError,
    TelegramConnectionUnavailable,
    runtime_manager,
)


settings = get_settings()
logger = logging.getLogger(__name__)


def _consume_background_result(task: asyncio.Future[Any]) -> None:
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


async def await_with_hard_timeout(awaitable: Any, timeout_seconds: float) -> Any:
    """Return on the deadline even if a third-party awaitable delays cancellation."""
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
    except BaseException:
        task.cancel()
        task.add_done_callback(_consume_background_result)
        raise
    if task not in done:
        logger.warning("Telegram operation reached hard deadline after %s seconds", timeout_seconds)
        task.cancel()
        task.add_done_callback(_consume_background_result)
        raise TimeoutError(f"Telegram 操作超过 {timeout_seconds:g} 秒")
    return task.result()


async def fetch_message_batch(
    client: Any,
    entity: Any,
    cursor: int,
    limit: int,
) -> list[Any]:
    return [
        message
        async for message in client.iter_messages(
            entity,
            min_id=cursor,
            reverse=True,
            limit=limit,
        )
    ]


async def publish_backup_event(
    user_id: int,
    event_type: str,
    *,
    rule: ChatBackupRule,
    run_id: int,
    status: str,
    cursor: int,
    fetched_count: int = 0,
    stored_count: int = 0,
    skipped_count: int = 0,
    media_count: int = 0,
    error_code: str | None = None,
    error_message: str | None = None,
    current_message_id: int | None = None,
    current_media_type: str | None = None,
    retry_attempt: int | None = None,
    retry_max: int | None = None,
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
            "cursor": cursor,
            "fetched_count": fetched_count,
            "stored_count": stored_count,
            "skipped_count": skipped_count,
            "media_count": media_count,
            "error_code": error_code,
            "error_message": error_message,
            "current_message_id": current_message_id,
            "current_media_type": current_media_type,
            "retry_attempt": retry_attempt,
            "retry_max": retry_max,
        },
    )


class PipelineFailure(Exception):
    def __init__(self, code: str, detail: str, action: str = "retry") -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.action = action


@dataclass(slots=True)
class DownloadedMedia:
    media_type: str
    telegram_media_id: str | None
    relative_path: str
    size_bytes: int
    sha256: str
    mime_type: str | None
    original_name: str | None
    downloaded_now: bool


AUTH_ERROR_NAMES = {
    "AuthKeyDuplicatedError",
    "AuthKeyUnregisteredError",
    "SessionExpiredError",
    "SessionPasswordNeededError",
    "SessionRevokedError",
    "UserDeactivatedBanError",
    "UserDeactivatedError",
}
PEER_ERROR_NAMES = {
    "ChannelPrivateError",
    "ChatAdminRequiredError",
    "ChatForbiddenError",
    "UserBannedInChannelError",
}
MEDIA_TYPE_LABELS = {
    "photo": "图片",
    "video": "视频",
    "animation": "动图",
    "audio": "音频",
    "voice": "语音",
    "document": "文件",
    "sticker": "贴纸",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def classify_exception(exc: BaseException) -> PipelineFailure:
    if isinstance(exc, PipelineFailure):
        return exc
    name = type(exc).__name__
    if isinstance(exc, TelegramAuthorizationError):
        return PipelineFailure("telegram_auth_invalid", str(exc), "pause_account")
    if isinstance(exc, TelegramConnectionUnavailable):
        return PipelineFailure("network_error", str(exc), "retry")
    if name in AUTH_ERROR_NAMES:
        return PipelineFailure("telegram_auth_invalid", name, "pause_account")
    if name in PEER_ERROR_NAMES:
        return PipelineFailure("chat_inaccessible", name, "pause_rule")
    if isinstance(exc, errors.FloodWaitError):
        return PipelineFailure(
            "telegram_flood_wait",
            f"Telegram 要求等待 {exc.seconds} 秒",
            "retry",
        )
    if isinstance(exc, (OSError, TimeoutError, ConnectionError)):
        detail = f"{name}: {exc}" if str(exc) else name
        return PipelineFailure("network_error", detail, "retry")
    if isinstance(exc, errors.RPCError):
        detail = f"{name}: {exc}" if str(exc) else name
        return PipelineFailure("telegram_rpc_error", detail, "retry")
    if isinstance(exc, PipelineFailure):
        return exc
    return PipelineFailure("unexpected_error", f"{name}: {exc}", "retry")


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if hasattr(value, "to_dict"):
        return json_safe(value.to_dict())
    return str(value)


def reaction_summary(message: Any) -> list[dict[str, Any]]:
    results = getattr(getattr(message, "reactions", None), "results", None) or []
    return [
        {
            "reaction": json_safe(getattr(item, "reaction", None)),
            "count": getattr(item, "count", 0),
            "chosen_order": getattr(item, "chosen_order", None),
        }
        for item in results
    ]


def forward_metadata(message: Any) -> dict[str, Any] | None:
    forwarded = getattr(message, "fwd_from", None)
    if forwarded is None:
        return None
    forward = getattr(message, "forward", None)
    source = getattr(forward, "sender", None) or getattr(forward, "chat", None)
    source_name = None
    if source is not None:
        source_name = (
            getattr(source, "title", None)
            or " ".join(
                part
                for part in (
                    getattr(source, "first_name", None),
                    getattr(source, "last_name", None),
                )
                if part
            )
            or getattr(source, "username", None)
        )
    return {
        "name": source_name or getattr(forwarded, "from_name", None),
        "date": json_safe(getattr(forwarded, "date", None)),
        "from_id": json_safe(getattr(forwarded, "from_id", None)),
        "saved_from_peer": json_safe(getattr(forwarded, "saved_from_peer", None)),
        "saved_from_id": json_safe(getattr(forwarded, "saved_from_id", None)),
        "saved_from_name": getattr(forwarded, "saved_from_name", None),
        "channel_post": getattr(forwarded, "channel_post", None),
        "post_author": getattr(forwarded, "post_author", None),
    }


def webpage_metadata(message: Any) -> dict[str, Any] | None:
    webpage = getattr(getattr(message, "media", None), "webpage", None)
    if webpage is None or not getattr(webpage, "url", None):
        return None
    return {
        "url": getattr(webpage, "url", None),
        "display_url": getattr(webpage, "display_url", None),
        "site_name": getattr(webpage, "site_name", None),
        "title": getattr(webpage, "title", None),
        "description": getattr(webpage, "description", None),
        "type": getattr(webpage, "type", None),
        "duration": getattr(webpage, "duration", None),
    }


def inline_buttons_metadata(message: Any) -> list[list[dict[str, Any]]]:
    rows = getattr(getattr(message, "reply_markup", None), "rows", None) or []
    result: list[list[dict[str, Any]]] = []
    for row in rows:
        buttons: list[dict[str, Any]] = []
        for button in getattr(row, "buttons", None) or []:
            text = getattr(button, "text", None)
            if not text:
                continue
            buttons.append(
                {
                    "text": text,
                    "url": getattr(button, "url", None),
                    "kind": type(button).__name__,
                }
            )
        if buttons:
            result.append(buttons)
    return result


def message_metadata(message: Any, media_type: str | None) -> dict[str, Any]:
    media = (
        getattr(message, "photo", None) or getattr(message, "document", None)
        if media_type
        else None
    )
    content_kind, content = serialize_message_content(message, media_type)
    return {
        "out": bool(getattr(message, "out", False)),
        "post": bool(getattr(message, "post", False)),
        "silent": bool(getattr(message, "silent", False)),
        "mentioned": bool(getattr(message, "mentioned", False)),
        "media_unread": bool(getattr(message, "media_unread", False)),
        "from_scheduled": bool(getattr(message, "from_scheduled", False)),
        "noforwards": bool(getattr(message, "noforwards", False)),
        "via_bot_id": getattr(message, "via_bot_id", None),
        "reply_to_msg_id": getattr(message, "reply_to_msg_id", None),
        "grouped_id": getattr(message, "grouped_id", None),
        "post_author": getattr(message, "post_author", None),
        "forward": forward_metadata(message),
        "webpage": webpage_metadata(message),
        "buttons": inline_buttons_metadata(message),
        "ttl_period": getattr(message, "ttl_period", None),
        "media_type": media_type,
        "media_id": getattr(media, "id", None),
        "entities": json_safe(getattr(message, "entities", None) or []),
        "content_kind": content_kind,
        "content": content,
    }


def volatile_metadata(message: Any) -> dict[str, Any]:
    replies = getattr(message, "replies", None)
    return {
        "views": getattr(message, "views", None),
        "forwards": getattr(message, "forwards", None),
        "replies": getattr(replies, "replies", None),
        "pinned": bool(getattr(message, "pinned", False)),
        "reactions": reaction_summary(message),
    }


def detect_media_type(message: Any) -> str | None:
    # Telethon exposes a WebPage's embedded photo/document through the custom
    # Message.photo and Message.document convenience properties. The enclosing
    # MessageMediaWebPage itself is not a downloadable InputFileLocation, so it
    # must remain metadata-only even when the linked page has an image or video.
    if isinstance(getattr(message, "media", None), types.MessageMediaWebPage):
        return None
    if getattr(message, "photo", None):
        return "photo"
    document = getattr(message, "document", None)
    if not document:
        return None
    attributes = getattr(document, "attributes", None) or []
    for attribute in attributes:
        if isinstance(attribute, types.DocumentAttributeSticker):
            return "sticker"
        if isinstance(attribute, types.DocumentAttributeAnimated):
            return "animation"
        if isinstance(attribute, types.DocumentAttributeAudio):
            return "voice" if attribute.voice else "audio"
        if isinstance(attribute, types.DocumentAttributeVideo):
            return "video"
    return "document"


def content_hash(message: Any, metadata: dict[str, Any]) -> str:
    hash_metadata = copy.deepcopy(metadata)
    forward = hash_metadata.get("forward")
    if isinstance(forward, dict):
        # These fields only help resolve the Saved Messages display peer. They
        # do not change message content and must not create a new archive version.
        forward.pop("saved_from_peer", None)
        forward.pop("saved_from_id", None)
        forward.pop("saved_from_name", None)
    content = hash_metadata.get("content")
    if isinstance(content, dict):
        content.pop("raw", None)
        if hash_metadata.get("content_kind") == "poll":
            content.pop("total_voters", None)
            content.pop("closed", None)
            for answer in content.get("answers") or []:
                if isinstance(answer, dict):
                    answer.pop("voters", None)
                    answer.pop("chosen", None)
                    answer.pop("correct", None)
        elif hash_metadata.get("content_kind") == "todo":
            for item in content.get("items") or []:
                if isinstance(item, dict):
                    item.pop("completed", None)
                    item.pop("completed_by", None)
                    item.pop("completed_at", None)
    payload = {
        "text": getattr(message, "message", None),
        "edit_date": json_safe(getattr(message, "edit_date", None)),
        "metadata": hash_metadata,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def safe_extension(message: Any) -> str:
    extension = getattr(getattr(message, "file", None), "ext", None) or ""
    if not extension.startswith(".") or len(extension) > 16:
        return ""
    return "".join(char for char in extension if char.isalnum() or char == ".")


def telegram_stream_expected_size(message: Any) -> int:
    """Match the exact photo variant selected by Telethon's iter_download.

    ``message.file.size`` is the largest byte count across all photo sizes,
    while ``iter_download(message.media)`` selects ``photo.sizes[-1]``. A
    higher-resolution progressive photo can be smaller in bytes, so mixing the
    two values produces a false incomplete-download error.
    """
    photo = getattr(message, "photo", None)
    sizes = getattr(photo, "sizes", None) or []
    if sizes:
        selected = sizes[-1]
        if isinstance(selected, types.PhotoSizeProgressive):
            return max(selected.sizes, default=0)
        if isinstance(selected, types.PhotoSize):
            return int(selected.size or 0)
        if isinstance(selected, types.PhotoCachedSize):
            return len(selected.bytes or b"")
        if isinstance(selected, types.PhotoStrippedSize):
            payload = selected.bytes or b""
            return len(payload) if len(payload) < 3 or payload[0] != 1 else len(payload) + 622
        if isinstance(selected, types.PhotoSizeEmpty):
            return 0
    return int(getattr(getattr(message, "file", None), "size", 0) or 0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def should_use_concurrent_media_download(
    telegram_account_id: int | None,
    expected_size: int,
    temporary: Path,
) -> bool:
    """Use concurrent ranges only for new large downloads with no serial state."""
    return (
        telegram_account_id is not None
        and expected_size >= settings.telegram_media_parallel_threshold_bytes
        and not temporary.exists()
    )


async def close_download_stream(stream: Any) -> None:
    """Close an initialized iterator without hiding an initialization error."""
    if isinstance(stream, RequestIter) and not hasattr(stream, "_sender"):
        return
    close = getattr(stream, "close", None) or getattr(stream, "aclose", None)
    if close is None:
        return
    result = close()
    if asyncio.iscoroutine(result):
        await result


async def download_media_concurrently(
    client: Any,
    stream_count: int,
    source: Any,
    expected_size: int,
    temporary: Path,
    on_activity: Callable[[], None] | None,
):
    """Download ranges as concurrent requests on the primary client."""
    logger.info(
        "Starting Telegram ranged media download streams=%s size=%s",
        stream_count,
        expected_size,
    )
    return await parallel_download_file(
        client,
        stream_count,
        source,
        expected_size,
        temporary,
        stall_timeout=settings.backup_media_timeout_seconds,
        request_size=settings.telegram_media_request_size_bytes,
        on_activity=on_activity,
    )


async def download_media(
    client: Any,
    message: Any,
    user_id: int,
    peer_id: int,
    selected_types: list[str],
    on_activity: Callable[[], None] | None = None,
    *,
    telegram_account_id: int | None = None,
) -> DownloadedMedia | None:
    media_type = detect_media_type(message)
    if not media_type or media_type not in selected_types:
        return None

    media = getattr(message, "photo", None) or getattr(message, "document", None)
    media_id = str(getattr(media, "id", "")) or None
    extension = safe_extension(message)
    directory = settings.media_root / f"user_{user_id}" / str(peer_id) / str(message.id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{media_type}_{media_id or 'media'}{extension}"
    temporary = target.with_name(f"{target.name}.part")

    downloaded_now = not target.exists()
    downloaded_sha256: str | None = None
    if downloaded_now:
        expected_size = telegram_stream_expected_size(message)
        use_parallel = should_use_concurrent_media_download(
            telegram_account_id,
            expected_size,
            temporary,
        )
        if use_parallel:
            try:
                async with runtime_manager.media_download_slot(
                    telegram_account_id,
                    timeout=settings.backup_fetch_timeout_seconds,
                ) as stream_count:
                    if stream_count > 1:
                        parallel_result = await download_media_concurrently(
                            client,
                            stream_count,
                            getattr(message, "media", None) or message,
                            expected_size,
                            temporary,
                            on_activity,
                        )
                        downloaded = parallel_result.path
                        downloaded_sha256 = parallel_result.sha256
                    else:
                        downloaded = await download_media_with_stall_timeout(
                            client,
                            message,
                            temporary,
                            on_activity=on_activity,
                        )
            except (TelegramAuthorizationError, asyncio.CancelledError):
                raise
            except BaseException as exc:
                error_name = type(exc).__name__
                if error_name == "AuthKeyDuplicatedError":
                    logger.warning(
                        "Telegram invalidated a duplicated authorization key for "
                        "account=%s peer=%s message=%s error=%s",
                        telegram_account_id,
                        peer_id,
                        getattr(message, "id", None),
                        error_name,
                    )
                    await runtime_manager.disable_parallel_media(telegram_account_id)
                    discard_parallel_download(temporary)
                    await runtime_manager.invalidate_account(
                        telegram_account_id,
                        error_name,
                    )
                    raise TelegramAuthorizationError(
                        "Telegram Session 已失效，请重新登录"
                    ) from exc
                if error_name == "AuthBytesInvalidError":
                    logger.warning(
                        "Telegram rejected media authorization for account=%s peer=%s "
                        "message=%s error=%s; disabling concurrent streams",
                        telegram_account_id,
                        peer_id,
                        getattr(message, "id", None),
                        error_name,
                    )
                    await runtime_manager.disable_parallel_media(telegram_account_id)
                    discard_parallel_download(temporary)
                    downloaded = await download_media_with_stall_timeout(
                        client,
                        message,
                        temporary,
                        on_activity=on_activity,
                    )
                else:
                    logger.warning(
                        "Parallel media download failed for peer=%s message=%s; "
                        "disabling concurrent streams and falling back to a serial "
                        "request stream: %s",
                        peer_id,
                        getattr(message, "id", None),
                        error_name,
                    )
                    await runtime_manager.disable_parallel_media(telegram_account_id)
                    discard_parallel_download(temporary)
                    downloaded = await download_media_with_stall_timeout(
                        client,
                        message,
                        temporary,
                        on_activity=on_activity,
                    )
        else:
            downloaded = await download_media_with_stall_timeout(
                client,
                message,
                temporary,
                on_activity=on_activity,
            )
        if not downloaded:
            raise PipelineFailure(
                "media_unavailable",
                "消息媒体在下载前已不可用",
                "skip",
            )
        downloaded_path = Path(downloaded)
        if not downloaded_path.exists():
            raise PipelineFailure("media_download_missing", "媒体下载结果不存在")
        os.replace(downloaded_path, target)

    file_info = getattr(message, "file", None)
    return DownloadedMedia(
        media_type=media_type,
        telegram_media_id=media_id,
        relative_path=target.relative_to(settings.media_root).as_posix(),
        size_bytes=target.stat().st_size,
        sha256=downloaded_sha256 or await asyncio.to_thread(sha256_file, target),
        mime_type=getattr(file_info, "mime_type", None),
        original_name=getattr(file_info, "name", None),
        downloaded_now=downloaded_now,
    )


async def download_media_with_stall_timeout(
    client: Any,
    message: Any,
    temporary: Path,
    *,
    on_activity: Callable[[], None] | None = None,
) -> str | None:
    """Resume a media download and time out only when byte progress stops.

    A fixed wall-clock deadline incorrectly rejects large files on slow proxies.
    Keeping the partial file also avoids restarting a large transfer after a
    transient media-DC or proxy failure.
    """
    expected_size = telegram_stream_expected_size(message)
    received = temporary.stat().st_size if temporary.exists() else 0
    if expected_size and received > expected_size:
        temporary.unlink()
        received = 0
    initial_received = received
    started = time.monotonic()

    source = getattr(message, "media", None) or message
    stream = client.iter_download(
        source,
        offset=received,
        file_size=expected_size or None,
    )
    can_close_stream = True
    try:
        with temporary.open("ab") as handle:
            while not expected_size or received < expected_size:
                try:
                    chunk = await await_with_hard_timeout(
                        stream.__anext__(),
                        settings.backup_media_timeout_seconds,
                    )
                except StopAsyncIteration:
                    break
                except TimeoutError as exc:
                    # await_with_hard_timeout may detach a cancellation-resistant
                    # library task, so closing the same iterator here could race it.
                    can_close_stream = False
                    raise TimeoutError(
                        f"媒体下载连续 {settings.backup_media_timeout_seconds:g} 秒没有进度"
                    ) from exc
                if not chunk:
                    raise TimeoutError("媒体下载没有返回新数据")
                handle.write(chunk)
                received += len(chunk)
                if on_activity:
                    on_activity()
    finally:
        if can_close_stream:
            await close_download_stream(stream)

    if expected_size and received != expected_size:
        raise PipelineFailure(
            "media_download_incomplete",
            f"媒体下载不完整（{received}/{expected_size} 字节）",
        )
    elapsed = max(time.monotonic() - started, 0.001)
    transferred_bytes = max(received - initial_received, 0)
    logger.info(
        "Downloaded Telegram media with primary stream transferred=%s elapsed=%.2fs "
        "speed=%.2f MiB/s",
        transferred_bytes,
        elapsed,
        transferred_bytes / 1024 / 1024 / elapsed,
    )
    return str(temporary)


async def ensure_state(rule_id: int) -> ChatBackupState:
    async with SessionLocal() as db:
        state = await db.scalar(
            select(ChatBackupState).where(ChatBackupState.rule_id == rule_id)
        )
        if state is None:
            state = ChatBackupState(rule_id=rule_id, last_message_id=0, status="idle")
            db.add(state)
            await db.commit()
            await db.refresh(state)
        return state


async def persist_message(
    rule: ChatBackupRule,
    account: TelegramAccount,
    run_id: int,
    message: Any,
    downloaded: DownloadedMedia | None,
) -> bool:
    media_type = detect_media_type(message)
    metadata = message_metadata(message, media_type)
    metrics = volatile_metadata(message)
    digest = content_hash(message, metadata)
    now = utcnow()
    preview_asset: tuple[int, DownloadedMedia] | None = None

    async with SessionLocal.begin() as db:
        sender_entity, sender_version_id = await discover_message_sender(
            db,
            account.id,
            message,
            source="message_backup",
            priority=90,
        )
        require_message_sender_link(message, sender_entity, sender_version_id)
        await discover_message_forward_sender(
            db,
            account.id,
            message,
            source="message_forward",
            priority=90,
        )
        await discover_message_via_bot(
            db,
            account.id,
            message,
            source="message_via_bot",
            priority=90,
        )
        archived = await db.scalar(
            select(ArchivedMessage).where(
                ArchivedMessage.telegram_account_id == account.id,
                ArchivedMessage.peer_id == rule.peer_id,
                ArchivedMessage.message_id == message.id,
            )
        )
        changed = archived is None or archived.current_content_hash != digest
        if archived is None:
            archived = ArchivedMessage(
                telegram_account_id=account.id,
                peer_id=rule.peer_id,
                message_id=message.id,
                sender_id=getattr(message, "sender_id", None),
                sender_entity_id=sender_entity.id if sender_entity else None,
                sent_at=getattr(message, "date", None),
                current_content_hash=digest,
                current_version=1,
                is_deleted=False,
                first_observed_at=now,
                last_observed_at=now,
                volatile_metadata_json=metrics,
            )
            db.add(archived)
            await db.flush()
            version_number = 1
        else:
            archived.sender_id = getattr(message, "sender_id", None)
            if sender_entity:
                archived.sender_entity_id = sender_entity.id
            archived.sent_at = getattr(message, "date", None)
            archived.last_observed_at = now
            archived.is_deleted = False
            archived.volatile_metadata_json = metrics
            version_number = archived.current_version + 1

        if changed:
            version = MessageVersion(
                archived_message_id=archived.id,
                sender_entity_version_id=sender_version_id,
                version=version_number,
                content_hash=digest,
                text=getattr(message, "message", None),
                content_kind=str(metadata.get("content_kind") or "unsupported"),
                content_json=metadata.get("content") or {},
                edit_date=getattr(message, "edit_date", None),
                is_deleted=False,
                metadata_json=metadata,
                observed_at=now,
            )
            db.add(version)
            await db.flush()
            archived.current_content_hash = digest
            archived.current_version = version_number
            if downloaded:
                asset = MediaAsset(
                    message_version_id=version.id,
                    media_type=downloaded.media_type,
                    telegram_media_id=downloaded.telegram_media_id,
                    relative_path=downloaded.relative_path,
                    size_bytes=downloaded.size_bytes,
                    sha256=downloaded.sha256,
                    mime_type=downloaded.mime_type,
                    original_name=downloaded.original_name,
                )
                db.add(asset)
                await db.flush()
                preview_asset = (asset.id, downloaded)
        else:
            current_version = await db.scalar(
                select(MessageVersion).where(
                    MessageVersion.archived_message_id == archived.id,
                    MessageVersion.version == archived.current_version,
                )
            )
            if current_version:
                current_version.content_kind = str(metadata.get("content_kind") or "unsupported")
                current_version.content_json = metadata.get("content") or {}
                current_version.metadata_json = metadata
                if sender_version_id and current_version.sender_entity_version_id is None:
                    current_version.sender_entity_version_id = sender_version_id

        metric = await db.scalar(
            select(MessageMetricDaily).where(
                MessageMetricDaily.archived_message_id == archived.id,
                MessageMetricDaily.sample_date == now.astimezone().date(),
            )
        )
        replies = getattr(getattr(message, "replies", None), "replies", None)
        if metric is None:
            metric = MessageMetricDaily(
                archived_message_id=archived.id,
                sample_date=now.astimezone().date(),
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

        state = await db.scalar(
            select(ChatBackupState)
            .where(ChatBackupState.rule_id == rule.id)
            .with_for_update()
        )
        run = await db.get(BackupRun, run_id)
        if state is None or run is None:
            raise RuntimeError("备份状态或运行记录不存在")
        if run.status != "running":
            raise PipelineFailure("run_expired", "备份运行已结束，拒绝迟到写入", "stop")
        state.last_message_id = max(state.last_message_id, message.id)
        state.updated_at = now
        run.end_cursor = state.last_message_id
        run.fetched_count += 1
        if changed:
            run.stored_count += 1
        if downloaded and changed:
            run.media_count += 1
    if preview_asset:
        media_id, item = preview_asset
        if supports_preview(item.media_type, item.mime_type):
            source = (settings.media_root / item.relative_path).resolve()
            target = preview_cache_path(settings.media_preview_root.resolve(), media_id, item.sha256)
            schedule_media_preview(
                source,
                target,
                media_type=item.media_type,
                ffmpeg_path=settings.ffmpeg_path,
                max_width=settings.media_preview_max_width,
                timeout_seconds=settings.media_preview_timeout_seconds,
            )
    return changed


async def skip_message(
    rule_id: int,
    run_id: int,
    peer_id: int,
    message_id: int,
    failure: PipelineFailure,
) -> None:
    now = utcnow()
    async with SessionLocal.begin() as db:
        state = await db.scalar(
            select(ChatBackupState)
            .where(ChatBackupState.rule_id == rule_id)
            .with_for_update()
        )
        run = await db.get(BackupRun, run_id)
        if state is None or run is None:
            raise RuntimeError("备份状态或运行记录不存在")
        if run.status != "running":
            raise PipelineFailure("run_expired", "备份运行已结束，拒绝推进游标", "stop")
        db.add(
            BackupItemEvent(
                run_id=run_id,
                peer_id=peer_id,
                message_id=message_id,
                level="warning",
                code=failure.code,
                detail=failure.detail,
            )
        )
        state.last_message_id = max(state.last_message_id, message_id)
        state.updated_at = now
        run.end_cursor = state.last_message_id
        run.fetched_count += 1
        run.skipped_count += 1


async def finish_run(
    rule_id: int,
    run_id: int,
    schedule_key: str | None,
) -> None:
    now = utcnow()
    async with SessionLocal.begin() as db:
        state = await db.scalar(
            select(ChatBackupState)
            .where(ChatBackupState.rule_id == rule_id)
            .with_for_update()
        )
        run = await db.get(BackupRun, run_id)
        if state is None or run is None or run.status != "running":
            return
        state.status = "idle"
        state.consecutive_failures = 0
        state.last_error_code = None
        state.last_error = None
        state.retry_after_at = None
        state.last_completed_at = now
        if schedule_key:
            state.last_schedule_key = schedule_key
        run.status = "success" if run.skipped_count == 0 else "partial"
        run.finished_at = now


async def fail_run(
    rule_id: int,
    run_id: int,
    failure: PipelineFailure,
    account_id: int,
) -> None:
    now = utcnow()
    async with SessionLocal.begin() as db:
        state = await db.scalar(
            select(ChatBackupState)
            .where(ChatBackupState.rule_id == rule_id)
            .with_for_update()
        )
        run = await db.get(BackupRun, run_id)
        if state is None or run is None or run.status != "running":
            return
        state.consecutive_failures += 1
        state.last_error_code = failure.code
        state.last_error = failure.detail[:4000]
        if failure.action in {"pause_account", "pause_rule"}:
            state.status = "paused"
            state.retry_after_at = None
        else:
            state.status = "error"
            delay_minutes = min(5 * (2 ** (state.consecutive_failures - 1)), 360)
            state.retry_after_at = now + timedelta(minutes=delay_minutes)
        run.status = "failed"
        run.error_code = failure.code
        run.error_message = failure.detail[:4000]
        run.finished_at = now
        db.add(
            BackupItemEvent(
                run_id=run_id,
                peer_id=0,
                message_id=None,
                level="error",
                code=failure.code,
                detail=failure.detail[:4000],
            )
        )
        if failure.action == "pause_account":
            account = await db.get(TelegramAccount, account_id)
            if account:
                account.status = "login_required"


async def expire_stalled_run(rule_id: int, detail: str) -> None:
    now = utcnow()
    async with SessionLocal.begin() as db:
        state = await db.scalar(
            select(ChatBackupState)
            .where(ChatBackupState.rule_id == rule_id)
            .with_for_update()
        )
        run = await db.scalar(
            select(BackupRun)
            .where(BackupRun.rule_id == rule_id, BackupRun.status == "running")
            .order_by(BackupRun.id.desc())
            .limit(1)
            .with_for_update()
        )
        rule = await db.get(ChatBackupRule, rule_id)
        if state is None or run is None or rule is None:
            return
        state.status = "error"
        state.consecutive_failures += 1
        state.last_error_code = "stage_timeout"
        state.last_error = detail
        state.retry_after_at = now + timedelta(minutes=5)
        run.status = "failed"
        run.error_code = "stage_timeout"
        run.error_message = detail
        run.finished_at = now
        db.add(
            BackupItemEvent(
                run_id=run.id,
                peer_id=rule.peer_id,
                message_id=None,
                level="error",
                code="stage_timeout",
                detail=detail,
            )
        )


async def _notify_activity(callback: Callable[[], None] | None) -> None:
    if callback:
        callback()


async def backup_rule(
    rule_id: int,
    trigger: str = "manual",
    schedule_key: str | None = None,
    on_activity: Callable[[], None] | None = None,
) -> int:
    state = await ensure_state(rule_id)
    now = utcnow()
    async with SessionLocal.begin() as db:
        rule = await db.get(ChatBackupRule, rule_id)
        if rule is None:
            raise PipelineFailure("rule_missing", "备份规则不存在", "stop")
        if rule.removed_at is not None:
            raise PipelineFailure("rule_removed", "备份规则已移除", "stop")
        account = await db.get(TelegramAccount, rule.telegram_account_id)
        if account is None or account.status != "active":
            raise PipelineFailure("telegram_auth_invalid", "Telegram 账号未登录", "pause_account")
        state_row = await db.scalar(
            select(ChatBackupState)
            .where(ChatBackupState.rule_id == rule_id)
            .with_for_update()
        )
        if state_row is None:
            raise RuntimeError("备份状态不存在")
        state_row.status = "running"
        state_row.last_started_at = now
        if schedule_key:
            # Claim this schedule slot before network work starts. If the
            # process fails, recovery uses the error/retry state instead of
            # launching the same schedule slot every scheduler tick.
            state_row.last_schedule_key = schedule_key
        run = BackupRun(
            rule_id=rule_id,
            trigger=trigger,
            status="running",
            schedule_key=schedule_key,
            start_cursor=state.last_message_id,
            end_cursor=state.last_message_id,
            started_at=now,
        )
        db.add(run)
        await db.flush()
        run_id = run.id

    await _notify_activity(on_activity)

    cursor = state.last_message_id
    fetched_count = 0
    stored_count = 0
    skipped_count = 0
    media_count = 0
    await publish_backup_event(
        account.user_id,
        "telegram.backup.started",
        rule=rule,
        run_id=run_id,
        status="running",
        cursor=cursor,
    )
    try:
        async with AsyncExitStack() as client_stack:
            await _notify_activity(on_activity)
            logger.info("Backup run %s waiting for Telegram client", run_id)
            async with asyncio.timeout(settings.backup_fetch_timeout_seconds):
                client = await client_stack.enter_async_context(
                    runtime_manager.client(
                        account.id,
                        timeout=settings.backup_fetch_timeout_seconds,
                    )
                )
            await _notify_activity(on_activity)
            logger.info("Backup run %s resolving Telegram peer", run_id)
            entity = await await_with_hard_timeout(
                client.get_entity(rule.peer_id),
                settings.backup_fetch_timeout_seconds,
            )
            await _notify_activity(on_activity)
            while True:
                await _notify_activity(on_activity)
                logger.info("Backup run %s fetching after cursor %s", run_id, cursor)
                batch = await await_with_hard_timeout(
                    fetch_message_batch(
                        client,
                        entity,
                        cursor,
                        settings.backup_batch_size,
                    ),
                    settings.backup_fetch_timeout_seconds,
                )
                await _notify_activity(on_activity)
                logger.info(
                    "Backup run %s fetched message ids %s",
                    run_id,
                    [getattr(item, "id", None) for item in batch],
                )
                if not batch:
                    break
                for message in batch:
                    failure: PipelineFailure | None = None
                    for attempt in range(settings.backup_message_retries):
                        processing_stage = "media"
                        try:
                            await _notify_activity(on_activity)
                            logger.info(
                                "Backup run %s processing message %s type %s attempt %s/%s",
                                run_id,
                                getattr(message, "id", None),
                                detect_media_type(message),
                                attempt + 1,
                                settings.backup_message_retries,
                            )
                            downloaded = await download_media(
                                client,
                                message,
                                rule.user_id,
                                rule.peer_id,
                                rule.media_types or [],
                                on_activity,
                                telegram_account_id=account.id,
                            )
                            processing_stage = "database"
                            changed = await persist_message(
                                rule, account, run_id, message, downloaded
                            )
                            await _notify_activity(on_activity)
                            cursor = message.id
                            fetched_count += 1
                            if changed:
                                stored_count += 1
                            if downloaded and changed:
                                media_count += 1
                            await publish_backup_event(
                                account.user_id,
                                "telegram.backup.progress",
                                rule=rule,
                                run_id=run_id,
                                status="running",
                                cursor=cursor,
                                fetched_count=fetched_count,
                                stored_count=stored_count,
                                skipped_count=skipped_count,
                                media_count=media_count,
                            )
                            failure = None
                            break
                        except BaseException as exc:
                            failure = classify_exception(exc)
                            message_media_type = detect_media_type(message)
                            if (
                                processing_stage == "media"
                                and failure.code == "network_error"
                                and message_media_type
                            ):
                                failure = PipelineFailure(
                                    "network_error",
                                    f"消息 #{message.id} 的{MEDIA_TYPE_LABELS.get(message_media_type, '媒体')}下载超时",
                                    "retry",
                                )
                            logger.warning(
                                "Backup run %s message %s attempt %s failed: %s (%s)",
                                run_id,
                                getattr(message, "id", None),
                                attempt + 1,
                                failure.code,
                                failure.detail,
                            )
                            if failure.action != "retry":
                                break
                            if attempt + 1 < settings.backup_message_retries:
                                await publish_backup_event(
                                    account.user_id,
                                    "telegram.backup.retrying",
                                    rule=rule,
                                    run_id=run_id,
                                    status="retrying",
                                    cursor=cursor,
                                    fetched_count=fetched_count,
                                    stored_count=stored_count,
                                    skipped_count=skipped_count,
                                    media_count=media_count,
                                    error_code=failure.code,
                                    error_message=failure.detail,
                                    current_message_id=getattr(message, "id", None),
                                    current_media_type=detect_media_type(message),
                                    retry_attempt=attempt + 2,
                                    retry_max=settings.backup_message_retries,
                                )
                                await asyncio.sleep(min(2**attempt, 8))
                    if failure:
                        if failure.action == "skip":
                            await skip_message(rule.id, run_id, rule.peer_id, message.id, failure)
                            cursor = message.id
                            fetched_count += 1
                            skipped_count += 1
                            await publish_backup_event(
                                account.user_id,
                                "telegram.backup.progress",
                                rule=rule,
                                run_id=run_id,
                                status="running",
                                cursor=cursor,
                                fetched_count=fetched_count,
                                stored_count=stored_count,
                                skipped_count=skipped_count,
                                media_count=media_count,
                            )
                            continue
                        raise failure
                if len(batch) < settings.backup_batch_size:
                    break
        await finish_run(rule_id, run_id, schedule_key)
        await publish_backup_event(
            account.user_id,
            "telegram.backup.completed",
            rule=rule,
            run_id=run_id,
            status="partial" if skipped_count else "success",
            cursor=cursor,
            fetched_count=fetched_count,
            stored_count=stored_count,
            skipped_count=skipped_count,
            media_count=media_count,
        )
        return run_id
    except BaseException as exc:
        failure = classify_exception(exc)
        logger.warning(
            "Backup run %s persisting failure %s (%s)",
            run_id,
            failure.code,
            failure.detail,
        )
        await fail_run(rule_id, run_id, failure, account.id)
        logger.warning("Backup run %s failure persisted", run_id)
        await publish_backup_event(
            account.user_id,
            "telegram.backup.failed",
            rule=rule,
            run_id=run_id,
            status="failed",
            cursor=cursor,
            fetched_count=fetched_count,
            stored_count=stored_count,
            skipped_count=skipped_count,
            media_count=media_count,
            error_code=failure.code,
            error_message=failure.detail,
        )
        raise failure from exc
