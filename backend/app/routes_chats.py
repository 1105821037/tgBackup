from __future__ import annotations

from fastapi import APIRouter, Query

from .chat_service import list_chats, refresh_chats, save_rule
from .dependencies import Csrf, CurrentUser, Db
from .schemas import ChatBackupRuleInput


router = APIRouter(prefix="/api/chats", tags=["chats"])


@router.get("")
async def chats(
    user: CurrentUser,
    db: Db,
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, object]:
    return await list_chats(db, user, limit)


@router.post("/refresh")
async def refresh(
    user: CurrentUser,
    db: Db,
    _: Csrf,
) -> dict[str, object]:
    return await refresh_chats(db, user)


@router.put("/{peer_id}/rule")
async def update_rule(
    peer_id: int,
    payload: ChatBackupRuleInput,
    user: CurrentUser,
    db: Db,
    _: Csrf,
) -> dict[str, object]:
    return await save_rule(db, user, peer_id, payload)
