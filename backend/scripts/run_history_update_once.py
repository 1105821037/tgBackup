from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.backup_scheduler import coordinator  # noqa: E402
from backend.app.db import SessionLocal, engine  # noqa: E402
from backend.app.history_update_service import history_update_rule  # noqa: E402
from backend.app.models import ChatBackupRule  # noqa: E402
from backend.app.telegram_runtime import runtime_manager  # noqa: E402


async def main(peer_id: int | None) -> None:
    await coordinator.recover_interrupted()
    await runtime_manager.start()
    async with SessionLocal() as db:
        query = select(ChatBackupRule).where(ChatBackupRule.removed_at.is_(None))
        if peer_id is not None:
            query = query.where(ChatBackupRule.peer_id == peer_id)
        rule = await db.scalar(query.order_by(ChatBackupRule.id).limit(1))
        if rule is None:
            raise RuntimeError("没有可执行的会话备份规则")
        print(f"开始历史消息更新 rule={rule.id} peer={rule.peer_id}")
        rule_id = rule.id
    try:
        run_id = await history_update_rule(rule_id, trigger="cli")
    finally:
        await runtime_manager.stop()
        await engine.dispose()
    print(f"历史消息更新完成 run={run_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--peer-id", type=int)
    arguments = parser.parse_args()
    asyncio.run(main(arguments.peer_id))
