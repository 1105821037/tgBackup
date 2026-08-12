from __future__ import annotations

import asyncio
import re
from pathlib import Path
from weakref import WeakValueDictionary

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import and_, func, or_, select
from telethon import functions

from .chat_identity import chat_display_title
from .config import get_settings
from .dependencies import CurrentUser, Db
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


MEDIA_LABELS = {
    "photo": "图片",
    "video": "视频",
    "audio": "音频",
    "voice": "语音",
    "document": "文件",
    "animation": "动图",
    "sticker": "贴纸",
}


async def owned_account(db: Db, user_id: int) -> TelegramAccount:
    account = await db.scalar(
        select(TelegramAccount).where(TelegramAccount.user_id == user_id)
    )
    if account is None:
        raise HTTPException(status_code=409, detail="请先连接 Telegram")
    return account


async def avatar_url(db: Db, entity: TelegramEntity | None) -> str | None:
    if entity is None or entity.photo_id is None:
        return None
    photo_id = await db.scalar(
        select(TelegramEntityPhoto.telegram_photo_id).where(
            TelegramEntityPhoto.entity_id == entity.id,
            TelegramEntityPhoto.telegram_photo_id == entity.photo_id,
            TelegramEntityPhoto.variant == "small",
            TelegramEntityPhoto.status == "available",
        )
    )
    if photo_id is None:
        return None
    return f"/api/entities/{entity.id}/avatar/{photo_id}/small"


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


def media_payload(media: MediaAsset) -> dict[str, object]:
    return {
        "id": media.id,
        "type": media.media_type,
        "mime_type": media.mime_type,
        "name": media.original_name,
        "size_bytes": media.size_bytes,
        "url": f"/api/archive/media/{media.id}",
        "download_url": f"/api/archive/media/{media.id}?download=true",
    }


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
    items: list[dict[str, object]] = []
    for peer_id, message_count, last_message_at in aggregates:
        dialog_row = (
            await db.execute(
                select(TelegramDialog, TelegramEntity)
                .outerjoin(TelegramEntity, TelegramEntity.id == TelegramDialog.entity_id)
                .where(
                    TelegramDialog.telegram_account_id == account.id,
                    TelegramDialog.peer_id == peer_id,
                )
                .limit(1)
            )
        ).first()
        dialog, entity = dialog_row if dialog_row else (None, None)
        rule = await db.scalar(
            select(ChatBackupRule).where(
                ChatBackupRule.telegram_account_id == account.id,
                ChatBackupRule.peer_id == peer_id,
            )
        )
        state = (
            await db.scalar(
                select(ChatBackupState).where(ChatBackupState.rule_id == rule.id)
            )
            if rule
            else None
        )
        latest_row = (
            await db.execute(
                select(ArchivedMessage, MessageVersion)
                .join(
                    MessageVersion,
                    and_(
                        MessageVersion.archived_message_id == ArchivedMessage.id,
                        MessageVersion.version == ArchivedMessage.current_version,
                    ),
                )
                .where(
                    ArchivedMessage.telegram_account_id == account.id,
                    ArchivedMessage.peer_id == peer_id,
                )
                .order_by(ArchivedMessage.message_id.desc())
                .limit(1)
            )
        ).first()
        _, latest_version = latest_row if latest_row else (None, None)
        media_count = await db.scalar(
            select(func.count(func.distinct(MediaAsset.relative_path)))
            .join(MessageVersion, MessageVersion.id == MediaAsset.message_version_id)
            .join(ArchivedMessage, ArchivedMessage.id == MessageVersion.archived_message_id)
            .where(
                ArchivedMessage.telegram_account_id == account.id,
                ArchivedMessage.peer_id == peer_id,
            )
        )
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
                "avatar_url": await avatar_url(db, entity),
                "message_count": message_count,
                "media_count": media_count or 0,
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
        for reply_message, reply_version, reply_sender in reply_rows:
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

    sender_avatars: dict[int, str | None] = {}
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
    items: list[dict[str, object]] = []
    for archived, version, sender in rows:
        if sender and sender.id not in sender_avatars:
            sender_avatars[sender.id] = await avatar_url(db, sender)
        metadata = version.metadata_json or {}
        forward_info = metadata.get("forward")
        origin_peer_id = forward_origin_peer_id(forward_info)
        origin_sender = origin_entities.get(origin_peer_id) if origin_peer_id else None
        via_bot_id = metadata.get("via_bot_id")
        via_bot = via_bot_entities.get(int(via_bot_id)) if via_bot_id else None
        if origin_sender and origin_sender.id not in sender_avatars:
            sender_avatars[origin_sender.id] = await avatar_url(db, origin_sender)
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
                "edit_date": version.edit_date,
                "observed_at": version.observed_at,
                "metrics": archived.volatile_metadata_json or {},
                "media": [media_payload(media) for media in media_items],
            }
        )
    return {
        "items": items,
        # Keep the original fields for older clients. `has_more` means older
        # pages are available, matching the former before-only pagination.
        "has_more": has_older,
        "has_older": has_older,
        "has_newer": has_newer,
        "next_before_id": items[0]["message_id"] if items and has_older else None,
        "next_after_id": items[-1]["message_id"] if items and has_newer else None,
        "requested_anchor_id": anchor_id,
        "anchor_id": resolved_anchor_id,
        "anchor_found": anchor_found,
    }


