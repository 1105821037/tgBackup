from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import errors, functions, types, utils

from .config import get_settings
from .db import SessionLocal
from .models import (
    TelegramAccount,
    ChatBackupRule,
    ChatBackupState,
    HistoryUpdateState,
    TelegramContact,
    TelegramEntity,
    TelegramEntityMetricDaily,
    TelegramEntityPhoto,
    TelegramEntityRefreshJob,
    TelegramEntityVersion,
)
from .realtime import realtime_hub


logger = logging.getLogger(__name__)
settings = get_settings()
ENTITY_REFRESH_KIND_RANK = {"photo": 0, "profile": 1}


def entity_refresh_worker_count(value: int) -> int:
    """Keep hydration parallelism useful without overwhelming one Telegram session."""
    return max(1, min(value, 8))


def entity_refresh_job_pause_seconds(value: float) -> float:
    """Guarantee that maintenance work yields measurable time to web requests."""
    return max(0.01, float(value))


@dataclass(slots=True)
class FullEntityPayload:
    entity: Any
    stable: dict[str, Any]
    participants_count: int | None = None
    online_count: int | None = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if hasattr(value, "to_dict"):
        return json_value(value.to_dict())
    return str(value)


def entity_kind(entity: Any) -> str:
    if isinstance(entity, types.User):
        return "bot" if entity.bot else "user"
    if isinstance(entity, (types.Channel, types.ChannelForbidden)):
        return "channel" if bool(getattr(entity, "broadcast", False)) else "supergroup"
    if isinstance(entity, (types.Chat, types.ChatForbidden)):
        return "group"
    return "unknown"


def peer_kind(peer_id: int) -> str:
    if peer_id > 0:
        return "user"
    if peer_id <= -1_000_000_000_000:
        return "channel"
    return "group"


def raw_telegram_id(peer_id: int) -> int:
    if peer_id > 0:
        return peer_id
    if peer_id <= -1_000_000_000_000:
        return abs(peer_id) - 1_000_000_000_000
    return abs(peer_id)


def display_name(entity: Any, peer_id: int) -> str:
    title = getattr(entity, "title", None)
    if title:
        return title
    name = " ".join(
        value
        for value in (
            getattr(entity, "first_name", None),
            getattr(entity, "last_name", None),
        )
        if value
    )
    return name or getattr(entity, "username", None) or str(peer_id)


def photo_id(entity: Any) -> int | None:
    return getattr(getattr(entity, "photo", None), "photo_id", None)


def basic_profile(entity: Any, peer_id: int) -> dict[str, Any]:
    usernames = []
    for item in getattr(entity, "usernames", None) or []:
        username = getattr(item, "username", None)
        if username:
            usernames.append(
                {
                    "username": username,
                    "active": bool(getattr(item, "active", False)),
                    "editable": bool(getattr(item, "editable", False)),
                }
            )
    return {
        "peer_id": peer_id,
        "telegram_id": int(getattr(entity, "id", raw_telegram_id(peer_id))),
        "entity_kind": entity_kind(entity),
        "display_name": display_name(entity, peer_id),
        "username": getattr(entity, "username", None),
        "usernames": usernames,
        "first_name": getattr(entity, "first_name", None),
        "last_name": getattr(entity, "last_name", None),
        "photo_id": photo_id(entity),
        "is_verified": bool(getattr(entity, "verified", False)),
        "is_deleted": bool(getattr(entity, "deleted", False)),
        "is_scam": bool(getattr(entity, "scam", False)),
        "is_fake": bool(getattr(entity, "fake", False)),
        "is_restricted": bool(getattr(entity, "restricted", False)),
        "is_bot": bool(getattr(entity, "bot", False)),
        "is_broadcast": bool(getattr(entity, "broadcast", False)),
        "is_megagroup": bool(getattr(entity, "megagroup", False)),
        "is_gigagroup": bool(getattr(entity, "gigagroup", False)),
        "is_forum": bool(getattr(entity, "forum", False)),
        "signatures": bool(getattr(entity, "signatures", False)),
        "signature_profiles": bool(getattr(entity, "signature_profiles", False)),
        "restriction_reason": json_value(getattr(entity, "restriction_reason", None)),
    }


