from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.db import SessionLocal, engine  # noqa: E402
from backend.app.entity_service import discover_placeholder  # noqa: E402
from backend.app.models import (  # noqa: E402
    ArchivedMessage,
    MessageVersion,
    TelegramEntity,
    TelegramEntityVersion,
)


async def main() -> None:
    linked_messages = 0
    linked_versions = 0
    async with SessionLocal.begin() as db:
        messages = list(
            await db.scalars(
                select(ArchivedMessage).where(
                    ArchivedMessage.sender_id.is_not(None),
                    ArchivedMessage.sender_entity_id.is_(None),
                )
            )
        )
        for message in messages:
            entity = await db.scalar(
                select(TelegramEntity).where(
                    TelegramEntity.telegram_account_id
                    == message.telegram_account_id,
                    TelegramEntity.peer_id == message.sender_id,
                )
            )
            if entity is None:
                entity, version_id = await discover_placeholder(
                    db,
                    message.telegram_account_id,
                    int(message.sender_id),
                    source="message_backfill",
                    priority=80,
                )
            else:
                version_id = await db.scalar(
                    select(TelegramEntityVersion.id).where(
                        TelegramEntityVersion.entity_id == entity.id,
                        TelegramEntityVersion.version == entity.current_version,
                    )
                )
            message.sender_entity_id = entity.id
            linked_messages += 1
            if version_id:
                versions = list(
                    await db.scalars(
                        select(MessageVersion).where(
                            MessageVersion.archived_message_id == message.id,
                            MessageVersion.sender_entity_version_id.is_(None),
                        )
                    )
                )
                for version in versions:
                    version.sender_entity_version_id = version_id
                    linked_versions += 1
    await engine.dispose()
    print(f"已关联归档消息 {linked_messages} 条、消息版本 {linked_versions} 条")


if __name__ == "__main__":
    asyncio.run(main())
