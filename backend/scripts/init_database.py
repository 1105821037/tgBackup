from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.config import get_settings  # noqa: E402
from backend.app.db import engine  # noqa: E402
from backend.app.models import Base  # noqa: E402


async def main(*, reset: bool = False) -> None:
    settings = get_settings()
    server_engine = create_async_engine(settings.server_database_url)
    safe_database = settings.mysql_database.replace("`", "``")
    async with server_engine.begin() as connection:
        if reset:
            await connection.execute(text(f"DROP DATABASE IF EXISTS `{safe_database}`"))
        await connection.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{safe_database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
    await server_engine.dispose()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    action = "已重建" if reset else "和基础表已就绪"
    print(f"数据库 {settings.mysql_database} {action}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="创建当前版本的数据库结构")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="删除现有数据库及全部数据，然后按当前模型重新创建",
    )
    args = parser.parse_args()
    asyncio.run(main(reset=args.reset))
