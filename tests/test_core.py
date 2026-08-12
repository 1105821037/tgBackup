import asyncio
import gc
import hashlib
from datetime import datetime, time, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.app.config import get_settings
from backend.app.chat_identity import SAVED_MESSAGES_TITLE, chat_display_title
from backend.app.backup_scheduler import due_history_schedule_key, due_schedule_key
from backend.app.backup_service import (
    await_with_hard_timeout,
    classify_exception,
    content_hash,
    detect_media_type,
    download_media_with_stall_timeout,
    message_metadata,
    forward_metadata,
)
from backend.app.models import ChatBackupRule
from backend.app.message_content import serialize_message_content
from backend.app.media_downloader import _prepare_parts, parallel_download_file
from backend.app.realtime import RealtimeHub
from backend.app.entity_service import basic_profile, raw_telegram_id, stable_hash
from backend.app.entity_service import discover_message_via_bot
from backend.app.routes_entities import avatar_url
from backend.app.routes_overview import empty_overview
from backend.app.routes_archive import (
    custom_emoji_locks,
    custom_emoji_extension,
    forward_origin_peer_id,
    message_entities_payload,
    serialized_peer_id,
    SHARED_MEDIA_TYPES,
    shared_media_asset_types,
    shared_media_link,
)
from backend.app.schemas import AdminUserUpdate, ChatBackupRuleInput, Credentials, PasswordChange, TelegramPhoneRequest
from backend.app.security import SlidingWindowRateLimiter, hash_password, verify_password
from backend.app.telegram_auth import mask_phone
from backend.app.telegram_runtime import media_connection_limit
from telethon import types


def test_custom_emoji_file_extensions_are_stable() -> None:
    assert custom_emoji_extension("application/x-tgsticker") == ".tgs"
    assert custom_emoji_extension("video/webm") == ".webm"
    assert custom_emoji_extension("image/webp") == ".webp"
    assert custom_emoji_extension("application/octet-stream") == ".bin"


def test_shared_media_categories_match_archive_media_types() -> None:
    assert SHARED_MEDIA_TYPES["media"] == ("photo", "video")
    assert SHARED_MEDIA_TYPES["documents"] == ("document",)
    assert SHARED_MEDIA_TYPES["audio"] == ("audio",)
    assert SHARED_MEDIA_TYPES["voice"] == ("voice",)
    assert SHARED_MEDIA_TYPES["gif"] == ("animation",)
    version = SimpleNamespace(metadata_json={}, text="说明 https://example.com/file。")
    assert shared_media_link(version) == "https://example.com/file"


def test_custom_emoji_lock_cache_releases_unused_entries() -> None:
    key = (999, 123456789)
    lock = custom_emoji_locks.setdefault(key, asyncio.Lock())
    assert custom_emoji_locks.get(key) is lock
    del lock
    gc.collect()
    assert key not in custom_emoji_locks


def test_shared_media_photo_and_video_filters_are_server_side() -> None:
    assert shared_media_asset_types("media", "all") == ("photo", "video")
    assert shared_media_asset_types("media", "photo") == ("photo",)
    assert shared_media_asset_types("media", "video") == ("video",)
    assert shared_media_asset_types("documents", "video") == ("document",)
    with pytest.raises(ValueError):
        shared_media_asset_types("media", "unknown")


def test_custom_emoji_document_ids_are_serialized_without_precision_loss() -> None:
    document_id = 5366316836101038579
    entities = message_entities_payload(
        [{"_": "MessageEntityCustomEmoji", "offset": 0, "length": 2, "document_id": document_id}]
    )
    assert entities[0]["document_id"] == str(document_id)


def test_password_is_hashed_and_verifiable() -> None:
    encoded = hash_password("correct horse battery staple")
    assert "correct horse" not in encoded
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong password", encoded)


def test_login_rate_limiter_blocks_and_recovers_after_window() -> None:
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=10)
    assert limiter.consume("client", now=100) is None
    assert limiter.consume("client", now=101) is None
    assert limiter.consume("client", now=102) == 8
    assert limiter.consume("client", now=111) is None
    limiter.clear("client")
    assert limiter.consume("client", now=112) is None