@router.get("/chats/{peer_id}/shared-media")
async def archive_shared_media(
    peer_id: int,
    user: CurrentUser,
    db: Db,
    media_type: str = Query(default="media", alias="type"),
    media_filter: str = Query(default="all", pattern="^(all|photo|video)$"),
    before_id: int | None = Query(default=None, ge=1),
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
        if before_id is not None:
            link_conditions.append(ArchivedMessage.message_id < before_id)
        link_rows = (
            await db.execute(
                select(ArchivedMessage, MessageVersion)
                .join(
                    MessageVersion,
                    and_(
                        MessageVersion.archived_message_id == ArchivedMessage.id,
                        MessageVersion.version == ArchivedMessage.current_version,
                    ),
                )
                .where(*link_conditions)
                .order_by(ArchivedMessage.message_id.desc())
                .limit(limit + 1)
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
            "has_more": has_more,
            "next_before_id": link_rows[-1][0].message_id if has_more and link_rows else None,
        }
    conditions = [
        ArchivedMessage.telegram_account_id == account.id,
        ArchivedMessage.peer_id == peer_id,
        MessageVersion.version == ArchivedMessage.current_version,
        MediaAsset.media_type.in_(shared_media_asset_types(media_type, media_filter)),
    ]
    if before_id is not None:
        conditions.append(ArchivedMessage.message_id < before_id)
    rows = (
        await db.execute(
            select(MediaAsset, ArchivedMessage, MessageVersion)
            .join(MessageVersion, MessageVersion.id == MediaAsset.message_version_id)
            .join(ArchivedMessage, ArchivedMessage.id == MessageVersion.archived_message_id)
            .where(*conditions)
            .order_by(ArchivedMessage.message_id.desc(), MediaAsset.id.desc())
            .limit(limit + 1)
        )
    ).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [
            {
                **media_payload(media),
                "message_id": archived.message_id,
                "sent_at": archived.sent_at,
                "text": version.text,
                "content": version.content_json or {},
            }
            for media, archived, version in rows
        ],
        "has_more": has_more,
        "next_before_id": rows[-1][1].message_id if has_more and rows else None,
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
                    media_payload(media)
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
    user: CurrentUser,
    db: Db,
) -> FileResponse:
    if document_id <= 0:
        raise HTTPException(status_code=404, detail="自定义 Emoji 不存在")
    account = await owned_account(db, user.id)
    directory = settings.media_root / f"user_{user.id}" / "custom_emoji"
    candidates = [
        directory / f"{document_id}{extension}"
        for extension in (".tgs", ".webm", ".webp", ".png", ".bin")
    ]

    async def existing_file() -> Path | None:
        return next((path for path in candidates if path.is_file()), None)

    path = await existing_file()
    if path is None:
        lock = custom_emoji_locks.setdefault((account.id, document_id), asyncio.Lock())
        async with lock:
            path = await existing_file()
            if path is None:
                try:
                    async with runtime_manager.client(account.id) as client:
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
                        downloaded = await client.download_media(document, file=str(target))
                        downloaded_path = Path(downloaded) if downloaded else None
                        path = target if target.is_file() else downloaded_path
                except TelegramAuthorizationError as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
                except TelegramConnectionUnavailable as exc:
                    raise HTTPException(status_code=503, detail=str(exc)) from exc
                if path is None or not path.is_file():
                    raise HTTPException(status_code=404, detail="自定义 Emoji 下载失败")

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


@router.get("/media/{media_id}")
async def archive_media(
    media_id: int,
    user: CurrentUser,
    db: Db,
    download: bool = False,
) -> FileResponse:
    row = (
        await db.execute(
            select(MediaAsset, TelegramAccount)
            .join(MessageVersion, MessageVersion.id == MediaAsset.message_version_id)
            .join(ArchivedMessage, ArchivedMessage.id == MessageVersion.archived_message_id)
            .join(
                TelegramAccount,
                TelegramAccount.id == ArchivedMessage.telegram_account_id,
            )
            .where(MediaAsset.id == media_id, TelegramAccount.user_id == user.id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    media, _ = row
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
