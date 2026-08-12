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
        for table in (
            "chat_backup_states",
            "backup_runs",
            "archived_messages",
            "message_versions",
            "media_assets",
            "backup_item_events",
            "history_update_states",
            "history_update_runs",
            "history_update_item_events",
            "message_metric_daily",
        ):
            count = await connection.scalar(text(f"SELECT COUNT(*) FROM {table}"))
            print(table, count)
        states = (
            await connection.execute(
                text(
                    "SELECT rule_id, last_message_id, status, last_schedule_key, "
                    "last_error_code, last_error "
                    "FROM chat_backup_states ORDER BY rule_id"
                )
            )
        ).mappings().all()
        runs = (
            await connection.execute(
                text(
                    "SELECT id, rule_id, status, start_cursor, end_cursor, fetched_count, "
                    "stored_count, skipped_count, media_count, error_code, started_at, finished_at "
                    "FROM backup_runs ORDER BY id DESC LIMIT 10"
                )
            )
        ).mappings().all()
        history_runs = (
            await connection.execute(
                text(
                    "SELECT id, rule_id, status, candidate_count, checked_count, "
                    "changed_count, deleted_count, media_completed_count, error_count "
                    "FROM history_update_runs ORDER BY id DESC LIMIT 10"
                )
            )
        ).mappings().all()
        backup_events = (
            await connection.execute(
                text(
                    "SELECT id, run_id, peer_id, message_id, level, code, detail, created_at "
                    "FROM backup_item_events ORDER BY id DESC LIMIT 20"
                )
            )
        ).mappings().all()
        message_summary = (
            await connection.execute(
                text(
                    "SELECT COUNT(*) AS messages, SUM(content_hash_schema = 3) AS schema3, "
                    "SUM(history_update_count) AS updates, SUM(is_deleted) AS deleted "
                    "FROM archived_messages"
                )
            )
        ).mappings().one()
        content_kinds = (
            await connection.execute(
                text(
                    "SELECT content_kind, COUNT(*) AS messages FROM message_versions "
                    "GROUP BY content_kind ORDER BY messages DESC, content_kind"
                )
            )
        ).mappings().all()
        unsupported_messages = (
            await connection.execute(
                text(
                    "SELECT am.peer_id, am.message_id, mv.content_kind, "
                    "JSON_UNQUOTE(JSON_EXTRACT(mv.content_json, '$.media_type')) AS media_type, "
                    "JSON_UNQUOTE(JSON_EXTRACT(mv.content_json, '$.raw._')) AS telegram_type "
                    "FROM archived_messages am JOIN message_versions mv "
                    "ON mv.archived_message_id = am.id AND mv.version = am.current_version "
                    "WHERE mv.content_kind = 'unsupported' ORDER BY am.message_id"
                )
            )
        ).mappings().all()
    await engine.dispose()
    print("states", [dict(row) for row in states])
    print("runs", [dict(row) for row in runs])
    print("history_runs", [dict(row) for row in history_runs])
    print("backup_events", [dict(row) for row in backup_events])
    print("message_summary", dict(message_summary))
    print("content_kinds", [dict(row) for row in content_kinds])
    print("unsupported_messages", [dict(row) for row in unsupported_messages])


if __name__ == "__main__":
    asyncio.run(main())