def stable_hash(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


async def enqueue_refresh_job(
    db: AsyncSession,
    entity_id: int,
    refresh_kind: str,
    *,
    priority: int,
    next_run_at: datetime | None = None,
) -> None:
    due = next_run_at or utcnow()
    job = await db.scalar(
        select(TelegramEntityRefreshJob).where(
            TelegramEntityRefreshJob.entity_id == entity_id,
            TelegramEntityRefreshJob.refresh_kind == refresh_kind,
        )
    )
    if job is None:
        db.add(
            TelegramEntityRefreshJob(
                entity_id=entity_id,
                refresh_kind=refresh_kind,
                priority=priority,
                status="pending",
                next_run_at=due,
            )
        )
        return
    job.priority = max(job.priority, priority)
    if job.status != "running":
        job.status = "pending"
        current_due = aware(job.next_run_at)
        if current_due is None or due < current_due:
            job.next_run_at = due


async def _current_version_id(db: AsyncSession, entity: TelegramEntity) -> int | None:
    return await db.scalar(
        select(TelegramEntityVersion.id).where(
            TelegramEntityVersion.entity_id == entity.id,
            TelegramEntityVersion.version == entity.current_version,
        )
    )


async def discover_entity(
    db: AsyncSession,
    account_id: int,
    entity_object: Any,
    *,
    source: str,
    priority: int = 60,
    full_payload: FullEntityPayload | None = None,
) -> tuple[TelegramEntity, int | None]:
    now = utcnow()
    peer_id = utils.get_peer_id(entity_object)
    basic = basic_profile(entity_object, peer_id)
    existing = await db.scalar(
        select(TelegramEntity).where(
            TelegramEntity.telegram_account_id == account_id,
            TelegramEntity.peer_id == peer_id,
        )
    )
    is_min = bool(getattr(entity_object, "min", False))
    existing_profile = dict(existing.profile_json or {}) if existing else {}
    if existing and is_min:
        for key in (
            "display_name",
            "username",
            "usernames",
            "first_name",
            "last_name",
            "photo_id",
            "is_verified",
            "is_deleted",
            "is_scam",
            "is_fake",
            "is_restricted",
            "is_bot",
            "is_broadcast",
            "is_megagroup",
            "is_gigagroup",
            "is_forum",
            "signatures",
            "signature_profiles",
            "restriction_reason",
        ):
            if key in existing_profile:
                basic[key] = existing_profile[key]
    profile = {**existing_profile, **basic}
    if full_payload:
        profile.update(full_payload.stable)
    about = (
        full_payload.stable.get("about")
        if full_payload and "about" in full_payload.stable
        else (existing.about if existing else None)
    )
    profile["about"] = about

    incoming_phone = getattr(entity_object, "phone", None)
    old_phone = existing.phone if existing else None
    if incoming_phone is None and existing:
        incoming_phone = old_phone

    snapshot = {
        key: profile.get(key)
        for key in (
            "peer_id",
            "telegram_id",
            "entity_kind",
            "display_name",
            "username",
            "usernames",
            "first_name",
            "last_name",
            "photo_id",
            "about",
            "is_verified",
            "is_deleted",
            "is_scam",
            "is_fake",
            "is_restricted",
            "is_bot",
            "is_broadcast",
            "is_megagroup",
            "is_gigagroup",
            "is_forum",
            "restriction_reason",
            "linked_chat_id",
            "location",
        )
    }
    digest = stable_hash(snapshot)
    phone_changed = existing is not None and incoming_phone != old_phone
    previous_photo_id = existing.photo_id if existing else None

    version_row: TelegramEntityVersion | None = None
    if existing is None:
        existing = TelegramEntity(
            telegram_account_id=account_id,
            peer_id=peer_id,
            telegram_id=basic["telegram_id"],
            entity_kind=basic["entity_kind"],
            display_name=basic["display_name"],
            username=basic["username"],
            first_name=basic["first_name"],
            last_name=basic["last_name"],
            phone=incoming_phone,
            about=about,
            photo_id=basic["photo_id"],
            is_contact=bool(getattr(entity_object, "contact", False)),
            is_verified=basic["is_verified"],
            is_deleted=basic["is_deleted"],
            is_scam=basic["is_scam"],
            is_fake=basic["is_fake"],
            access_state="available",
            current_profile_hash=digest,
            current_version=1,
            profile_json=profile,
            first_observed_at=now,
            last_observed_at=now,
            last_basic_refreshed_at=now,
            last_full_refreshed_at=now if full_payload else None,
            next_refresh_at=now,
        )
        db.add(existing)
        await db.flush()
        version_row = TelegramEntityVersion(
            entity_id=existing.id,
            version=1,
            profile_hash=digest,
            snapshot_json=snapshot,
            phone=incoming_phone,
            source=source,
            observed_at=now,
        )
        db.add(version_row)
    else:
        existing.last_observed_at = now
        existing.last_basic_refreshed_at = now
        existing.access_state = "available"
        if full_payload:
            existing.last_full_refreshed_at = now
        if existing.current_profile_hash != digest or phone_changed:
            next_version = existing.current_version + 1
            version_row = TelegramEntityVersion(
                entity_id=existing.id,
                version=next_version,
                profile_hash=digest,
                snapshot_json=snapshot,
                phone=incoming_phone,
                source=source,
                observed_at=now,
            )
            db.add(version_row)
            existing.current_version = next_version
            existing.current_profile_hash = digest
        existing.telegram_id = profile["telegram_id"]
        existing.entity_kind = profile["entity_kind"]
        existing.display_name = profile["display_name"]
        existing.username = profile["username"]
        existing.first_name = profile["first_name"]
        existing.last_name = profile["last_name"]
        existing.phone = incoming_phone
        existing.about = about
        existing.photo_id = profile["photo_id"]
        if not is_min:
            existing.is_contact = bool(getattr(entity_object, "contact", False))
        existing.is_verified = profile["is_verified"]
        existing.is_deleted = profile["is_deleted"]
        existing.is_scam = profile["is_scam"]
        existing.is_fake = profile["is_fake"]
        existing.profile_json = profile

    await db.flush()
    if version_row:
        await db.flush()
        version_id = version_row.id
    else:
        version_id = await _current_version_id(db, existing)

    if isinstance(entity_object, types.User) and not is_min:
        contact = await db.scalar(
            select(TelegramContact).where(
                TelegramContact.telegram_account_id == account_id,
                TelegramContact.entity_id == existing.id,
            )
        )
        is_contact = bool(getattr(entity_object, "contact", False))
        is_mutual = bool(getattr(entity_object, "mutual_contact", False))
        if contact is None:
            contact = TelegramContact(
                telegram_account_id=account_id,
                entity_id=existing.id,
                is_contact=is_contact,
                is_mutual_contact=is_mutual,
                first_observed_at=now,
                last_observed_at=now,
            )
            db.add(contact)
        else:
            contact.is_contact = is_contact
            contact.is_mutual_contact = is_mutual
            contact.last_observed_at = now

    full_due = aware(existing.last_full_refreshed_at)
    if source != "profile_refresh" and (
        full_due is None
        or full_due + timedelta(hours=settings.telegram_entity_profile_ttl_hours) <= now
    ):
        await enqueue_refresh_job(
            db,
            existing.id,
            "profile",
            priority=priority,
            next_run_at=now,
        )
    if profile["photo_id"] and (
        previous_photo_id != profile["photo_id"] or existing.last_photo_checked_at is None
    ):
        await enqueue_refresh_job(
            db,
            existing.id,
            "photo",
            priority=max(priority - 5, 1),
            next_run_at=now,
        )
    return existing, version_id


async def discover_placeholder(
    db: AsyncSession,
    account_id: int,
    peer_id: int,
    *,
    source: str,
    priority: int = 90,
) -> tuple[TelegramEntity, int | None]:
    existing = await db.scalar(
        select(TelegramEntity).where(
            TelegramEntity.telegram_account_id == account_id,
            TelegramEntity.peer_id == peer_id,
        )
    )
    if existing:
        await enqueue_refresh_job(
            db, existing.id, "profile", priority=priority, next_run_at=utcnow()
        )
        return existing, await _current_version_id(db, existing)
    now = utcnow()
    kind = peer_kind(peer_id)
    snapshot = {
        "peer_id": peer_id,
        "telegram_id": raw_telegram_id(peer_id),
        "entity_kind": kind,
        "display_name": str(peer_id),
        "placeholder": True,
    }
    digest = stable_hash(snapshot)
    entity = TelegramEntity(
        telegram_account_id=account_id,
        peer_id=peer_id,
        telegram_id=raw_telegram_id(peer_id),
        entity_kind=kind,
        display_name=str(peer_id),
        current_profile_hash=digest,
        current_version=1,
        profile_json=snapshot,
        first_observed_at=now,
        last_observed_at=now,
        next_refresh_at=now,
    )
    db.add(entity)
    await db.flush()
    version = TelegramEntityVersion(
        entity_id=entity.id,
        version=1,
        profile_hash=digest,
        snapshot_json=snapshot,
        source=source,
        observed_at=now,
    )
    db.add(version)
    await db.flush()
    await enqueue_refresh_job(
        db, entity.id, "profile", priority=priority, next_run_at=now
    )
    return entity, version.id


async def discover_message_sender(
    db: AsyncSession,
    account_id: int,
    message: Any,
    *,
    source: str,
    priority: int = 90,
) -> tuple[TelegramEntity | None, int | None]:
    sender = getattr(message, "sender", None)
    if isinstance(
        sender,
        (
            types.User,
            types.Channel,
            types.Chat,
            types.ChannelForbidden,
            types.ChatForbidden,
        ),
    ):
        return await discover_entity(
            db,
            account_id,
            sender,
            source=source,
            priority=priority,
        )
    sender_id = getattr(message, "sender_id", None)
    if sender_id is None:
        return None, None
    return await discover_placeholder(
        db,
        account_id,
        int(sender_id),
        source=source,
        priority=priority,
    )


def require_message_sender_link(
    message: Any,
    entity: TelegramEntity | None,
    entity_version_id: int | None,
) -> None:
    """Prevent messages with a Telegram sender from losing entity history."""
    if getattr(message, "sender_id", None) is not None and (
        entity is None or entity_version_id is None
    ):
        raise RuntimeError("消息发送者实体关联未建立")


async def discover_message_forward_sender(
    db: AsyncSession,
    account_id: int,
    message: Any,
    *,
    source: str,
    priority: int = 90,
) -> TelegramEntity | None:
    """Cache the peer Telegram uses as the sender in Saved Messages."""
    forwarded = getattr(message, "fwd_from", None)
    if forwarded is None:
        return None

    forward = getattr(message, "forward", None)
    origin = getattr(forward, "sender", None) or getattr(forward, "chat", None)
    if isinstance(
        origin,
        (
            types.User,
            types.Channel,
            types.Chat,
            types.ChannelForbidden,
            types.ChatForbidden,
        ),
    ):
        entity, _ = await discover_entity(
            db,
            account_id,
            origin,
            source=source,
            priority=priority,
        )
        return entity

    origin_peer = (
        getattr(forwarded, "from_id", None)
        or getattr(forwarded, "saved_from_id", None)
        or getattr(forwarded, "saved_from_peer", None)
    )
    if origin_peer is None:
        return None
    peer_id = int(utils.get_peer_id(origin_peer))
    entity, _ = await discover_placeholder(
        db,
        account_id,
        peer_id,
        source=source,
        priority=priority,
    )
    return entity


async def discover_message_via_bot(
    db: AsyncSession,
    account_id: int,
    message: Any,
    *,
    source: str,
    priority: int = 90,
) -> TelegramEntity | None:
    via_bot_id = getattr(message, "via_bot_id", None)
    if via_bot_id is None:
        return None
    bot = getattr(message, "via_bot", None)
    if isinstance(bot, types.User):
        entity, _ = await discover_entity(
            db,
            account_id,
            bot,
            source=source,
            priority=priority,
        )
        return entity
    entity, _ = await discover_placeholder(
        db,
        account_id,
        int(via_bot_id),
        source=source,
        priority=priority,
    )
    return entity


async def fetch_full_entity(client: Any, entity: Any) -> FullEntityPayload:
    if isinstance(entity, types.User):
        response = await client(functions.users.GetFullUserRequest(entity))
        updated = next(
            (item for item in response.users if item.id == entity.id), entity
        )
        full = response.full_user
        return FullEntityPayload(
            entity=updated,
            stable={
                "about": getattr(full, "about", None),
                "personal_channel_id": getattr(full, "personal_channel_id", None),
                "bot_info": json_value(getattr(full, "bot_info", None)),
            },
        )
    if isinstance(entity, types.Channel):
        response = await client(functions.channels.GetFullChannelRequest(entity))
        updated = next(
            (item for item in response.chats if item.id == entity.id), entity
        )
        full = response.full_chat
        return FullEntityPayload(
            entity=updated,
            stable={
                "about": getattr(full, "about", None),
                "linked_chat_id": getattr(full, "linked_chat_id", None),
                "location": json_value(getattr(full, "location", None)),
                "can_view_participants": bool(
                    getattr(full, "can_view_participants", False)
                ),
                "available_reactions": json_value(
                    getattr(full, "available_reactions", None)
                ),
            },
            participants_count=getattr(full, "participants_count", None),
            online_count=getattr(full, "online_count", None),
        )
    if isinstance(entity, types.Chat):
        response = await client(functions.messages.GetFullChatRequest(entity.id))
        updated = next(
            (item for item in response.chats if item.id == entity.id), entity
        )
        full = response.full_chat
        participants = getattr(full, "participants", None)
        participant_items = getattr(participants, "participants", None)
        return FullEntityPayload(
            entity=updated,
            stable={
                "about": getattr(full, "about", None),
                "available_reactions": json_value(
                    getattr(full, "available_reactions", None)
                ),
            },
            participants_count=(len(participant_items) if participant_items else None),
        )
    return FullEntityPayload(entity=entity, stable={})


async def upsert_entity_metrics(
    db: AsyncSession,
    entity_id: int,
    payload: FullEntityPayload,
    now: datetime,
) -> None:
    metric = await db.scalar(
        select(TelegramEntityMetricDaily).where(
            TelegramEntityMetricDaily.entity_id == entity_id,
            TelegramEntityMetricDaily.sample_date == now.astimezone().date(),
        )
    )
    if metric is None:
        db.add(
            TelegramEntityMetricDaily(
                entity_id=entity_id,
                sample_date=now.astimezone().date(),
                participants_count=payload.participants_count,
                online_count=payload.online_count,
                observed_at=now,
            )
        )
    else:
        metric.participants_count = payload.participants_count
        metric.online_count = payload.online_count
        metric.observed_at = now


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def download_avatar_variant(
    client: Any,
    telegram_entity: Any,
    entity: TelegramEntity,
    user_id: int,
    variant: str,
) -> dict[str, Any] | None:
    if entity.photo_id is None:
        return None
    relative = (
        Path(f"user_{user_id}")
        / f"{entity.entity_kind}_{entity.telegram_id}"
        / str(entity.photo_id)
        / f"{variant}.jpg"
    )
    target = settings.avatar_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    if temporary.exists():
        temporary.unlink()
    downloaded = await client.download_profile_photo(
        telegram_entity,
        file=str(temporary),
        download_big=variant == "big",
    )
    if not downloaded:
        return None
    downloaded_path = Path(downloaded)
    if not downloaded_path.exists():
        raise FileNotFoundError("Telegram 头像下载结果不存在")
    os.replace(downloaded_path, target)
    return {
        "variant": variant,
        "relative_path": relative.as_posix(),
        "size_bytes": target.stat().st_size,
        "sha256": await asyncio.to_thread(sha256_file, target),
    }


class EntityRefreshCoordinator:
    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[None]] = []
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if any(not task.done() for task in self._tasks):
            return
        now = utcnow()
        async with SessionLocal.begin() as db:
            await db.execute(
                update(TelegramEntityRefreshJob)
                .where(
                    TelegramEntityRefreshJob.status == "running",
                    or_(
                        TelegramEntityRefreshJob.lease_until.is_(None),
                        TelegramEntityRefreshJob.lease_until <= now,
                    ),
                )
                .values(status="pending", lease_until=None)
            )
        self._stop.clear()
        worker_count = entity_refresh_worker_count(
            settings.telegram_entity_worker_concurrency
        )
        # Keep one lane moving profile hydration while the remaining workers
        # drain visible avatar work. General lanes automatically help profiles
        # as soon as the avatar backlog is empty.
        self._tasks = [
            asyncio.create_task(
                self._loop("profile" if worker_count > 1 and index == 0 else None),
                name=f"tg-entity-refresh-worker-{index + 1}",
            )
            for index in range(worker_count)
        ]

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _loop(self, refresh_kind: str | None = None) -> None:
        while not self._stop.is_set():
            try:
                worked = await self.tick(refresh_kind)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telegram entity refresh tick failed")
                worked = False
            if worked:
                # Entity hydration is background maintenance. A small pacing
                # delay prevents a large queue from monopolising the event
                # loop and database pool while keeping avatar progress quick.
                await asyncio.sleep(
                    entity_refresh_job_pause_seconds(
                        settings.telegram_entity_job_pause_seconds
                    )
                )
                continue
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=settings.telegram_entity_refresh_interval_seconds,
                )
            except TimeoutError:
                pass

    async def claim_job(self, refresh_kind: str | None = None) -> int | None:
        now = utcnow()
        busy_accounts = select(ChatBackupRule.telegram_account_id).join(
            ChatBackupState, ChatBackupState.rule_id == ChatBackupRule.id
        ).where(ChatBackupState.status == "running")
        history_busy_accounts = select(ChatBackupRule.telegram_account_id).join(
            HistoryUpdateState, HistoryUpdateState.rule_id == ChatBackupRule.id
        ).where(HistoryUpdateState.status == "running")
        async with SessionLocal.begin() as db:
            job = await db.scalar(
                select(TelegramEntityRefreshJob)
                .join(TelegramEntity, TelegramEntity.id == TelegramEntityRefreshJob.entity_id)
                .join(
                    TelegramAccount,
                    TelegramAccount.id == TelegramEntity.telegram_account_id,
                )
                .where(
                    TelegramAccount.status == "active",
                    TelegramEntity.telegram_account_id.not_in(busy_accounts),
                    TelegramEntity.telegram_account_id.not_in(history_busy_accounts),
                    *(
                        [TelegramEntityRefreshJob.refresh_kind == refresh_kind]
                        if refresh_kind
                        else []
                    ),
                    or_(
                        and_(
                            TelegramEntityRefreshJob.status.in_(["pending", "error"]),
                            TelegramEntityRefreshJob.next_run_at <= now,
                        ),
                        and_(
                            TelegramEntityRefreshJob.status == "running",
                            TelegramEntityRefreshJob.lease_until <= now,
                        ),
                    ),
                )
                .order_by(
                    # A current avatar is visible throughout the UI. Always
                    # drain one-shot photo work before recurring profile work,
                    # regardless of the discovery source's numeric priority.
                    case(
                        ENTITY_REFRESH_KIND_RANK,
                        value=TelegramEntityRefreshJob.refresh_kind,
                        else_=2,
                    ),
                    TelegramEntityRefreshJob.priority.desc(),
                    TelegramEntityRefreshJob.next_run_at,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            job.status = "running"
            job.attempts += 1
            job.lease_until = now + timedelta(
                seconds=settings.telegram_entity_worker_lease_seconds
            )
            job.last_error_code = None
            job.last_error = None
            return job.id

    async def tick(self, refresh_kind: str | None = None) -> bool:
        job_id = await self.claim_job(refresh_kind)
        if job_id is None and refresh_kind is not None:
            # A dedicated profile lane lends its capacity to the general queue
            # when no profile is due, so idle capacity still drains avatars.
            job_id = await self.claim_job()
        if job_id is None:
            return False
        try:
            await self.execute_job(job_id)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            await self.fail_job(job_id, exc)
        return True

    async def execute_job(self, job_id: int) -> None:
        async with SessionLocal() as db:
            job = await db.get(TelegramEntityRefreshJob, job_id)
            entity = await db.get(TelegramEntity, job.entity_id) if job else None
            account = (
                await db.get(TelegramAccount, entity.telegram_account_id)
                if entity
                else None
            )
        if not job or not entity or not account:
            await self.complete_job(job_id, recurring=False)
            return

        from .telegram_runtime import runtime_manager

        if job.refresh_kind == "profile":
            async with runtime_manager.client(account.id) as client:
                basic = await client.get_entity(entity.peer_id)
                payload = await fetch_full_entity(client, basic)
            now = utcnow()
            async with SessionLocal.begin() as db:
                stored, _ = await discover_entity(
                    db,
                    account.id,
                    payload.entity,
                    source="profile_refresh",
                    priority=job.priority,
                    full_payload=payload,
                )
                await upsert_entity_metrics(db, stored.id, payload, now)
                stored.next_refresh_at = now + timedelta(
                    hours=settings.telegram_entity_profile_ttl_hours
                )
                stored_id = stored.id
                snapshot = {
                    "entity_id": stored.id,
                    "peer_id": stored.peer_id,
                    "entity_kind": stored.entity_kind,
                    "display_name": stored.display_name,
                    "username": stored.username,
                    "photo_id": stored.photo_id,
                    "current_version": stored.current_version,
                }
            await self.complete_job(
                job_id,
                recurring=True,
                next_run_at=now
                + timedelta(hours=settings.telegram_entity_profile_ttl_hours),
            )
            await realtime_hub.publish(
                account.user_id, "telegram.entity.updated", snapshot
            )
            return

        if job.refresh_kind == "photo":
            async with runtime_manager.client(account.id) as client:
                telegram_entity = await client.get_entity(entity.peer_id)
                current_photo_id = photo_id(telegram_entity)
                if current_photo_id != entity.photo_id:
                    async with SessionLocal.begin() as db:
                        entity, _ = await discover_entity(
                            db,
                            account.id,
                            telegram_entity,
                            source="photo_refresh",
                            priority=job.priority,
                        )
                if entity.photo_id:
                    for variant in ("small", "big"):
                        asset = await self.existing_photo(entity, variant)
                        if asset is None:
                            downloaded = await download_avatar_variant(
                                client,
                                telegram_entity,
                                entity,
                                account.user_id,
                                variant,
                            )
                            if downloaded:
                                await self.persist_photo_asset(
                                    entity.id, entity.photo_id, downloaded
                                )
                                await realtime_hub.publish(
                                    account.user_id,
                                    "telegram.entity.avatar.updated",
                                    {
                                        "entity_id": entity.id,
                                        "peer_id": entity.peer_id,
                                        "photo_id": entity.photo_id,
                                        "variants": [variant],
                                    },
                                )
            now = utcnow()
            async with SessionLocal.begin() as db:
                stored = await db.get(TelegramEntity, entity.id)
                if stored:
                    stored.last_photo_checked_at = now
            await self.complete_job(job_id, recurring=False)
            return

        await self.complete_job(job_id, recurring=False)

    async def persist_photo_asset(
        self,
        entity_id: int,
        telegram_photo_id: int,
        item: dict[str, Any],
    ) -> None:
        """Expose the small avatar immediately instead of waiting for the big copy."""
        now = utcnow()
        async with SessionLocal.begin() as db:
            asset = await db.scalar(
                select(TelegramEntityPhoto).where(
                    TelegramEntityPhoto.entity_id == entity_id,
                    TelegramEntityPhoto.telegram_photo_id == telegram_photo_id,
                    TelegramEntityPhoto.variant == item["variant"],
                )
            )
            if asset is None:
                asset = TelegramEntityPhoto(
                    entity_id=entity_id,
                    telegram_photo_id=telegram_photo_id,
                    variant=item["variant"],
                    first_observed_at=now,
                )
                db.add(asset)
            asset.relative_path = item["relative_path"]
            asset.size_bytes = item["size_bytes"]
            asset.sha256 = item["sha256"]
            asset.mime_type = "image/jpeg"
            asset.status = "available"
            asset.last_observed_at = now

    async def existing_photo(
        self, entity: TelegramEntity, variant: str
    ) -> TelegramEntityPhoto | None:
        async with SessionLocal() as db:
            asset = await db.scalar(
                select(TelegramEntityPhoto).where(
                    TelegramEntityPhoto.entity_id == entity.id,
                    TelegramEntityPhoto.telegram_photo_id == entity.photo_id,
                    TelegramEntityPhoto.variant == variant,
                )
            )
            if asset and (settings.avatar_root / asset.relative_path).exists():
                return asset
            return None

    async def complete_job(
        self,
        job_id: int,
        *,
        recurring: bool,
        next_run_at: datetime | None = None,
    ) -> None:
        async with SessionLocal.begin() as db:
            job = await db.get(TelegramEntityRefreshJob, job_id)
            if not job:
                return
            job.status = "pending" if recurring else "success"
            job.attempts = 0
            job.lease_until = None
            job.last_error_code = None
            job.last_error = None
            job.next_run_at = next_run_at or (
                utcnow() + timedelta(days=3650)
            )

    async def fail_job(self, job_id: int, exc: BaseException) -> None:
        now = utcnow()
        name = type(exc).__name__
        async with SessionLocal.begin() as db:
            job = await db.get(TelegramEntityRefreshJob, job_id)
            if not job:
                return
            if isinstance(exc, errors.FloodWaitError):
                delay = timedelta(seconds=exc.seconds + 5)
                code = "telegram_flood_wait"
            elif name in {
                "ChannelPrivateError",
                "ChatForbiddenError",
                "UserPrivacyRestrictedError",
            }:
                delay = timedelta(days=7)
                code = "entity_inaccessible"
                entity = await db.get(TelegramEntity, job.entity_id)
                if entity:
                    entity.access_state = "restricted"
            else:
                delay = timedelta(minutes=min(5 * 2 ** max(job.attempts - 1, 0), 360))
                code = "entity_refresh_failed"
            job.status = "error"
            job.lease_until = None
            job.next_run_at = now + delay
            job.last_error_code = code
            job.last_error = f"{name}: {exc}"[:4000]


entity_refresh_coordinator = EntityRefreshCoordinator()
