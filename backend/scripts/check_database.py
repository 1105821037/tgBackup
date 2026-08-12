from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.db import engine  # noqa: E402


async def main() -> None:
    async with engine.connect() as connection:
        tables = (await connection.execute(text("SHOW TABLES"))).scalars().all()
        users = await connection.scalar(text("SELECT COUNT(*) FROM users"))
        chat_backup_rules = await connection.scalar(text("SELECT COUNT(*) FROM chat_backup_rules"))
        web_sessions = await connection.scalar(text("SELECT COUNT(*) FROM web_sessions"))
        session_expiry = await connection.scalar(text("SELECT MAX(expires_at) FROM web_sessions"))
    await engine.dispose()
    print("tables", sorted(tables))
    print("users", users)
    print("chat_backup_rules", chat_backup_rules)
    print("web_sessions", web_sessions)
    print("latest_session_expiry", session_expiry)


if __name__ == "__main__":
    asyncio.run(main())
