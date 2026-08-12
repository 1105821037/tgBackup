from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from .dependencies import Csrf, CurrentUser, Db, get_session_record
from .models import TelegramAccount, User, WebSession
from .schemas import AdminUserCreate, AdminUserUpdate, PasswordChange
from .security import hash_password, verify_password


router = APIRouter(prefix="/api/users", tags=["users"])


def require_owner(user: User) -> None:
    if not user.is_owner:
        raise HTTPException(status_code=403, detail="仅管理员可以管理系统账户")


def user_payload(user: User, bound_user_ids: set[int] | None = None) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "is_owner": user.is_owner,
        "has_telegram": user.id in (bound_user_ids or set()),
        "created_at": user.created_at,
    }


@router.post("/me/password", status_code=204)
async def change_password(
    payload: PasswordChange,
    user: CurrentUser,
    db: Db,
    _: Csrf,
    session_record: WebSession = Depends(get_session_record),
) -> None:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    user.password_hash = hash_password(payload.new_password)
    await db.execute(
        delete(WebSession).where(
            WebSession.user_id == user.id,
            WebSession.id != session_record.id,
        )
    )
    await db.commit()


@router.get("")
async def list_users(user: CurrentUser, db: Db) -> dict[str, object]:
    require_owner(user)
    users = (await db.scalars(select(User).order_by(User.created_at, User.id))).all()
    bound_ids = set(
        await db.scalars(select(TelegramAccount.user_id).where(TelegramAccount.user_id.in_([item.id for item in users])))
    ) if users else set()
    return {"items": [user_payload(item, bound_ids) for item in users]}


@router.post("", status_code=201)
async def create_user(
    payload: AdminUserCreate, user: CurrentUser, db: Db, _: Csrf
) -> dict[str, object]:
    require_owner(user)
    created = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        is_owner=payload.is_owner,
    )
    db.add(created)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="该用户名已被使用") from None
    await db.refresh(created)
    return user_payload(created)


@router.put("/{user_id}")
async def update_user(
    user_id: int,
    payload: AdminUserUpdate,
    user: CurrentUser,
    db: Db,
    _: Csrf,
) -> dict[str, object]:
    require_owner(user)
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="请在账户安全中修改当前账户")
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="账户不存在")
    if payload.is_owner is False:
        # Lock all current owners in a stable order so two administrators cannot
        # concurrently demote each other after both observing the same count.
        owner_ids = list(
            await db.scalars(
                select(User.id)
                .where(User.is_owner.is_(True))
                .order_by(User.id)
                .with_for_update()
            )
        )
        if target.id in owner_ids and len(owner_ids) <= 1:
            raise HTTPException(status_code=409, detail="系统必须至少保留一个管理员")
    if payload.username is not None:
        target.username = payload.username
    if payload.is_owner is not None:
        target.is_owner = payload.is_owner
    if payload.password is not None:
        target.password_hash = hash_password(payload.password)
        await db.execute(delete(WebSession).where(WebSession.user_id == target.id))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="该用户名已被使用") from None
    await db.refresh(target)
    has_telegram = await db.scalar(
        select(func.count(TelegramAccount.id)).where(TelegramAccount.user_id == target.id)
    )
    return user_payload(target, {target.id} if has_telegram else set())
