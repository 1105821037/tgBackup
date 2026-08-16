from __future__ import annotations

import asyncio
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from weakref import WeakValueDictionary

from fastapi import APIRouter, Cookie, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import functions

from .chat_identity import chat_display_title
from .config import get_settings
from .dependencies import CurrentUser, Db
from .security import SESSION_COOKIE, digest
from .media_preview import (
    PreviewGenerationError,
    ensure_media_preview,
    preview_cache_path,
    supports_preview,
)
from .models import (
    ArchivedMessage,
    ChatBackupRule,
    ChatBackupState,
    MediaAsset,
    MessageVersion,
    TelegramAccount,
    TelegramDialog,
    TelegramEntity,
    TelegramEntityPhoto,
    WebSession,
)
from .telegram_runtime import (
    TelegramAuthorizationError,
    TelegramConnectionUnavailable,
    runtime_manager,
)


router = APIRouter(prefix="/api/archive", tags=["archive"])
settings = get_settings()
custom_emoji_locks: WeakValueDictionary[tuple[int, int], asyncio.Lock] = (
    WeakValueDictionary()
)
custom_emoji_slots = asyncio.Semaphore(
    max(1, settings.custom_emoji_download_concurrency)
)


@dataclass(frozen=True)
class AuthorizedMedia:
    id: int
    media_type: str
    mime_type: str | None
    original_name: str | None
    relative_path: str
    sha256: str


@dataclass(frozen=True)
class AuthorizedTelegramAccount:
    user_id: int
    account_id: int