def test_credentials_normalize_username() -> None:
    credentials = Credentials(username="  Owner.Name  ", password="long-password")
    assert credentials.username == "owner.name"


def test_short_password_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Credentials(username="owner", password="short")


def test_account_management_payloads_validate_passwords_and_changes() -> None:
    change = PasswordChange(current_password="old", new_password="long-new-password")
    assert change.new_password == "long-new-password"
    update = AdminUserUpdate(username="  Staff.Name  ")
    assert update.username == "staff.name"
    with pytest.raises(ValidationError):
        AdminUserUpdate()


def test_empty_overview_has_stable_zero_state() -> None:
    payload = empty_overview()
    assert payload["account_bound"] is False
    assert payload["message_count"] == 0
    assert payload["activities"] == []


def test_phone_requires_international_format() -> None:
    with pytest.raises(ValidationError):
        TelegramPhoneRequest(phone="13800000000")
    assert TelegramPhoneRequest(phone="+8613800000000").phone.startswith("+86")


def test_phone_mask_and_local_origins() -> None:
    assert mask_phone("+8613800000000").endswith("0000")
    assert "13800000000" not in mask_phone("+8613800000000")
    assert "http://localhost:5173" in get_settings().allowed_frontend_origins
    assert "http://127.0.0.1:5173" in get_settings().allowed_frontend_origins


def test_media_connection_limit_uses_premium_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_media_parallel_connections_regular", 3)
    monkeypatch.setattr(settings, "telegram_media_parallel_connections_premium", 6)
    assert media_connection_limit(False) == 3
    assert media_connection_limit(True) == 6


def test_media_connection_limit_has_safe_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_media_parallel_connections_regular", 0)
    monkeypatch.setattr(settings, "telegram_media_parallel_connections_premium", 20)
    assert media_connection_limit(False) == 1
    assert media_connection_limit(True) == 6


def test_weekly_rule_requires_and_normalizes_weekdays() -> None:
    with pytest.raises(ValidationError):
        ChatBackupRuleInput(
            schedule_kind="weekly",
            backup_time="09:00",
            weekdays=[],
        )
    rule = ChatBackupRuleInput(
        schedule_kind="weekly",
        backup_time="09:00",
        weekdays=[5, 1, 3, 3],
        media_types=["photo", "photo", "document"],
    )
    assert rule.weekdays == [1, 3, 5]
    assert rule.media_types == ["photo", "document"]


def test_cron_rules_require_standard_five_fields() -> None:
    with pytest.raises(ValidationError):
        ChatBackupRuleInput(
            schedule_kind="cron",
            cron_expression="0 9 * * * 30",
        )
    rule = ChatBackupRuleInput(
        schedule_kind="cron",
        cron_expression="  0   9  * * 1-5 ",
        history_schedule_kind="cron",
        history_cron_expression="0 */6 * * *",
    )
    assert rule.cron_expression == "0 9 * * 1-5"
    assert rule.history_cron_expression == "0 */6 * * *"


def test_schedule_uses_server_local_day_and_time() -> None:
    rule = ChatBackupRule(
        enabled=True,
        schedule_kind="weekly",
        backup_time=time(9, 0),
        weekdays=[1, 3, 5],
    )
    monday = datetime(2026, 8, 10, 9, 1, tzinfo=timezone.utc)
    assert due_schedule_key(rule, monday) == "2026-08-10@09:00"
    assert due_schedule_key(rule, monday.replace(hour=8, minute=59)) is None
    rule.removed_at = monday
    assert due_schedule_key(rule, monday) is None


def test_cron_schedule_claims_only_the_matching_minute() -> None:
    rule = ChatBackupRule(
        enabled=True,
        removed_at=None,
        schedule_kind="cron",
        backup_time=time(9, 0),
        weekdays=[],
        cron_expression="15 9 * * 1-5",
        history_enabled=True,
        history_schedule_kind="cron",
        history_time=time(3, 0),
        history_weekdays=[],
        history_cron_expression="30 3 * * *",
    )
    monday = datetime(2026, 8, 10, 9, 15, tzinfo=timezone.utc)
    assert due_schedule_key(rule, monday) == "2026-08-10@09:15"
    assert due_schedule_key(rule, monday.replace(minute=16)) is None
    history_time = monday.replace(hour=3, minute=30)
    assert due_history_schedule_key(rule, history_time) == "2026-08-10@03:30"


