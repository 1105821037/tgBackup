from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select

from .config import get_settings
from .dependencies import CurrentUser, Db
from .models import (
    TelegramAccount,
    TelegramEntity,
    TelegramEntityMetricDaily,
    TelegramEntityPhoto,
    TelegramEntityVersion,
)
from .profile_crypto import decrypt_profile_value
from .telegram_auth import mask_phone


router = APIRouter(prefix="/api/entities", tags=["entities"])
settings = get_settings()


async def owned_entity(
    db: Db,
    user_id: int,
    entity_id: int,
) -> TelegramEntity:
    entity = await db.scalar(
        select(TelegramEntity)
        .join(
            TelegramAccount,
            TelegramAccount.id == TelegramEntity.telegram_account_id,
        )
        .where(TelegramEntity.id == entity_id, TelegramAccount.user_id == user_id)
    )
    if entity is None:
        raise HTTPException(status_code=404, detail="实体资料不存在")
    return entity


def avatar_url(entity_id: int, photo_id: int, variant: str) -> str:
    return f"/api/entities/{entity_id}/avatar/{photo_id}/{variant}"


@router.get("/{entity_id}")
async def entity_detail(
    entity_id: int,
    user: CurrentUser,
    db: Db,
    version_limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    entity = await owned_entity(db, user.id, entity_id)
    versions = (
        await db.scalars(
            select(TelegramEntityVersion)
            .where(TelegramEntityVersion.entity_id == entity.id)
            .order_by(TelegramEntityVersion.version.desc())
            .limit(version_limit)
        )
    ).all()
    photos = (
        await db.scalars(
            select(TelegramEntityPhoto).where(
                TelegramEntityPhoto.entity_id == entity.id,
                TelegramEntityPhoto.telegram_photo_id == entity.photo_id,
                TelegramEntityPhoto.status == "available",
            )
        )
    ).all()
    metrics = (
        await db.scalars(
            select(TelegramEntityMetricDaily)
            .where(TelegramEntityMetricDaily.entity_id == entity.id)
            .order_by(TelegramEntityMetricDaily.sample_date.desc())
            .limit(30)
        )
    ).all()
    phone = decrypt_profile_value(entity.phone_ciphertext)
    return {
        "id": entity.id,
        "peer_id": entity.peer_id,
        "telegram_id": entity.telegram_id,
        "kind": entity.entity_kind,
        "display_name": entity.display_name,
        "username": entity.username,
        "first_name": entity.first_name,
        "last_name": entity.last_name,
        "phone_masked": mask_phone(phone) if phone else None,
        "about": entity.about,
        "is_contact": entity.is_contact,
        "is_verified": entity.is_verified,
        "is_deleted": entity.is_deleted,
        "is_scam": entity.is_scam,
        "is_fake": entity.is_fake,
        "access_state": entity.access_state,
        "current_version": entity.current_version,
        "photo_id": entity.photo_id,
        "avatars": {
            photo.variant: avatar_url(entity.id, photo.telegram_photo_id, photo.variant)
            for photo in photos
        },
        "first_observed_at": entity.first_observed_at,
        "last_observed_at": entity.last_observed_at,
        "last_full_refreshed_at": entity.last_full_refreshed_at,
        "versions": [
            {
                "version": version.version,
                "source": version.source,
                "observed_at": version.observed_at,
                "snapshot": version.snapshot_json,
            }
            for version in versions
        ],
        "metrics": [
            {
                "date": metric.sample_date,
                "participants_count": metric.participants_count,
                "online_count": metric.online_count,
                "observed_at": metric.observed_at,
            }
            for metric in metrics
        ],
    }


@router.get("/{entity_id}/avatar/{photo_id}/{variant}")
async def entity_avatar(
    entity_id: int,
    photo_id: int,
    variant: str,
    user: CurrentUser,
    db: Db,
) -> FileResponse:
    if variant not in {"small", "big"}:
        raise HTTPException(status_code=404, detail="头像规格不存在")
    entity = await owned_entity(db, user.id, entity_id)
    photo = await db.scalar(
        select(TelegramEntityPhoto).where(
            TelegramEntityPhoto.entity_id == entity.id,
            TelegramEntityPhoto.telegram_photo_id == photo_id,
            TelegramEntityPhoto.variant == variant,
            TelegramEntityPhoto.status == "available",
        )
    )
    if photo is None:
        raise HTTPException(status_code=404, detail="头像不存在")
    root = settings.avatar_root.resolve()
    path = (root / photo.relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="头像路径无效") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="头像文件不存在")
    return FileResponse(
        Path(path),
        media_type=photo.mime_type or "image/jpeg",
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "ETag": f'"{photo.sha256}"',
        },
    )