async def authorized_telegram_account(
    db: AsyncSession,
    session_token: str | None,
) -> AuthorizedTelegramAccount:
    if not session_token:
        raise HTTPException(status_code=401, detail="请先登录")
    row = (
        await db.execute(
            select(WebSession.id, WebSession.user_id, TelegramAccount.id, TelegramAccount.status)
            .select_from(WebSession)
            .outerjoin(
                TelegramAccount,
                TelegramAccount.user_id == WebSession.user_id,
            )
            .where(
                WebSession.token_hash == digest(session_token),
                WebSession.expires_at > datetime.now(timezone.utc),
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=401, detail="登录已过期")
    _, user_id, account_id, account_status = row
    if account_id is None:
        raise HTTPException(status_code=409, detail="请先连接 Telegram")
    if account_status != "active":
        raise HTTPException(status_code=409, detail="Telegram 登录已失效，请先重新登录")
    return AuthorizedTelegramAccount(user_id=user_id, account_id=account_id)


async def authorized_media(
    db: AsyncSession,
    media_id: int,
    session_token: str | None,
) -> AuthorizedMedia:
    """Authenticate and resolve a media asset with one short DB checkout."""
    if not session_token:
        raise HTTPException(status_code=401, detail="请先登录")
    row = (
        await db.execute(
            select(WebSession.id, MediaAsset, TelegramAccount.id)
            .select_from(WebSession)
            .outerjoin(MediaAsset, MediaAsset.id == media_id)
            .outerjoin(MessageVersion, MessageVersion.id == MediaAsset.message_version_id)
            .outerjoin(ArchivedMessage, ArchivedMessage.id == MessageVersion.archived_message_id)
            .outerjoin(
                TelegramAccount,
                and_(
                    TelegramAccount.id == ArchivedMessage.telegram_account_id,
                    TelegramAccount.user_id == WebSession.user_id,
                ),
            )
            .where(
                WebSession.token_hash == digest(session_token),
                WebSession.expires_at > datetime.now(timezone.utc),
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=401, detail="登录已过期")
    _, media, owner_account_id = row
    if media is None or owner_account_id is None:
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    return AuthorizedMedia(
        id=media.id,
        media_type=media.media_type,
        mime_type=media.mime_type,
        original_name=media.original_name,
        relative_path=media.relative_path,
        sha256=media.sha256,
    )


MEDIA_LABELS = {
    "photo": "图片",
    "video": "视频",
    "audio": "音频",
    "voice": "语音",
    "document": "文件",
    "animation": "动图",
    "sticker": "贴纸",
}


def is_server_restricted_placeholder(version: MessageVersion | None) -> bool:
    if version is None or version.is_deleted or version.content_kind != "text":
        return False
    text = " ".join((version.text or "").strip().lower().replace("’", "'").split())
    if not text.startswith(
        (
            "this channel ",
            "this group ",
            "this chat ",
            "this message ",
            "this bot ",
            "this user ",
        )
    ):
        return False
    if not any(
        phrase in text
        for phrase in (
            "can't be displayed",
            "cannot be displayed",
            "couldn't be displayed",
            "could not be displayed",
        )
    ):
        return False
    return any(
        reason in text
        for reason in (
            "terms of service",
            "violat",
            "local laws",
            "copyright",
            "pornographic content",
            "sensitive content",
        )
    )


async def display_versions(
    db: AsyncSession,
    current_pairs: list[tuple[ArchivedMessage, MessageVersion]],
) -> dict[int, MessageVersion]:
    """Use the latest normal snapshot when Telegram replaces media with a restriction notice."""
    selected = {archived.id: version for archived, version in current_pairs}
    restricted = {
        archived.id: version.version
        for archived, version in current_pairs
        if is_server_restricted_placeholder(version)
    }
    if not restricted:
        return selected
    candidates = (
        await db.scalars(
            select(MessageVersion)
            .where(MessageVersion.archived_message_id.in_(restricted))
            .order_by(
                MessageVersion.archived_message_id,
                MessageVersion.version.desc(),
            )
        )
    ).all()
    resolved: set[int] = set()
    for candidate in candidates:
        archived_id = candidate.archived_message_id
        if archived_id in resolved or candidate.version >= restricted.get(archived_id, 0):
            continue
        if candidate.is_deleted or is_server_restricted_placeholder(candidate):
            continue
        selected[archived_id] = candidate
        resolved.add(archived_id)
    return selected


async def owned_account(db: Db, user_id: int) -> TelegramAccount:
    account = await db.scalar(
        select(TelegramAccount).where(TelegramAccount.user_id == user_id)
    )
    if account is None:
        raise HTTPException(status_code=409, detail="请先连接 Telegram")
    return account


async def avatar_urls(
    db: Db, entities: list[TelegramEntity | None]
) -> dict[int, str]:
    entity_ids = {entity.id for entity in entities if entity and entity.photo_id}
    if not entity_ids:
        return {}
    rows = (
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
    return {
        entity_id: f"/api/entities/{entity_id}/avatar/{photo_id}/small"
        for entity_id, photo_id in rows
    }


def message_preview(version: MessageVersion | None) -> str:
    if version is None:
        return "消息内容不可用"
    if version.is_deleted:
        return "消息已从 Telegram 删除"
    text = (version.text or "").strip().replace("\n", " ")
    if text:
        return text[:120]
    media_type = version.metadata_json.get("media_type")
    return f"[{MEDIA_LABELS.get(str(media_type), '媒体')}]" if media_type else "空消息"


def media_payload(
    media: MediaAsset,
    content: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": media.id,
        "type": media.media_type,
        "mime_type": media.mime_type,
        "name": media.original_name,
        "size_bytes": media.size_bytes,
        "url": f"/api/archive/media/{media.id}",
        "download_url": f"/api/archive/media/{media.id}?download=true",
    }
    content = content or {}
    width = content.get("width")
    height = content.get("height")
    if isinstance(width, (int, float)) and width > 0:
        payload["width"] = width
    if isinstance(height, (int, float)) and height > 0:
        payload["height"] = height
    if supports_preview(media.media_type, media.mime_type):
        payload["preview_url"] = f"/api/archive/media/{media.id}/preview"
    return payload


SHARED_MEDIA_TYPES = {
    "media": ("photo", "video"),
    "documents": ("document",),
    "audio": ("audio",),
    "voice": ("voice",),
    "gif": ("animation",),
}


def shared_media_asset_types(media_type: str, media_filter: str = "all") -> tuple[str, ...]:
    """Resolve a shared-media category and its optional photo/video filter."""
    if media_type not in SHARED_MEDIA_TYPES:
        raise ValueError("unsupported shared media type")
    if media_type != "media":
        return SHARED_MEDIA_TYPES[media_type]
    if media_filter == "photo":
        return ("photo",)
    if media_filter == "video":
        return ("video",)
    if media_filter == "all":
        return SHARED_MEDIA_TYPES[media_type]
    raise ValueError("unsupported media filter")


def shared_media_link(version: MessageVersion) -> str | None:
    webpage = (version.metadata_json or {}).get("webpage") or {}
    if webpage.get("url"):
        return str(webpage["url"])
    match = re.search(r"(?:https?://|tg://|t\.me/)\S+", version.text or "")
    trailing_punctuation = ".,;:!?)]}。，；：！？）】》〉」』〕〗〙〛"
    return match.group(0).rstrip(trailing_punctuation) if match else None


def message_entities_payload(value: object) -> list[dict[str, object]]:
    entities: list[dict[str, object]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        entity = dict(item)
        if entity.get("_") == "MessageEntityCustomEmoji":
            document_id = entity.get("document_id", entity.get("documentId"))
            if document_id is not None:
                entity["document_id"] = str(document_id)
                entity.pop("documentId", None)
        entities.append(entity)
    return entities


def serialized_peer_id(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    peer_type = value.get("_")
    raw_id = value.get("user_id") or value.get("chat_id") or value.get("channel_id")
    try:
        telegram_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    if peer_type == "PeerUser":
        return telegram_id
    if peer_type == "PeerChat":
        return -telegram_id
    if peer_type == "PeerChannel":
        return -(1_000_000_000_000 + telegram_id)
    return None


def forward_origin_peer_id(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    for key in ("from_id", "saved_from_id", "saved_from_peer"):
        if peer_id := serialized_peer_id(value.get(key)):
            return peer_id
    return None


@router.get("/chats")
async def archive_chats(user: CurrentUser, db: Db) -> dict[str, object]:
    account = await owned_account(db, user.id)
    aggregates = (
        await db.execute(
            select(
                ArchivedMessage.peer_id,
                func.count(ArchivedMessage.id),
                func.max(ArchivedMessage.sent_at),
            )
            .where(ArchivedMessage.telegram_account_id == account.id)
            .group_by(ArchivedMessage.peer_id)
            .order_by(func.max(ArchivedMessage.sent_at).desc())
        )
    ).all()
    if not aggregates:
        return {"items": [], "count": 0}
    peer_ids = [peer_id for peer_id, _, _ in aggregates]
    dialog_rows = (
        await db.execute(
            select(TelegramDialog, TelegramEntity)
            .outerjoin(TelegramEntity, TelegramEntity.id == TelegramDialog.entity_id)
            .where(
                TelegramDialog.telegram_account_id == account.id,
                TelegramDialog.peer_id.in_(peer_ids),
            )
        )
    ).all()
    dialogs = {dialog.peer_id: (dialog, entity) for dialog, entity in dialog_rows}
    rules = {
        rule.peer_id: rule
        for rule in (
            await db.scalars(
                select(ChatBackupRule).where(
                    ChatBackupRule.telegram_account_id == account.id,
                    ChatBackupRule.peer_id.in_(peer_ids),
                )
            )
        ).all()
    }
    rule_ids = [rule.id for rule in rules.values()]
    states = {
        state.rule_id: state
        for state in (
            await db.scalars(
                select(ChatBackupState).where(ChatBackupState.rule_id.in_(rule_ids))
            )
        ).all()
    } if rule_ids else {}
    latest_ids = (
        select(
            ArchivedMessage.peer_id.label("peer_id"),
            func.max(ArchivedMessage.message_id).label("message_id"),
        )
        .where(
            ArchivedMessage.telegram_account_id == account.id,
            ArchivedMessage.peer_id.in_(peer_ids),
        )
        .group_by(ArchivedMessage.peer_id)
        .subquery()
    )
    latest_rows = (
        await db.execute(
            select(ArchivedMessage, MessageVersion)
            .join(
                latest_ids,
                and_(
                    latest_ids.c.peer_id == ArchivedMessage.peer_id,
                    latest_ids.c.message_id == ArchivedMessage.message_id,
                ),
            )
            .join(
                MessageVersion,
                and_(
                    MessageVersion.archived_message_id == ArchivedMessage.id,
                    MessageVersion.version == ArchivedMessage.current_version,
                ),
            )
            .where(ArchivedMessage.telegram_account_id == account.id)
        )
    ).all()
    selected_latest = await display_versions(db, latest_rows)
    latest_versions = {
        message.peer_id: selected_latest[message.id]
        for message, _ in latest_rows
    }
    media_counts = {
        peer_id: count
        for peer_id, count in (
            await db.execute(
                select(
                    ArchivedMessage.peer_id,
                    func.count(func.distinct(MediaAsset.relative_path)),
                )
                .join(MessageVersion, MessageVersion.id == MediaAsset.message_version_id)
                .join(ArchivedMessage, ArchivedMessage.id == MessageVersion.archived_message_id)
                .where(
                    ArchivedMessage.telegram_account_id == account.id,
                    ArchivedMessage.peer_id.in_(peer_ids),
                )
                .group_by(ArchivedMessage.peer_id)
            )
        ).all()
    }
    entity_avatar_urls = await avatar_urls(
        db, [entity for _, entity in dialogs.values()]
    )

    items: list[dict[str, object]] = []
    for peer_id, message_count, last_message_at in aggregates:
        dialog, entity = dialogs.get(peer_id, (None, None))
        rule = rules.get(peer_id)
        state = states.get(rule.id) if rule else None
        latest_version = latest_versions.get(peer_id)
        fallback_title = (
            entity.display_name
            if entity and entity.display_name
            else dialog.title
            if dialog
            else rule.chat_title
            if rule
            else str(peer_id)
        )
        items.append(
            {
                "peer_id": peer_id,
                "is_self": peer_id == account.telegram_user_id,
                "title": chat_display_title(
                    account.telegram_user_id, peer_id, fallback_title
                ),
                "kind": dialog.kind if dialog else rule.chat_kind if rule else "unknown",
                "shows_sender_profiles": bool(
                    (entity.profile_json or {}).get("signature_profiles")
                    if entity
                    else False
                ),
                "username": entity.username if entity else dialog.username if dialog else None,
                "entity_id": entity.id if entity else None,
                "avatar_url": entity_avatar_urls.get(entity.id) if entity else None,
                "message_count": message_count,
                "media_count": media_counts.get(peer_id, 0),
                "last_message": message_preview(latest_version),
                "last_message_at": last_message_at,
                "rule_status": (
                    "removed"
                    if rule and rule.removed_at
                    else "paused"
                    if rule and not rule.enabled
                    else "active"
                    if rule
                    else "none"
                ),
                "backup_status": state.status if state else "idle",
                "last_backup_at": state.last_completed_at if state else None,
            }
        )
    return {"items": items, "count": len(items)}


@router.get("/chats/{peer_id}/messages")
async def archive_messages(
    peer_id: int,
    user: CurrentUser,
    db: Db,
    before_id: int | None = Query(default=None, ge=1),
    after_id: int | None = Query(default=None, ge=1),
    anchor_id: int | None = Query(default=None, ge=1),
    before_count: int = Query(default=20, alias="before", ge=0, le=50),
    after_count: int = Query(default=20, alias="after", ge=0, le=50),
    limit: int = Query(default=40, ge=1, le=100),
) -> dict[str, object]:
    account = await owned_account(db, user.id)
    if sum(value is not None for value in (before_id, after_id, anchor_id)) > 1:
        raise HTTPException(
            status_code=422,
            detail="before_id、after_id 与 anchor_id 只能使用一个",
        )

    base_conditions = [
        ArchivedMessage.telegram_account_id == account.id,
        ArchivedMessage.peer_id == peer_id,
    ]
    message_query = (
        select(ArchivedMessage, MessageVersion, TelegramEntity)
        .join(
            MessageVersion,
            and_(
                MessageVersion.archived_message_id == ArchivedMessage.id,
                MessageVersion.version == ArchivedMessage.current_version,
            ),
        )
        .outerjoin(TelegramEntity, TelegramEntity.id == ArchivedMessage.sender_entity_id)
    )

    resolved_anchor_id: int | None = None
    anchor_found: bool | None = None
    if anchor_id is not None:
        resolved_anchor_id = await db.scalar(
            select(ArchivedMessage.message_id).where(
                *base_conditions,
                ArchivedMessage.message_id == anchor_id,
            )
        )
        anchor_found = resolved_anchor_id is not None
        if resolved_anchor_id is None:
            # A referenced Telegram message may never have been archived. Use
            # the closest archived predecessor, or the first successor when
            # there is no predecessor, so callers can still show useful context.
            resolved_anchor_id = await db.scalar(
                select(ArchivedMessage.message_id)
                .where(*base_conditions, ArchivedMessage.message_id < anchor_id)
                .order_by(ArchivedMessage.message_id.desc())
                .limit(1)
            )
        if resolved_anchor_id is None:
            resolved_anchor_id = await db.scalar(
                select(ArchivedMessage.message_id)
                .where(*base_conditions, ArchivedMessage.message_id > anchor_id)
                .order_by(ArchivedMessage.message_id.asc())
                .limit(1)
            )

        if resolved_anchor_id is None:
            rows = []
        else:
            older_rows = (
                await db.execute(
                    message_query
                    .where(
                        *base_conditions,
                        ArchivedMessage.message_id <= resolved_anchor_id,
                    )
                    .order_by(ArchivedMessage.message_id.desc())
                    .limit(before_count + 1)
                )
            ).all()
            older_rows.reverse()
            newer_rows = (
                await db.execute(
                    message_query
                    .where(
                        *base_conditions,
                        ArchivedMessage.message_id > resolved_anchor_id,
                    )
                    .order_by(ArchivedMessage.message_id.asc())
                    .limit(after_count)
                )
            ).all()
            rows = [*older_rows, *newer_rows]
    elif after_id is not None:
        rows = (
            await db.execute(
                message_query
                .where(*base_conditions, ArchivedMessage.message_id > after_id)
                .order_by(ArchivedMessage.message_id.asc())
                .limit(limit)
            )
        ).all()
    else:
        conditions = list(base_conditions)
        if before_id is not None:
            conditions.append(ArchivedMessage.message_id < before_id)
        rows = (
            await db.execute(
                message_query
                .where(*conditions)
                .order_by(ArchivedMessage.message_id.desc())
                .limit(limit)
            )
        ).all()
        rows.reverse()

    rows.sort(key=lambda row: row[0].message_id)
    first_message_id = rows[0][0].message_id if rows else None
    last_message_id = rows[-1][0].message_id if rows else None
    has_older = bool(
        first_message_id is not None
        and await db.scalar(
            select(ArchivedMessage.id)
            .where(*base_conditions, ArchivedMessage.message_id < first_message_id)
            .limit(1)
        )
    )
    has_newer = bool(
        last_message_id is not None
        and await db.scalar(
            select(ArchivedMessage.id)
            .where(*base_conditions, ArchivedMessage.message_id > last_message_id)
            .limit(1)
        )
    )

    selected_versions = await display_versions(
        db, [(archived, version) for archived, version, _ in rows]
    )
    rows = [
        (archived, selected_versions[archived.id], sender)
        for archived, _, sender in rows
    ]

    version_ids = [version.id for _, version, _ in rows]
    media_by_version: dict[int, list[MediaAsset]] = {}
    if version_ids:
        media_rows = (
            await db.scalars(
                select(MediaAsset)
                .where(MediaAsset.message_version_id.in_(version_ids))
                .order_by(MediaAsset.id)
            )
        ).all()
        for media in media_rows:
            media_by_version.setdefault(media.message_version_id, []).append(media)

    reply_ids = {
        int(reply_id)
        for _, version, _ in rows
        if (reply_id := (version.metadata_json or {}).get("reply_to_msg_id")) is not None
    }
    reply_previews: dict[int, dict[str, object]] = {}
    if reply_ids:
        reply_rows = (
            await db.execute(
                select(ArchivedMessage, MessageVersion, TelegramEntity)
                .join(
                    MessageVersion,
                    and_(
                        MessageVersion.archived_message_id == ArchivedMessage.id,
                        MessageVersion.version == ArchivedMessage.current_version,
                    ),
                )
                .outerjoin(
                    TelegramEntity,
                    TelegramEntity.id == ArchivedMessage.sender_entity_id,
                )
                .where(
                    ArchivedMessage.telegram_account_id == account.id,
                    ArchivedMessage.peer_id == peer_id,
                    ArchivedMessage.message_id.in_(reply_ids),
                )
            )
        ).all()
        reply_selected_versions = await display_versions(
            db,
            [
                (reply_message, reply_version)
                for reply_message, reply_version, _ in reply_rows
            ],
        )
        for reply_message, reply_version, reply_sender in reply_rows:
            reply_version = reply_selected_versions[reply_message.id]
            reply_previews[reply_message.message_id] = {
                "message_id": reply_message.message_id,
                "sender_name": (
                    reply_sender.display_name
                    if reply_sender and reply_sender.display_name
                    else (reply_version.metadata_json or {}).get("post_author")
                ),
                "text": message_preview(reply_version),
                "is_deleted": reply_message.is_deleted or reply_version.is_deleted,
            }

    origin_peer_ids = {
        origin_peer_id
        for _, version, _ in rows
        if (origin_peer_id := forward_origin_peer_id((version.metadata_json or {}).get("forward")))
        is not None
    }
    origin_entities: dict[int, TelegramEntity] = {}
    if origin_peer_ids:
        origin_entities = {
            entity.peer_id: entity
            for entity in (
                await db.scalars(
                    select(TelegramEntity).where(
                        TelegramEntity.telegram_account_id == account.id,
                        TelegramEntity.peer_id.in_(origin_peer_ids),
                    )
                )
            ).all()
        }
    via_bot_ids = {
        int(via_bot_id)
        for _, version, _ in rows
        if (via_bot_id := (version.metadata_json or {}).get("via_bot_id"))
        is not None
    }
    via_bot_entities: dict[int, TelegramEntity] = {}
    if via_bot_ids:
        via_bot_entities = {
            entity.peer_id: entity
            for entity in (
                await db.scalars(
                    select(TelegramEntity).where(
                        TelegramEntity.telegram_account_id == account.id,
                        TelegramEntity.peer_id.in_(via_bot_ids),
                    )
                )
            ).all()
        }
    sender_avatars = await avatar_urls(
        db,
        [sender for _, _, sender in rows]
        + list(origin_entities.values()),
    )
    items: list[dict[str, object]] = []
    for archived, version, sender in rows:
        metadata = version.metadata_json or {}
        forward_info = metadata.get("forward")
        origin_peer_id = forward_origin_peer_id(forward_info)
        origin_sender = origin_entities.get(origin_peer_id) if origin_peer_id else None
        via_bot_id = metadata.get("via_bot_id")
        via_bot = via_bot_entities.get(int(via_bot_id)) if via_bot_id else None
        media_items = media_by_version.get(version.id, [])
        items.append(
            {
                "id": archived.id,
                "message_id": archived.message_id,
                "sender_id": archived.sender_id,
                "sender": (
                    {
                        "entity_id": sender.id,
                        "peer_id": sender.peer_id,
                        "kind": sender.entity_kind,
                        "name": sender.display_name,
                        "username": sender.username,
                        "avatar_url": sender_avatars.get(sender.id),
                    }
                    if sender
                    else None
                ),
                "sent_at": archived.sent_at,
                "text": version.text,
                "content_kind": version.content_kind,
                "content": version.content_json or {},
                "out": bool(metadata.get("out")),
                "post": bool(metadata.get("post")),
                "post_author": metadata.get("post_author"),
                "via_bot": (
                    {
                        "entity_id": via_bot.id if via_bot else None,
                        "peer_id": int(via_bot_id),
                        "kind": via_bot.entity_kind if via_bot else "bot",
                        "name": via_bot.display_name if via_bot else None,
                        "username": via_bot.username if via_bot else None,
                    }
                    if via_bot_id
                    else None
                ),
                "reply_to_msg_id": metadata.get("reply_to_msg_id"),
                "reply_preview": reply_previews.get(metadata.get("reply_to_msg_id")),
                "grouped_id": metadata.get("grouped_id"),
                "forward_info": forward_info,
                "origin_sender": (
                    {
                        "entity_id": origin_sender.id if origin_sender else None,
                        "peer_id": origin_peer_id,
                        "kind": origin_sender.entity_kind if origin_sender else None,
                        "name": (
                            origin_sender.display_name
                            if origin_sender
                            else (forward_info or {}).get("name")
                            or (forward_info or {}).get("saved_from_name")
                        ),
                        "username": origin_sender.username if origin_sender else None,
                        "avatar_url": (
                            sender_avatars.get(origin_sender.id) if origin_sender else None
                        ),
                    }
                    if forward_info
                    else None
                ),
                "webpage_info": metadata.get("webpage"),
                "buttons": metadata.get("buttons") or [],
                "entities": message_entities_payload(metadata.get("entities")),
                "is_deleted": archived.is_deleted or version.is_deleted,
                "is_edited": archived.current_version > 1 or version.edit_date is not None,
                "current_version": archived.current_version,
                "displayed_version": version.version,
                "is_restored": version.version != archived.current_version,
                "edit_date": version.edit_date,
                "observed_at": version.observed_at,
                "metrics": archived.volatile_metadata_json or {},
                "media": [
                    media_payload(media, version.content_json)
                    for media in media_items
                ],
            }
        )
    return {
        "items": items,
        "has_older": has_older,
        "has_newer": has_newer,
        "next_before_id": items[0]["message_id"] if items and has_older else None,
        "next_after_id": items[-1]["message_id"] if items and has_newer else None,
        "requested_anchor_id": anchor_id,
        "anchor_id": resolved_anchor_id,
        "anchor_found": anchor_found,
    }


@router.get("/chats/{peer_id}/search")
async def archive_message_search(
    peer_id: int,
    user: CurrentUser,
    db: Db,
    query: str = Query(alias="q", min_length=1, max_length=256),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=42, ge=1, le=100),
) -> dict[str, object]:
    """Search every saved text version and return one result per message."""
    account = await owned_account(db, user.id)
    normalized_query = " ".join(query.split())
    if not normalized_query:
        raise HTTPException(status_code=422, detail="搜索内容不能为空")

    text_match = MessageVersion.text.contains(normalized_query, autoescape=True)
    matching_versions = (
        select(
            MessageVersion.archived_message_id.label("archived_message_id"),
            func.max(MessageVersion.version).label("matched_version"),
        )
        .where(MessageVersion.text.is_not(None), text_match)
        .group_by(MessageVersion.archived_message_id)
        .subquery()
    )
    base_conditions = [
        ArchivedMessage.telegram_account_id == account.id,
        ArchivedMessage.peer_id == peer_id,
    ]
    total_count = await db.scalar(
        select(func.count())
        .select_from(matching_versions)
        .join(
            ArchivedMessage,
            ArchivedMessage.id == matching_versions.c.archived_message_id,
        )
        .where(*base_conditions)
    ) or 0
    rows = (
        await db.execute(
            select(ArchivedMessage, MessageVersion, TelegramEntity)
            .join(
                matching_versions,
                matching_versions.c.archived_message_id == ArchivedMessage.id,
            )
            .join(
                MessageVersion,
                and_(
                    MessageVersion.archived_message_id == ArchivedMessage.id,
                    MessageVersion.version == matching_versions.c.matched_version,
                ),
            )
            .outerjoin(
                TelegramEntity,
                TelegramEntity.id == ArchivedMessage.sender_entity_id,
            )
            .where(*base_conditions)
            .order_by(ArchivedMessage.message_id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return {
        "items": [
            {
                "message_id": archived.message_id,
                "sent_at": archived.sent_at,
                "sender_name": sender.display_name if sender else None,
                "text": version.text,
                "matched_version": version.version,
                "current_version": archived.current_version,
                "is_history_version": version.version != archived.current_version,
            }
            for archived, version, sender in rows
        ],
        "total_count": total_count,
        "offset": offset,
        "has_more": offset + len(rows) < total_count,
    }


@router.get("/chats/{peer_id}/shared-media")
async def archive_shared_media(
    peer_id: int,
    user: CurrentUser,
    db: Db,
    media_type: str = Query(default="media", alias="type"),
    media_filter: str = Query(default="all", pattern="^(all|photo|video)$"),
    before_id: int | None = Query(default=None, ge=1),
    before_version: int | None = Query(default=None, ge=1),
    before_media_id: int | None = Query(default=None, ge=1),
    offset: int | None = Query(default=None, ge=0),
    limit: int = Query(default=60, ge=1, le=100),
) -> dict[str, object]:
    account = await owned_account(db, user.id)
    if media_type not in {*SHARED_MEDIA_TYPES, "links"}:
        raise HTTPException(status_code=422, detail="不支持的共享媒体类型")
    if media_type == "links":
        link_conditions = [
            ArchivedMessage.telegram_account_id == account.id,
            ArchivedMessage.peer_id == peer_id,
            or_(
                MessageVersion.content_kind == "webpage",
                MessageVersion.text.op("REGEXP")(r"(https?://|tg://|t\\.me/)"),
            ),
        ]
        total_count = await db.scalar(
            select(func.count())
            .select_from(ArchivedMessage)
            .join(
                MessageVersion,
                and_(
                    MessageVersion.archived_message_id == ArchivedMessage.id,
                    MessageVersion.version == ArchivedMessage.current_version,
                ),
            )
            .where(*link_conditions)
        ) or 0
        paged_link_conditions = list(link_conditions)
        if before_id is not None and offset is None:
            paged_link_conditions.append(ArchivedMessage.message_id < before_id)
        link_query = (
            select(ArchivedMessage, MessageVersion)
            .join(
                MessageVersion,
                and_(
                    MessageVersion.archived_message_id == ArchivedMessage.id,
                    MessageVersion.version == ArchivedMessage.current_version,
                ),
            )
            .where(*paged_link_conditions)
            .order_by(ArchivedMessage.message_id.desc())
        )
        if offset is not None:
            link_query = link_query.offset(offset)
        link_rows = (
            await db.execute(
                link_query.limit(limit + 1)
            )
        ).all()
        has_more = len(link_rows) > limit
        link_rows = link_rows[:limit]
        return {
            "items": [
                {
                    "id": version.id,
                    "type": "link",
                    "size_bytes": 0,
                    "url": shared_media_link(version),
                    "download_url": shared_media_link(version),
                    "message_id": archived.message_id,
                    "sent_at": archived.sent_at,
                    "text": version.text,
                    "content": (version.metadata_json or {}).get("webpage") or {},
                }
                for archived, version in link_rows
            ],
            "total_count": total_count,
            "has_more": has_more,
            "next_before_id": link_rows[-1][0].message_id if has_more and link_rows else None,
        }
    conditions = [
        ArchivedMessage.telegram_account_id == account.id,
        ArchivedMessage.peer_id == peer_id,
        MediaAsset.media_type.in_(shared_media_asset_types(media_type, media_filter)),
    ]
    total_count = await db.scalar(
        select(func.count(MediaAsset.id))
        .join(MessageVersion, MessageVersion.id == MediaAsset.message_version_id)
        .join(ArchivedMessage, ArchivedMessage.id == MessageVersion.archived_message_id)
        .where(*conditions)
    ) or 0
    if before_id is not None and offset is None:
        if before_version is not None and before_media_id is not None:
            conditions.append(
                or_(
                    ArchivedMessage.message_id < before_id,
                    and_(
                        ArchivedMessage.message_id == before_id,
                        MessageVersion.version < before_version,
                    ),
                    and_(
                        ArchivedMessage.message_id == before_id,
                        MessageVersion.version == before_version,
                        MediaAsset.id < before_media_id,
                    ),
                )
            )
        else:
            conditions.append(ArchivedMessage.message_id < before_id)
    media_query = (
        select(MediaAsset, ArchivedMessage, MessageVersion)
        .join(MessageVersion, MessageVersion.id == MediaAsset.message_version_id)
        .join(ArchivedMessage, ArchivedMessage.id == MessageVersion.archived_message_id)
        .where(*conditions)
        .order_by(
            ArchivedMessage.message_id.desc(),
            MessageVersion.version.desc(),
            MediaAsset.id.desc(),
        )
    )
    if offset is not None:
        media_query = media_query.offset(offset)
    rows = (await db.execute(media_query.limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [
            {
                **media_payload(media, version.content_json),
                "message_id": archived.message_id,
                "sent_at": archived.sent_at,
                "text": version.text,
                "content": version.content_json or {},
                "version": version.version,
                "current_version": archived.current_version,
                "is_history_version": version.version != archived.current_version,
            }
            for media, archived, version in rows
        ],
        "total_count": total_count,
        "has_more": has_more,
        "next_before_id": rows[-1][1].message_id if has_more and rows else None,
        "next_before_version": rows[-1][2].version if has_more and rows else None,
        "next_before_media_id": rows[-1][0].id if has_more and rows else None,
    }


@router.get("/messages/{archived_message_id}/versions")
async def archive_message_versions(
    archived_message_id: int,
    user: CurrentUser,
    db: Db,
) -> dict[str, object]:
    archived = await db.scalar(
        select(ArchivedMessage)
        .join(
            TelegramAccount,
            TelegramAccount.id == ArchivedMessage.telegram_account_id,
        )
        .where(
            ArchivedMessage.id == archived_message_id,
            TelegramAccount.user_id == user.id,
        )
    )
    if archived is None:
        raise HTTPException(status_code=404, detail="归档消息不存在")

    versions = (
        await db.scalars(
            select(MessageVersion)
            .where(MessageVersion.archived_message_id == archived.id)
            .order_by(MessageVersion.version.desc())
        )
    ).all()
    version_ids = [version.id for version in versions]
    media_by_version: dict[int, list[MediaAsset]] = {}
    if version_ids:
        media_rows = (
            await db.scalars(
                select(MediaAsset)
                .where(MediaAsset.message_version_id.in_(version_ids))
                .order_by(MediaAsset.id)
            )
        ).all()
        for media in media_rows:
            media_by_version.setdefault(media.message_version_id, []).append(media)

    items: list[dict[str, object]] = []
    for version in versions:
        metadata = version.metadata_json or {}
        items.append(
            {
                "version": version.version,
                "text": version.text,
                "content_kind": version.content_kind,
                "content": version.content_json or {},
                "is_deleted": version.is_deleted,
                "edit_date": version.edit_date,
                "observed_at": version.observed_at,
                "entities": message_entities_payload(metadata.get("entities")),
                "post_author": metadata.get("post_author"),
                "media": [
                    media_payload(media, version.content_json)
                    for media in media_by_version.get(version.id, [])
                ],
            }
        )
    return {
        "id": archived.id,
        "message_id": archived.message_id,
        "current_version": archived.current_version,
        "is_deleted": archived.is_deleted,
        "items": items,
    }


def custom_emoji_extension(mime_type: str | None) -> str:
    return {
        "application/x-tgsticker": ".tgs",
        "video/webm": ".webm",
        "image/webp": ".webp",
        "image/png": ".png",
    }.get(mime_type or "", ".bin")


@router.get("/custom-emojis/{document_id}")
async def archive_custom_emoji(
    document_id: int,
    db: Db,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> FileResponse:
    if document_id <= 0:
        raise HTTPException(status_code=404, detail="自定义 Emoji 不存在")
    account = await authorized_telegram_account(db, session_token)
    # No request waiting on a per-document lock, Telegram, or a file response
    # should retain a checkout from the main SQLAlchemy pool.
    await db.close()
    directory = settings.media_root / f"user_{account.user_id}" / "custom_emoji"
    candidates = [
        directory / f"{document_id}{extension}"
        for extension in (".tgs", ".webm", ".webp", ".png", ".bin")
    ]

    def existing_file() -> Path | None:
        return next((path for path in candidates if path.is_file()), None)

    path = existing_file()
    if path is None:
        lock = custom_emoji_locks.setdefault((account.account_id, document_id), asyncio.Lock())
        async with lock:
            path = existing_file()
            if path is None:
                downloaded_path: Path | None = None
                temporary: Path | None = None
                try:
                    async with custom_emoji_slots:
                        # Another document may have completed while this request
                        # waited for the global Telegram download slot.
                        path = existing_file()
                        if path is None:
                            async with asyncio.timeout(
                                max(1, settings.custom_emoji_download_timeout_seconds)
                            ):
                                async with runtime_manager.client(account.account_id) as client:
                                    documents = await client(
                                        functions.messages.GetCustomEmojiDocumentsRequest(
                                            document_id=[document_id]
                                        )
                                    )
                                    if not documents:
                                        raise HTTPException(
                                            status_code=404, detail="自定义 Emoji 不存在"
                                        )
                                    document = documents[0]
                                    directory.mkdir(parents=True, exist_ok=True)
                                    target = directory / (
                                        f"{document_id}"
                                        f"{custom_emoji_extension(getattr(document, 'mime_type', None))}"
                                    )
                                    temporary = target.with_name(
                                        f".{target.stem}.{uuid.uuid4().hex}.part{target.suffix}"
                                    )
                                    downloaded = await client.download_media(
                                        document,
                                        file=str(temporary),
                                    )
                                    downloaded_path = Path(downloaded) if downloaded else temporary
                                    if not downloaded_path.is_file() or downloaded_path.stat().st_size <= 0:
                                        raise HTTPException(
                                            status_code=404,
                                            detail="自定义 Emoji 下载失败",
                                        )
                                    os.replace(downloaded_path, target)
                                    path = target
                except TimeoutError as exc:
                    raise HTTPException(
                        status_code=504,
                        detail="自定义 Emoji 下载超时",
                    ) from exc
                except TelegramAuthorizationError as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
                except TelegramConnectionUnavailable as exc:
                    raise HTTPException(status_code=503, detail=str(exc)) from exc
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                    if (
                        downloaded_path is not None
                        and downloaded_path != path
                        and downloaded_path != temporary
                    ):
                        downloaded_path.unlink(missing_ok=True)
                if path is None or not path.is_file():
                    raise HTTPException(
                        status_code=404,
                        detail="自定义 Emoji 下载失败",
                    )

    mime_type = {
        ".tgs": "application/x-tgsticker",
        ".webm": "video/webm",
        ".webp": "image/webp",
        ".png": "image/png",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(
        path,
        media_type=mime_type,
        headers={
            "Cache-Control": "private, max-age=604800, immutable",
            "ETag": f'"custom-emoji-{document_id}"',
        },
    )


@router.get("/media/{media_id}/preview")
async def archive_media_preview(
    media_id: int,
    db: Db,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> FileResponse:
    media = await authorized_media(db, media_id, session_token)
    # FFmpeg work can wait behind the two-slot preview queue. Return the DB
    # connection before any filesystem or subprocess work begins.
    await db.close()
    if not supports_preview(media.media_type, media.mime_type):
        raise HTTPException(status_code=415, detail="该媒体类型不支持预览图")

    media_root = settings.media_root.resolve()
    source = (media_root / media.relative_path).resolve()
    try:
        source.relative_to(media_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="媒体路径无效") from exc
    if not source.is_file():
        raise HTTPException(status_code=404, detail="媒体文件不存在")

    target = preview_cache_path(
        settings.media_preview_root.resolve(), media.id, media.sha256
    )
    try:
        preview = await ensure_media_preview(
            source,
            target,
            media_type=media.media_type,
            ffmpeg_path=settings.ffmpeg_path,
            max_width=settings.media_preview_max_width,
            timeout_seconds=settings.media_preview_timeout_seconds,
        )
    except PreviewGenerationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(
        preview,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "ETag": f'"preview-{media.sha256}"',
        },
    )


@router.get("/media/{media_id}")
async def archive_media(
    media_id: int,
    db: Db,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    download: bool = False,
) -> FileResponse:
    media = await authorized_media(db, media_id, session_token)
    await db.close()
    root = settings.media_root.resolve()
    path = (root / media.relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="媒体路径无效") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    response = FileResponse(
        Path(path),
        media_type=media.mime_type or "application/octet-stream",
        filename=media.original_name or path.name if download else None,
        headers={
            "Cache-Control": "private, max-age=3600",
            "ETag": f'"{media.sha256}"',
            "Accept-Ranges": "bytes",
        },
    )
    return response