def test_pipeline_metadata_hash_and_failure_classification() -> None:
    message = SimpleNamespace(
        photo=SimpleNamespace(id=7),
        document=None,
        message="hello",
        views=1,
    )
    assert detect_media_type(message) == "photo"
    stable = message_metadata(message, "photo")
    first = content_hash(message, stable)
    message.views = 2
    second = content_hash(message, message_metadata(message, "photo"))
    assert first == second
    message.message = "edited"
    assert first != content_hash(message, message_metadata(message, "photo"))
    failure = classify_exception(OSError("offline"))
    assert failure.code == "network_error"
    assert failure.action == "retry"


def test_forward_metadata_keeps_saved_messages_origin() -> None:
    header = types.MessageFwdHeader(
        date=datetime(2026, 8, 12, tzinfo=timezone.utc),
        from_id=types.PeerChannel(channel_id=42),
        saved_from_peer=types.PeerChannel(channel_id=42),
        saved_from_id=types.PeerUser(user_id=7),
        saved_from_name="Original sender",
    )
    metadata = forward_metadata(SimpleNamespace(fwd_from=header, forward=None))
    assert metadata is not None
    assert metadata["saved_from_name"] == "Original sender"
    assert forward_origin_peer_id(metadata) == -1_000_000_000_042
    assert serialized_peer_id({"_": "PeerUser", "user_id": 7}) == 7
    message = SimpleNamespace(message="saved", edit_date=None)
    old_forward = dict(metadata)
    old_forward.pop("saved_from_peer")
    old_forward.pop("saved_from_id")
    old_forward.pop("saved_from_name")
    old_metadata = {"forward": old_forward}
    assert content_hash(message, old_metadata) == content_hash(message, {"forward": metadata})


@pytest.mark.asyncio
async def test_via_bot_falls_back_to_a_cached_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = SimpleNamespace(peer_id=7411560693)

    async def fake_placeholder(db, account_id, peer_id, *, source, priority):
        assert account_id == 1
        assert peer_id == 7411560693
        assert source == "message_via_bot"
        assert priority == 90
        return expected, 7

    monkeypatch.setattr(
        "backend.app.entity_service.discover_placeholder", fake_placeholder
    )
    result = await discover_message_via_bot(
        object(),
        1,
        SimpleNamespace(via_bot_id=7411560693, via_bot=None),
        source="message_via_bot",
        priority=90,
    )
    assert result is expected


def test_structured_telegram_message_content_is_normalized() -> None:
    contact_kind, contact = serialize_message_content(
        SimpleNamespace(
            action=None,
            message="",
            media=types.MessageMediaContact(
                phone_number="+8613800000000",
                first_name="Test",
                last_name="Contact",
                vcard="",
                user_id=42,
            ),
        ),
        None,
    )
    assert contact_kind == "contact"
    assert contact["name"] == "Test Contact"
    assert contact["phone_number"] == "+8613800000000"

    dice_kind, dice = serialize_message_content(
        SimpleNamespace(
            action=None,
            message="",
            media=types.MessageMediaDice(value=6, emoticon="🎲"),
        ),
        None,
    )
    assert dice_kind == "dice"
    assert dice["value"] == 6


def test_round_video_and_service_messages_have_dedicated_kinds() -> None:
    round_kind, round_content = serialize_message_content(
        SimpleNamespace(
            action=None,
            message="",
            media=SimpleNamespace(spoiler=False, ttl_seconds=None),
            document=SimpleNamespace(
                mime_type="video/mp4",
                size=1024,
                attributes=[
                    types.DocumentAttributeVideo(
                        duration=8,
                        w=320,
                        h=320,
                        round_message=True,
                        supports_streaming=True,
                    )
                ],
            ),
            file=SimpleNamespace(name="round.mp4", duration=8, width=320, height=320),
        ),
        "video",
    )
    assert round_kind == "round_video"
    assert round_content["round_message"] is True
    assert round_content["duration"] == 8

    service_kind, service = serialize_message_content(
        SimpleNamespace(
            action=types.MessageActionChatEditTitle(title="新的群名"),
            media=None,
            message="",
        ),
        None,
    )
    assert service_kind == "service"
    assert service["summary"] == "修改了群组名称：新的群名"


