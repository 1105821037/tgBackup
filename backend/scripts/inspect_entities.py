from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.db import engine  # noqa: E402
from backend.app.config import get_settings  # noqa: E402


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


QUERIES = {
    "实体数": "SELECT COUNT(*) FROM telegram_entities",
    "资料版本数": "SELECT COUNT(*) FROM telegram_entity_versions",
    "已关联会话数": "SELECT COUNT(*) FROM telegram_dialogs WHERE entity_id IS NOT NULL",
    "头像文件记录数": "SELECT COUNT(*) FROM telegram_entity_photos",
    "消息发送者关联数": (
        "SELECT COUNT(*) FROM archived_messages WHERE sender_entity_id IS NOT NULL"
    ),
    "归档消息总数": "SELECT COUNT(*) FROM archived_messages",
    "运行中备份状态": "SELECT COUNT(*) FROM chat_backup_states WHERE status = 'running'",
    "运行中历史更新状态": (
        "SELECT COUNT(*) FROM history_update_states WHERE status = 'running'"
    ),
}


async def main() -> None:
    settings = get_settings()
    async with engine.connect() as connection:
        for label, query in QUERIES.items():
            value = await connection.scalar(text(query))
            print(f"{label}: {value}")
        jobs = await connection.execute(
            text(
                "SELECT status, refresh_kind, COUNT(*) AS total "
                "FROM telegram_entity_refresh_jobs "
                "GROUP BY status, refresh_kind ORDER BY status, refresh_kind"
            )
        )
        for status, refresh_kind, total in jobs:
            print(f"任务 {status}/{refresh_kind}: {total}")
        job_samples = await connection.execute(
            text(
                "SELECT j.status, j.refresh_kind, j.attempts, j.next_run_at, "
                "j.last_error_code, e.display_name FROM telegram_entity_refresh_jobs j "
                "JOIN telegram_entities e ON e.id = j.entity_id "
                "ORDER BY j.updated_at DESC LIMIT 10"
            )
        )
        for row in job_samples:
            print("任务样本:", tuple(row))
        samples = await connection.execute(
            text(
                "SELECT peer_id, entity_kind, display_name, current_version, "
                "access_state FROM telegram_entities ORDER BY last_observed_at DESC LIMIT 10"
            )
        )
        for row in samples:
            print("实体样本:", tuple(row))
        avatar_paths = await connection.scalars(
            text("SELECT relative_path FROM telegram_entity_photos WHERE status = 'available'")
        )
        missing = [
            relative_path
            for relative_path in avatar_paths
            if not (settings.avatar_root / relative_path).is_file()
        ]
        print(f"头像缺失文件记录数: {len(missing)}")
        for relative_path in missing[:10]:
            print("缺失头像:", relative_path)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
