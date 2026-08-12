"""Minimal Telegram login and connectivity probe.

This tool deliberately keeps API credentials and the authenticated Telethon
session outside source control. Run ``python telegram_probe.py login`` in an
interactive terminal so verification codes and 2FA passwords never enter logs.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from telethon import TelegramClient


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"缺少配置 {name}，请先编辑 .env")
    return value


def load_config() -> tuple[int, str, Path, dict[str, object] | None]:
    load_dotenv()

    try:
        api_id = int(required_env("TELEGRAM_API_ID"))
    except ValueError as exc:
        raise SystemExit("TELEGRAM_API_ID 必须是整数") from exc

    api_hash = required_env("TELEGRAM_API_HASH")
    session = Path(
        os.getenv("TELEGRAM_SESSION", "data/sessions/probe_account").strip()
    ).expanduser()
    session.parent.mkdir(parents=True, exist_ok=True)

    proxy_type = os.getenv("TELEGRAM_PROXY_TYPE", "").strip().lower()
    if not proxy_type:
        return api_id, api_hash, session, None
    if proxy_type not in {"socks5", "socks4", "http"}:
        raise SystemExit("TELEGRAM_PROXY_TYPE 只支持 socks5、socks4、http")

    host = required_env("TELEGRAM_PROXY_HOST")
    try:
        port = int(required_env("TELEGRAM_PROXY_PORT"))
    except ValueError as exc:
        raise SystemExit("TELEGRAM_PROXY_PORT 必须是整数") from exc

    proxy: dict[str, object] = {
        "proxy_type": proxy_type,
        "addr": host,
        "port": port,
        "rdns": True,
    }
    username = os.getenv("TELEGRAM_PROXY_USERNAME", "").strip()
    password = os.getenv("TELEGRAM_PROXY_PASSWORD", "").strip()
    if username:
        proxy["username"] = username
    if password:
        proxy["password"] = password
    return api_id, api_hash, session, proxy


async def login() -> None:
    api_id, api_hash, session, proxy = load_config()
    client = TelegramClient(
        str(session),
        api_id,
        api_hash,
        proxy=proxy,
        auto_reconnect=True,
        connection_retries=5,
        request_retries=5,
        flood_sleep_threshold=60,
    )

    # start() performs the interactive phone/code/2FA flow when needed. It
    # reuses the local session without asking again after a successful login.
    await client.start()
    try:
        me = await client.get_me()
        display_name = " ".join(
            part for part in (me.first_name, me.last_name) if part
        )
        print("\n登录成功")
        print(f"用户 ID: {me.id}")
        print(f"显示名: {display_name or '(未设置)'}")
        print(f"用户名: @{me.username}" if me.username else "用户名: (未设置)")
        print(f"Session: {session.with_suffix('.session').resolve()}")
    finally:
        await client.disconnect()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_fingerprint(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def reaction_summary(message: Any) -> list[dict[str, Any]]:
    reactions = getattr(message, "reactions", None)
    results = getattr(reactions, "results", None) or []
    summary: list[dict[str, Any]] = []
    for item in results:
        reaction = getattr(item, "reaction", None)
        summary.append(
            {
                "reaction_type": type(reaction).__name__ if reaction else None,
                "emoticon": getattr(reaction, "emoticon", None),
                "document_id": getattr(reaction, "document_id", None),
                "count": getattr(item, "count", None),
                "chosen_order": getattr(item, "chosen_order", None),
            }
        )
    return summary


def message_summary(message: Any) -> dict[str, Any]:
    media = getattr(message, "media", None)
    file = getattr(message, "file", None)
    return {
        "id": message.id,
        "date": message.date.isoformat() if message.date else None,
        "edit_date": message.edit_date.isoformat() if message.edit_date else None,
        "outgoing": bool(message.out),
        "sender_id": message.sender_id,
        "text_length": len(message.raw_text or ""),
        "text_sha256": text_fingerprint(message.raw_text),
        "media_type": type(media).__name__ if media else None,
        "file_size": getattr(file, "size", None),
        "file_ext": getattr(file, "ext", None),
        "views": message.views,
        "forwards": message.forwards,
        "replies_count": getattr(getattr(message, "replies", None), "replies", None),
        "reactions": reaction_summary(message),
        "pinned": bool(message.pinned),
        "grouped_id": message.grouped_id,
        "reply_to_message_id": message.reply_to_msg_id,
    }


async def probe_chat(
    chat_ref: str,
    limit: int,
    download_max_bytes: int,
    verify_ids: list[int] | None = None,
) -> None:
    api_id, api_hash, session, proxy = load_config()
    client = TelegramClient(
        str(session),
        api_id,
        api_hash,
        proxy=proxy,
        auto_reconnect=True,
        connection_retries=5,
        request_retries=5,
        flood_sleep_threshold=60,
    )
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise SystemExit("Session 尚未登录，请先运行 login 命令")

        entity = await client.get_entity(chat_ref)
        peer_id = await client.get_peer_id(entity)
        newest_first = [
            message
            async for message in client.iter_messages(entity, limit=limit)
        ]
        if not newest_first:
            raise SystemExit("目标聊天当前没有可读取的消息")

        ids_desc = [message.id for message in newest_first]
        newest_id = ids_desc[0]
        oldest_sample_id = ids_desc[-1]

        # Freeze the upper bound so messages arriving during this probe do not
        # change the result set. max_id and min_id are both exclusive.
        ascending_window = [
            message
            async for message in client.iter_messages(
                entity,
                min_id=max(0, oldest_sample_id - 1),
                max_id=newest_id + 1,
                reverse=True,
                limit=None,
            )
        ]
        ids_asc = [message.id for message in ascending_window]

        page_size = min(5, limit)
        page_one = await client.get_messages(entity, limit=page_size)
        page_two = await client.get_messages(
            entity,
            limit=page_size,
            offset_id=page_one[-1].id,
        )
        page_one_ids = [message.id for message in page_one]
        page_two_ids = [message.id for message in page_two]

        checkpoint_id = ids_asc[len(ids_asc) // 2]
        incremental = [
            message
            async for message in client.iter_messages(
                entity,
                min_id=checkpoint_id,
                max_id=newest_id + 1,
                reverse=True,
                limit=None,
            )
        ]
        incremental_ids = [message.id for message in incremental]

        lookup_ids = list(
            dict.fromkeys(
                [oldest_sample_id, checkpoint_id, newest_id, newest_id + 1]
                + (verify_ids or [])
            )
        )
        lookup = await client.get_messages(entity, ids=lookup_ids)
        lookup_result = [
            {
                "requested_id": requested_id,
                "returned_id": message.id if message else None,
                "present": message is not None,
            }
            for requested_id, message in zip(lookup_ids, lookup)
        ]

        probe_dir = Path("data/probes") / str(peer_id)
        probe_dir.mkdir(parents=True, exist_ok=True)
        downloaded: dict[str, Any] | None = None
        for message in newest_first:
            file = getattr(message, "file", None)
            size = getattr(file, "size", None)
            if not file or size is None or size > download_max_bytes:
                continue
            extension = getattr(file, "ext", None) or ".bin"
            if not extension.startswith(".") or len(extension) > 12:
                extension = ".bin"
            target = probe_dir / f"message_{message.id}{extension}"
            downloaded_path = await client.download_media(message, file=str(target))
            if downloaded_path:
                actual = Path(downloaded_path)
                downloaded = {
                    "message_id": message.id,
                    "path": str(actual.resolve()),
                    "size": actual.stat().st_size,
                    "sha256": sha256_file(actual),
                }
            break

        expected_incremental = [value for value in ids_asc if value > checkpoint_id]
        generated_at = datetime.now(timezone.utc)
        report = {
            "generated_at": generated_at.isoformat(),
            "library": "Telethon",
            "chat": {
                "requested": chat_ref,
                "peer_id": peer_id,
                "entity_type": type(entity).__name__,
                "title_or_username": getattr(entity, "title", None)
                or getattr(entity, "username", None),
            },
            "sample": {
                "count": len(newest_first),
                "newest_id": newest_id,
                "oldest_sample_id": oldest_sample_id,
                "ids_newest_first": ids_desc,
                "ids_oldest_first": ids_asc,
                "descending_is_strict": all(a > b for a, b in zip(ids_desc, ids_desc[1:])),
                "ascending_is_strict": all(a < b for a, b in zip(ids_asc, ids_asc[1:])),
            },
            "pagination": {
                "page_one": page_one_ids,
                "page_two": page_two_ids,
                "overlap": sorted(set(page_one_ids) & set(page_two_ids)),
                "ordered": all(a > b for a, b in zip(page_one_ids + page_two_ids, (page_one_ids + page_two_ids)[1:])),
            },
            "incremental": {
                "checkpoint_id": checkpoint_id,
                "actual_ids": incremental_ids,
                "expected_from_frozen_sample": expected_incremental,
                "matches_frozen_sample": incremental_ids == expected_incremental,
            },
            "id_lookup": lookup_result,
            "field_observations": [message_summary(message) for message in reversed(newest_first)],
            "downloaded_media": downloaded,
        }

        report_path = probe_dir / (
            "probe_report_" + generated_at.strftime("%Y%m%dT%H%M%SZ") + ".json"
        )
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"\n报告已写入: {report_path.resolve()}")
    finally:
        await client.disconnect()


async def probe_channel_capabilities(dialog_limit: int) -> None:
    """Find one accessible channel and inspect fields unavailable in private chats."""
    api_id, api_hash, session, proxy = load_config()
    client = TelegramClient(str(session), api_id, api_hash, proxy=proxy)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise SystemExit("Session 尚未登录，请先运行 login 命令")

        candidates: list[dict[str, Any]] = []
        async for dialog in client.iter_dialogs(limit=dialog_limit):
            entity = dialog.entity
            if not (
                getattr(entity, "broadcast", False)
                or getattr(entity, "megagroup", False)
            ):
                continue
            sample = [
                message
                async for message in client.iter_messages(entity, limit=10)
            ]
            if not sample:
                continue
            candidates.append(
                {
                    "peer_id": await client.get_peer_id(entity),
                    "entity_type": type(entity).__name__,
                    "broadcast": bool(getattr(entity, "broadcast", False)),
                    "megagroup": bool(getattr(entity, "megagroup", False)),
                    "sample_count": len(sample),
                    "strict_descending_ids": all(
                        a.id > b.id for a, b in zip(sample, sample[1:])
                    ),
                    "has_views": any(message.views is not None for message in sample),
                    "has_forwards": any(message.forwards is not None for message in sample),
                    "has_replies": any(message.replies is not None for message in sample),
                    "has_reactions": any(message.reactions is not None for message in sample),
                    "examples": [
                        {
                            "id": message.id,
                            "date": message.date.isoformat() if message.date else None,
                            "views": message.views,
                            "forwards": message.forwards,
                            "replies_count": getattr(message.replies, "replies", None),
                            "reactions": reaction_summary(message),
                        }
                        for message in sample[:3]
                    ],
                }
            )
            if candidates[-1]["has_views"] and candidates[-1]["has_forwards"]:
                break

        selected = next(
            (
                item
                for item in candidates
                if item["has_views"] or item["has_forwards"]
            ),
            candidates[0] if candidates else None,
        )
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dialogs_examined_at_most": dialog_limit,
            "channel_samples": [selected] if selected else [],
        }
        output = Path("data/probes/channel_capabilities.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"\n报告已写入: {output.resolve()}")
    finally:
        await client.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram 备份模型验证工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("login", help="交互式登录 Telegram")
    probe = subparsers.add_parser("probe", help="只读探测一个聊天")
    probe.add_argument("--chat", required=True, help="用户名或 peer ID")
    probe.add_argument("--limit", type=int, default=30, help="探测的最近消息数量")
    probe.add_argument(
        "--download-max-mb",
        type=int,
        default=20,
        help="最多下载一个不超过此大小的媒体，0 表示不下载",
    )
    probe.add_argument(
        "--verify-ids",
        type=int,
        nargs="*",
        default=[],
        help="额外按 ID 精确查询这些消息",
    )
    capabilities = subparsers.add_parser(
        "capabilities", help="匿名化抽样频道消息的易变元数据字段"
    )
    capabilities.add_argument("--dialog-limit", type=int, default=100)
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    if args.command == "login":
        asyncio.run(login())
    elif args.command == "probe":
        if args.limit < 2 or args.limit > 200:
            raise SystemExit("--limit 必须在 2 到 200 之间")
        asyncio.run(
            probe_chat(
                args.chat,
                args.limit,
                max(0, args.download_max_mb) * 1024 * 1024,
                args.verify_ids,
            )
        )
    elif args.command == "capabilities":
        if args.dialog_limit < 1 or args.dialog_limit > 500:
            raise SystemExit("--dialog-limit 必须在 1 到 500 之间")
        asyncio.run(probe_channel_capabilities(args.dialog_limit))


if __name__ == "__main__":
    main()