def test_poll_votes_do_not_create_content_versions() -> None:
    poll_media_type = type("MessageMediaPoll", (), {})
    poll = SimpleNamespace(
        id=7,
        question="午饭吃什么？",
        answers=[SimpleNamespace(text="面", option=b"a")],
        closed=False,
        public_voters=False,
        multiple_choice=False,
        quiz=False,
    )
    result = SimpleNamespace(option=b"a", voters=1, chosen=False, correct=False)
    media = poll_media_type()
    media.poll = poll
    media.results = SimpleNamespace(results=[result], total_voters=1, solution=None)
    message = SimpleNamespace(action=None, media=media, message="", edit_date=None)

    kind, content = serialize_message_content(message, None)
    metadata = {"content_kind": kind, "content": content}
    first_hash = content_hash(message, metadata)

    result.voters = 99
    result.chosen = True
    media.results.total_voters = 99
    _, updated_content = serialize_message_content(message, None)
    assert content_hash(message, {"content_kind": kind, "content": updated_content}) == first_hash

    poll.question = "晚饭吃什么？"
    _, changed_content = serialize_message_content(message, None)
    assert content_hash(message, {"content_kind": kind, "content": changed_content}) != first_hash


def test_todo_completion_is_state_not_a_content_version() -> None:
    media = types.MessageMediaToDo(
        todo=types.TodoList(
            title=types.TextWithEntities(text="发布前检查", entities=[]),
            list=[
                types.TodoItem(
                    id=1,
                    title=types.TextWithEntities(text="运行测试", entities=[]),
                )
            ],
        ),
        completions=[],
    )
    message = SimpleNamespace(action=None, media=media, message="", edit_date=None)
    kind, content = serialize_message_content(message, None)
    assert kind == "todo"
    assert content["items"][0]["title"] == "运行测试"
    first_hash = content_hash(message, {"content_kind": kind, "content": content})

    media.completions = [
        types.TodoCompletion(
            id=1,
            completed_by=types.PeerUser(user_id=42),
            date=datetime(2026, 8, 12, tzinfo=timezone.utc),
        )
    ]
    _, completed_content = serialize_message_content(message, None)
    assert completed_content["items"][0]["completed"] is True
    assert content_hash(
        message, {"content_kind": kind, "content": completed_content}
    ) == first_hash


def test_special_message_content_is_normalized_for_archive_rendering() -> None:
    invoice_kind, invoice = serialize_message_content(
        SimpleNamespace(
            action=None,
            media=types.MessageMediaInvoice(
                title="测试商品",
                description="付款说明",
                currency="CNY",
                total_amount=1299,
                start_param="order-1",
                test=True,
                shipping_address_requested=True,
            ),
            message="",
        ),
        None,
    )
    assert invoice_kind == "invoice"
    assert invoice["total_amount"] == 1299
    assert invoice["shipping_address_requested"] is True

    giveaway_kind, giveaway = serialize_message_content(
        SimpleNamespace(
            action=None,
            media=types.MessageMediaGiveaway(
                channels=[1001, 1002],
                quantity=3,
                until_date=datetime(2026, 8, 20, tzinfo=timezone.utc),
                only_new_subscribers=True,
                countries_iso2=["CN", "SG"],
                months=6,
            ),
            message="",
        ),
        None,
    )
    assert giveaway_kind == "giveaway"
    assert giveaway["channel_ids"] == [1001, 1002]
    assert giveaway["quantity"] == 3
    assert giveaway["countries"] == ["CN", "SG"]

    paid_kind, paid = serialize_message_content(
        SimpleNamespace(
            action=None,
            media=types.MessageMediaPaidMedia(
                stars_amount=50,
                extended_media=[types.MessageExtendedMediaPreview(w=640, h=480)],
            ),
            message="",
        ),
        None,
    )
    assert paid_kind == "paid_media"
    assert paid["stars_amount"] == 50
    assert paid["item_count"] == 1
    assert paid["purchased"] is False

    story_kind, story = serialize_message_content(
        SimpleNamespace(
            action=None,
            media=types.MessageMediaStory(
                peer=types.PeerUser(user_id=42),
                id=7,
                story=types.StoryItemDeleted(id=7),
            ),
            message="",
        ),
        None,
    )
    assert story_kind == "story"
    assert story["peer_id"] == 42
    assert story["state"] == "expired"


def test_giveaway_results_keep_winner_and_claim_counts() -> None:
    kind, content = serialize_message_content(
        SimpleNamespace(
            action=None,
            media=types.MessageMediaGiveawayResults(
                channel_id=1001,
                launch_msg_id=12,
                winners_count=2,
                unclaimed_count=1,
                winners=[42, 43],
                until_date=datetime(2026, 8, 20, tzinfo=timezone.utc),
                prize_description="纪念奖品",
            ),
            message="",
        ),
        None,
    )
    assert kind == "giveaway_results"
    assert content["winner_ids"] == [42, 43]
    assert content["winners_count"] == 2
    assert content["unclaimed_count"] == 1


def test_telegram_entity_identity_and_stable_hash() -> None:
    assert raw_telegram_id(-1001819277894) == 1819277894
    user = types.User(id=42, first_name="Test", last_name="Sender")
    profile = basic_profile(user, 42)
    assert profile["entity_kind"] == "user"
    assert profile["display_name"] == "Test Sender"
    channel = types.Channel(
        id=99,
        title="Profiles channel",
        photo=types.ChatPhotoEmpty(),
        date=datetime(2026, 8, 12, tzinfo=timezone.utc),
        broadcast=True,
        signature_profiles=True,
    )
    assert basic_profile(channel, -1_000_000_000_099)["signature_profiles"] is True
    assert basic_profile(
        types.Channel(
            id=100,
            title="Ordinary channel",
            photo=types.ChatPhotoEmpty(),
            date=datetime(2026, 8, 12, tzinfo=timezone.utc),
            broadcast=True,
        ),
        -1_000_000_000_100,
    )["signature_profiles"] is False
    assert stable_hash({"name": "Test", "id": 42}) == stable_hash(
        {"id": 42, "name": "Test"}
    )
    assert avatar_url(8, 99, "small") == "/api/entities/8/avatar/99/small"


def test_self_dialog_uses_saved_messages_title() -> None:
    assert chat_display_title(42, 42, "Smith") == SAVED_MESSAGES_TITLE
    assert chat_display_title(42, 99, "Morty") == "Morty"


def test_history_update_scope_and_limits_are_validated() -> None:
    with pytest.raises(ValidationError):
        ChatBackupRuleInput(
            history_start_kind="days_ago",
            history_start_days_ago=2,
            history_end_kind="days_ago",
            history_end_days_ago=7,
        )
    rule = ChatBackupRuleInput(
        history_enabled=True,
        history_start_kind="days_ago",
        history_start_days_ago=7,
        history_end_kind="days_ago",
        history_end_days_ago=2,
        history_max_updates=5,
    )
    assert rule.history_max_updates == 5
    assert rule.history_start_days_ago == 7
    assert rule.history_end_days_ago == 2


@pytest.mark.asyncio
async def test_realtime_hub_is_user_scoped_and_drops_stale_state() -> None:
    hub = RealtimeHub(queue_size=1)
    first_user = await hub.subscribe(1)
    second_user = await hub.subscribe(2)
    await hub.publish(1, "telegram.runtime.changed", {"connection": "connecting"})
    await hub.publish(1, "telegram.runtime.changed", {"connection": "connected"})

    latest = await first_user.queue.get()
    assert latest["version"] == 1
    assert latest["type"] == "telegram.runtime.changed"
    assert latest["payload"] == {"connection": "connected"}
    assert second_user.queue.empty()
    assert await hub.connection_count() == 2

    await hub.unsubscribe(first_user)
    await hub.unsubscribe(second_user)
    assert await hub.connection_count() == 0


@pytest.mark.asyncio
async def test_hard_timeout_does_not_wait_for_slow_cancellation() -> None:
    release = asyncio.Event()

    async def cancellation_resistant() -> None:
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()

    started = asyncio.get_running_loop().time()
    with pytest.raises(TimeoutError):
        await await_with_hard_timeout(cancellation_resistant(), 0.02)
    assert asyncio.get_running_loop().time() - started < 0.2
    release.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_media_timeout_tracks_inactivity_not_total_duration(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "backup_media_timeout_seconds", 0.03)
    heartbeats = 0

    class ProgressingClient:
        async def _chunks(self):
            for _ in range(4):
                await asyncio.sleep(0.015)
                yield b"x"

        def iter_download(self, message, *, offset, file_size):
            assert offset == 0
            assert file_size == 4
            return self._chunks()

    def heartbeat() -> None:
        nonlocal heartbeats
        heartbeats += 1

    result = await download_media_with_stall_timeout(
        ProgressingClient(),
        SimpleNamespace(file=SimpleNamespace(size=4), media=object()),
        tmp_path / "moving.part",
        on_activity=heartbeat,
    )
    assert result == str(tmp_path / "moving.part")
    assert heartbeats == 4


@pytest.mark.asyncio
async def test_media_timeout_still_rejects_a_stalled_transfer(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "backup_media_timeout_seconds", 0.02)

    class StalledClient:
        async def _chunks(self):
            await asyncio.sleep(1)
            yield b"x"

        def iter_download(self, message, *, offset, file_size):
            return self._chunks()

    with pytest.raises(TimeoutError, match="没有进度"):
        await download_media_with_stall_timeout(
            StalledClient(),
            SimpleNamespace(file=SimpleNamespace(size=1), media=object()),
            tmp_path / "stalled.part",
        )


@pytest.mark.asyncio
async def test_media_download_resumes_an_existing_partial_file(tmp_path) -> None:
    partial = tmp_path / "resume.part"
    partial.write_bytes(b"abc")

    class ResumingClient:
        async def _chunks(self):
            yield b"def"

        def iter_download(self, message, *, offset, file_size):
            assert offset == 3
            assert file_size == 6
            return self._chunks()

    result = await download_media_with_stall_timeout(
        ResumingClient(),
        SimpleNamespace(file=SimpleNamespace(size=6), media=object()),
        partial,
    )
    assert result == str(partial)
    assert partial.read_bytes() == b"abcdef"


@pytest.mark.asyncio
async def test_parallel_media_download_uses_independent_ranges(tmp_path) -> None:
    payload = bytes(range(251)) * 300
    request_size = 4096
    calls: list[tuple[int, int]] = []

    class RangeClient:
        def iter_download(
            self, source, *, offset, limit, chunk_size, request_size, file_size
        ):
            calls.append((offset, limit))

            async def chunks():
                cursor = offset
                for _ in range(limit):
                    if cursor >= file_size:
                        break
                    data = payload[cursor : min(cursor + request_size, file_size)]
                    cursor += len(data)
                    yield data

            return chunks()

    temporary = tmp_path / "parallel.part"
    result = await parallel_download_file(
        [RangeClient(), RangeClient(), RangeClient()],
        object(),
        len(payload),
        temporary,
        stall_timeout=1,
        request_size=request_size,
    )

    assert temporary.read_bytes() == payload
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.connections == 3
    assert len(calls) == 3
    assert sorted(offset for offset, _ in calls)[0] == 0
    assert not list(tmp_path.glob("*.chunk-*"))
    assert not list(tmp_path.glob("*.parallel.json"))


@pytest.mark.asyncio
async def test_parallel_media_download_resumes_each_shard(tmp_path) -> None:
    payload = bytes(range(127)) * 200
    request_size = 4096
    temporary = tmp_path / "resume-parallel.part"
    parts = _prepare_parts(temporary, len(payload), 2, request_size)
    first_completed = 700
    parts[0].path.write_bytes(payload[:first_completed])
    offsets: list[int] = []

    class RangeClient:
        def iter_download(
            self, source, *, offset, limit, chunk_size, request_size, file_size
        ):
            offsets.append(offset)

            async def chunks():
                cursor = offset
                for _ in range(limit):
                    if cursor >= file_size:
                        break
                    data = payload[cursor : min(cursor + request_size, file_size)]
                    cursor += len(data)
                    yield data

            return chunks()

    await parallel_download_file(
        [RangeClient(), RangeClient()],
        object(),
        len(payload),
        temporary,
        stall_timeout=1,
        request_size=request_size,
    )

    assert temporary.read_bytes() == payload
    assert parts[0].start + first_completed in offsets
