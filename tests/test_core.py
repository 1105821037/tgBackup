import asyncio
import gc
import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, time, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.app.config import get_settings
from backend.app.chat_identity import SAVED_MESSAGES_TITLE, chat_display_title
import backend.app.backup_scheduler as backup_scheduler
from backend.app.backup_scheduler import (
    BackupCoordinator,
    due_history_schedule_key,
    due_schedule_key,
)
from backend.app.backup_service import (
    await_with_hard_timeout,
    classify_exception,
    close_download_stream,
    content_hash,
    detect_media_type,
    download_media_with_stall_timeout,
    message_metadata,
    forward_metadata,
    should_use_concurrent_media_download,
    telegram_stream_expected_size,
)
from backend.app.models import (
    ChatBackupRule,
    MessageVersion,
    TelegramEntity,
    TelegramEntityVersion,
)
from backend.app.history_update_service import summarize_history_sweep
from backend.app.origin_utils import is_allowed_browser_origin
from backend.app.message_content import serialize_message_content
from backend.app.media_downloader import (
    _prepare_parts,
    discard_parallel_download,
    parallel_download_file,
)
import backend.app.media_preview as media_preview
import backend.app.routes_archive as routes_archive
import backend.app.routes_entities as routes_entities
from backend.app.realtime import RealtimeHub
from backend.app.entity_service import (
    basic_profile,
    raw_telegram_id,
    require_message_sender_link,
    stable_hash,
)
from backend.app.entity_service import discover_message_via_bot
from backend.app.routes_entities import avatar_url
from backend.app.routes_overview import empty_overview
import backend.app.routes_overview as routes_overview
from backend.app.routes_archive import (
    custom_emoji_locks,
    custom_emoji_extension,
    display_versions,
    forward_origin_peer_id,
    is_server_restricted_placeholder,
    message_entities_payload,
    serialized_peer_id,
    SHARED_MEDIA_TYPES,
    shared_media_asset_types,
    shared_media_link,
)
from backend.app.schemas import AdminUserUpdate, ChatBackupRuleInput, Credentials, PasswordChange, TelegramPhoneRequest
from backend.app.security import SlidingWindowRateLimiter, hash_password, verify_password
from backend.app.telegram_auth import mask_phone
from backend.app.telegram_runtime import (
    account_session_stem,
    candidate_session_stem,
    media_stream_limit,
)
from telethon import types
from telethon.requestiter import RequestIter


def test_custom_emoji_file_extensions_are_stable() -> None:
    assert custom_emoji_extension("application/x-tgsticker") == ".tgs"
    assert custom_emoji_extension("video/webm") == ".webm"


@pytest.mark.asyncio
async def test_cached_custom_emoji_releases_db_before_file_response(monkeypatch, tmp_path) -> None:
    account = routes_archive.AuthorizedTelegramAccount(user_id=7, account_id=11)

    async def fake_authorized_account(_db, _token):
        return account

    class FakeDb:
        closed = False

        async def close(self):
            self.closed = True

    cached = tmp_path / "user_7" / "custom_emoji" / "123.webp"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"emoji")
    fake_db = FakeDb()
    monkeypatch.setattr(routes_archive, "authorized_telegram_account", fake_authorized_account)
    monkeypatch.setattr(
        routes_archive,
        "settings",
        SimpleNamespace(
            media_root=tmp_path,
            custom_emoji_download_concurrency=3,
            custom_emoji_download_timeout_seconds=45,
        ),
    )
    response = await routes_archive.archive_custom_emoji(123, fake_db, "session")
    assert fake_db.closed
    assert Path(response.path) == cached


@pytest.mark.asyncio
async def test_custom_emoji_downloads_are_globally_bounded(monkeypatch, tmp_path) -> None:
    account = routes_archive.AuthorizedTelegramAccount(user_id=7, account_id=11)
    active = 0
    max_active = 0

    async def fake_authorized_account(_db, _token):
        return account

    class FakeDb:
        closed = False

        async def close(self):
            self.closed = True

    class FakeClient:
        async def __call__(self, _request):
            return [SimpleNamespace(mime_type="image/webp")]

        async def download_media(self, _document, file):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(.02)
                Path(file).write_bytes(b"emoji")
                return file
            finally:
                active -= 1

    class FakeRuntime:
        @asynccontextmanager
        async def client(self, _account_id):
            yield FakeClient()

    databases = [FakeDb() for _ in range(5)]
    monkeypatch.setattr(routes_archive, "authorized_telegram_account", fake_authorized_account)
    monkeypatch.setattr(routes_archive, "runtime_manager", FakeRuntime())
    monkeypatch.setattr(routes_archive, "custom_emoji_slots", asyncio.Semaphore(2))
    monkeypatch.setattr(
        routes_archive,
        "settings",
        SimpleNamespace(
            media_root=tmp_path,
            custom_emoji_download_concurrency=2,
            custom_emoji_download_timeout_seconds=1,
        ),
    )
    responses = await asyncio.gather(*(
        routes_archive.archive_custom_emoji(200 + index, db, "session")
        for index, db in enumerate(databases)
    ))
    assert max_active == 2
    assert all(db.closed for db in databases)
    assert all(Path(response.path).is_file() for response in responses)
    assert not list(tmp_path.rglob("*.part*"))


@pytest.mark.asyncio
async def test_avatar_releases_db_before_file_response(monkeypatch, tmp_path) -> None:
    cached = tmp_path / "avatar.jpg"
    cached.write_bytes(b"avatar")

    async def fake_authorized_avatar(_db, _entity_id, _photo_id, _variant, _token):
        return routes_entities.AuthorizedAvatar(
            relative_path="avatar.jpg",
            mime_type="image/jpeg",
            sha256="abc",
        )

    class FakeDb:
        closed = False

        async def close(self):
            self.closed = True

    fake_db = FakeDb()
    monkeypatch.setattr(routes_entities, "authorized_avatar", fake_authorized_avatar)
    monkeypatch.setattr(routes_entities, "settings", SimpleNamespace(avatar_root=tmp_path))
    response = await routes_entities.entity_avatar(1, 2, "small", fake_db, "session")
    assert fake_db.closed
    assert Path(response.path) == cached


def test_history_sweep_progress_accumulates_continuation_runs() -> None:
    first = SimpleNamespace(
        id=1, status="success", candidate_count=1590, checked_count=1000,
        changed_count=2, deleted_count=1, media_completed_count=3, error_count=0,
    )
    second = SimpleNamespace(
        id=2, status="running", candidate_count=590, checked_count=200,
        changed_count=1, deleted_count=0, media_completed_count=2, error_count=0,
    )
    state = SimpleNamespace(status="running", next_run_at=object())
    progress = summarize_history_sweep([first, second], state)
    assert progress == {
        "id": 2,
        "status": "running",
        "candidate_count": 1590,
        "checked_count": 1200,
        "changed_count": 3,
        "deleted_count": 1,
        "media_completed_count": 5,
        "error_count": 0,
        "has_remaining": True,
    }


def test_history_sweep_progress_marks_waiting_continuation() -> None:
    run = SimpleNamespace(
        id=1, status="success", candidate_count=1590, checked_count=1000,
        changed_count=0, deleted_count=0, media_completed_count=0, error_count=0,
    )
    state = SimpleNamespace(status="idle", next_run_at=object())
    progress = summarize_history_sweep([run], state)
    assert progress["status"] == "continuing"
    assert progress["has_remaining"] is True


def restricted_message_version(version: int = 2) -> MessageVersion:
    return MessageVersion(
        archived_message_id=68,
        version=version,
        content_hash="a" * 64,
        text="This channel can’t be displayed because it violated Telegram's Terms of Service.",
        content_kind="text",
        content_json={},
        is_deleted=False,
        metadata_json={},
    )


def test_telegram_restriction_notice_is_detected_conservatively() -> None:
    assert is_server_restricted_placeholder(restricted_message_version())
    bot_notice = restricted_message_version()
    bot_notice.text = "This bot can’t be displayed because it violated Telegram's Terms of Service."
    assert is_server_restricted_placeholder(bot_notice)
    ordinary = restricted_message_version()
    ordinary.text = "This channel can't be displayed while the demo is loading."
    assert not is_server_restricted_placeholder(ordinary)


@pytest.mark.asyncio
async def test_restricted_current_version_falls_back_to_latest_normal_snapshot() -> None:
    normal = MessageVersion(
        archived_message_id=68,
        version=1,
        content_hash="b" * 64,
        text=None,
        content_kind="video",
        content_json={},
        is_deleted=False,
        metadata_json={"media_type": "video"},
    )

    class Scalars:
        def all(self):
            return [restricted_message_version(), normal]

    class Db:
        async def scalars(self, _statement):
            return Scalars()

    archived = SimpleNamespace(id=68)
    selected = await display_versions(Db(), [(archived, restricted_message_version())])
    assert selected[68] is normal
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


@pytest.mark.asyncio
async def test_overview_cache_coalesces_repeated_page_loads(monkeypatch) -> None:
    routes_overview.clear_overview_cache()
    calls = 0

    async def fake_build(_user, _db):
        nonlocal calls
        calls += 1
        return {"message_count": calls}

    monkeypatch.setattr(routes_overview, "build_overview", fake_build)
    monkeypatch.setattr(routes_overview.settings, "overview_cache_seconds", 10)
    user = SimpleNamespace(id=987654)
    first = await routes_overview.overview(user, object())
    second = await routes_overview.overview(user, object())

    assert first == second == {"message_count": 1}
    assert calls == 1
    routes_overview.clear_overview_cache(user.id)


def test_phone_requires_international_format() -> None:
    with pytest.raises(ValidationError):
        TelegramPhoneRequest(phone="13800000000")
    assert TelegramPhoneRequest(phone="+8613800000000").phone.startswith("+86")


def test_phone_mask_and_local_origins() -> None:
    assert mask_phone("+8613800000000").endswith("0000")
    assert "13800000000" not in mask_phone("+8613800000000")
    assert "http://localhost:5173" in get_settings().allowed_frontend_origins
    assert "http://127.0.0.1:5173" in get_settings().allowed_frontend_origins


def test_browser_origin_accepts_current_host_and_rejects_foreign_hosts() -> None:
    configured = ["http://localhost:5173"]
    assert is_allowed_browser_origin(
        "http://127.0.0.1:8000", "127.0.0.1:8000", configured
    )
    assert is_allowed_browser_origin(
        "https://backup.example.com", "backup.example.com", configured
    )
    assert is_allowed_browser_origin(
        "http://localhost:5173", "127.0.0.1:8000", configured
    )
    assert not is_allowed_browser_origin(
        "https://evil.example", "backup.example.com", configured
    )


def test_media_stream_limit_uses_premium_tier() -> None:
    assert media_stream_limit(False) == 3
    assert media_stream_limit(True) == 6


def test_media_stream_limit_has_safe_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_media_parallel_connections_regular", 0)
    monkeypatch.setattr(settings, "telegram_media_parallel_connections_premium", 20)

    assert media_stream_limit(False) == 1
    assert media_stream_limit(True) == 6


def test_existing_serial_media_part_is_not_restarted_as_concurrent(tmp_path) -> None:
    temporary = tmp_path / "video.mp4.part"
    threshold = get_settings().telegram_media_parallel_threshold_bytes

    assert should_use_concurrent_media_download(1, threshold, temporary)
    temporary.write_bytes(b"partial")
    assert not should_use_concurrent_media_download(1, threshold, temporary)


def test_account_session_path_is_derived_from_current_project(tmp_path) -> None:
    assert account_session_stem(tmp_path / "accounts", 7) == (
        tmp_path / "accounts" / "user_7" / "telegram"
    ).resolve()


def test_candidate_session_key_is_resolved_below_pending_directory(tmp_path) -> None:
    key = "user_7_0123456789abcdef0123456789abcdef"
    assert candidate_session_stem(tmp_path / "accounts", 7, key) == (
        tmp_path / "accounts" / "pending" / key
    ).resolve()


@pytest.mark.parametrize(
    "key",
    [
        "../telegram",
        "pending/user_7_0123456789abcdef0123456789abcdef",
        "user_8_0123456789abcdef0123456789abcdef",
        "user_7_not-a-uuid",
    ],
)
def test_candidate_session_key_rejects_unsafe_or_foreign_values(
    tmp_path, key
) -> None:
    with pytest.raises(ValueError):
        candidate_session_stem(tmp_path / "accounts", 7, key)


@pytest.mark.asyncio
async def test_background_preview_is_deduplicated(monkeypatch, tmp_path) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_preview(source, target, **_kwargs):
        started.set()
        await release.wait()
        return target

    monkeypatch.setattr(media_preview, "ensure_media_preview", fake_preview)
    source = tmp_path / "source.mp4"
    target = tmp_path / "preview.jpg"
    source.write_bytes(b"video")
    await media_preview.start_media_preview_workers(worker_count=1, queue_size=1)
    try:
        assert media_preview.schedule_media_preview(source, target, media_type="video")
        assert not media_preview.schedule_media_preview(source, target, media_type="video")
        await asyncio.wait_for(started.wait(), timeout=1)
        release.set()
        assert media_preview.preview_queue is not None
        await asyncio.wait_for(media_preview.preview_queue.join(), timeout=1)
        assert str(target) not in media_preview.scheduled_preview_targets
    finally:
        release.set()
        await media_preview.stop_media_preview_workers()


@pytest.mark.asyncio
async def test_background_preview_queue_is_bounded(monkeypatch, tmp_path) -> None:
    release = asyncio.Event()

    async def fake_preview(source, target, **_kwargs):
        await release.wait()
        return target

    monkeypatch.setattr(media_preview, "ensure_media_preview", fake_preview)
    await media_preview.start_media_preview_workers(worker_count=1, queue_size=1)
    try:
        first = tmp_path / "first.jpg"
        second = tmp_path / "second.jpg"
        assert media_preview.schedule_media_preview(tmp_path / "first.mp4", first, media_type="video")
        # No event-loop turn has allowed the worker to drain the queue yet.
        assert not media_preview.schedule_media_preview(tmp_path / "second.mp4", second, media_type="video")
        assert str(second) not in media_preview.scheduled_preview_targets
    finally:
        release.set()
        await media_preview.stop_media_preview_workers()


@pytest.mark.asyncio
async def test_backup_coordinator_waits_for_pipeline_cleanup(monkeypatch) -> None:
    started = asyncio.Event()
    cleaned_up = asyncio.Event()

    async def pending_backup(*_args, **_kwargs) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            # Give the cancellation path a scheduling point so the test catches
            # a coordinator that cancels the pipeline without joining it.
            await asyncio.sleep(0)
            cleaned_up.set()

    monkeypatch.setattr(backup_scheduler, "backup_rule", pending_backup)
    test_coordinator = BackupCoordinator()
    assert test_coordinator.launch(7, "manual")
    await asyncio.wait_for(started.wait(), timeout=1)

    await asyncio.wait_for(test_coordinator.stop(), timeout=1)

    assert cleaned_up.is_set()
    assert not any(
        task.get_name() == "tg-backup-pipeline-7" and not task.done()
        for task in asyncio.all_tasks()
    )


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


def test_cron_preview_uses_strict_scheduler_rules() -> None:
    from backend.app.schedule_utils import next_cron_runs

    base = datetime(2026, 8, 16, 13, 42)
    assert next_cron_runs("0 */1 * * ?", base) == [
        datetime(2026, 8, 16, 14, 0),
        datetime(2026, 8, 16, 15, 0),
        datetime(2026, 8, 16, 16, 0),
        datetime(2026, 8, 16, 17, 0),
        datetime(2026, 8, 16, 18, 0),
    ]
    assert next_cron_runs("0 9 * * * extra", base) == []


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
    assert "offline" in failure.detail


def test_webpage_embedded_media_is_not_treated_as_downloadable() -> None:
    embedded_photo = SimpleNamespace(id=123)
    message = SimpleNamespace(
        media=types.MessageMediaWebPage(
            webpage=types.WebPagePending(id=99, date=datetime.now(timezone.utc))
        ),
        photo=embedded_photo,
        document=None,
    )

    assert detect_media_type(message) is None
    metadata = message_metadata(message, None)
    assert metadata["media_type"] is None
    assert metadata["media_id"] is None


@pytest.mark.asyncio
async def test_uninitialized_telethon_download_stream_does_not_mask_error() -> None:
    class FailedDownloadIter(RequestIter):
        async def _init(self, **kwargs):
            raise OSError("original download failure")

        async def _load_next_chunk(self):
            return True

        async def close(self):
            if not self._sender:
                return

    stream = FailedDownloadIter(SimpleNamespace(), limit=1)
    with pytest.raises(OSError, match="original download failure"):
        try:
            await stream.__anext__()
        finally:
            await close_download_stream(stream)


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


def test_entity_phone_fields_use_the_expected_schema() -> None:
    assert "phone" in TelegramEntity.__table__.columns
    assert "phone" in TelegramEntityVersion.__table__.columns


def test_messages_with_sender_ids_require_entity_history_links() -> None:
    message = SimpleNamespace(sender_id=7)
    entity = SimpleNamespace(id=11)
    require_message_sender_link(message, entity, 13)
    with pytest.raises(RuntimeError, match="发送者实体关联"):
        require_message_sender_link(message, None, None)
    require_message_sender_link(SimpleNamespace(sender_id=None), None, None)


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


def test_photo_stream_size_matches_telethon_selected_variant() -> None:
    message = SimpleNamespace(
        file=SimpleNamespace(size=3024),
        photo=SimpleNamespace(sizes=[
            types.PhotoSize(type="m", w=320, h=299, size=3024),
            types.PhotoSizeProgressive(
                type="x", w=348, h=325, sizes=[1178, 2786, 2800]
            ),
        ]),
    )
    assert telegram_stream_expected_size(message) == 2800


@pytest.mark.asyncio
async def test_completed_progressive_photo_partial_is_accepted(tmp_path) -> None:
    partial = tmp_path / "progressive.jpg.part"
    partial.write_bytes(b"x" * 2800)
    message = SimpleNamespace(
        file=SimpleNamespace(size=3024),
        media=object(),
        photo=SimpleNamespace(sizes=[
            types.PhotoSize(type="m", w=320, h=299, size=3024),
            types.PhotoSizeProgressive(
                type="x", w=348, h=325, sizes=[1178, 2786, 2800]
            ),
        ]),
    )

    class CompletePhotoClient:
        consumed = False

        async def _chunks(self):
            self.consumed = True
            yield b"unexpected"

        def iter_download(self, source, *, offset, file_size):
            assert source is message.media
            assert offset == 2800
            assert file_size == 2800
            return self._chunks()

    client = CompletePhotoClient()
    result = await download_media_with_stall_timeout(client, message, partial)
    assert result == str(partial)
    assert client.consumed is False


@pytest.mark.asyncio
async def test_parallel_media_download_uses_one_client_for_concurrent_ranges(tmp_path) -> None:
    payload = bytes(range(251)) * 300
    request_size = 4096
    calls: list[tuple[int, int]] = []

    active = 0
    max_active = 0

    class RangeClient:
        def iter_download(
            self, source, *, offset, limit, chunk_size, request_size, file_size
        ):
            calls.append((offset, limit))

            async def chunks():
                nonlocal active, max_active
                active += 1
                max_active = max(max_active, active)
                cursor = offset
                try:
                    for _ in range(limit):
                        if cursor >= file_size:
                            break
                        await asyncio.sleep(0)
                        data = payload[cursor : min(cursor + request_size, file_size)]
                        cursor += len(data)
                        yield data
                finally:
                    active -= 1

            return chunks()

    temporary = tmp_path / "parallel.part"
    client = RangeClient()
    result = await parallel_download_file(
        client,
        3,
        object(),
        len(payload),
        temporary,
        stall_timeout=1,
        request_size=request_size,
    )

    assert temporary.read_bytes() == payload
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.streams == 3
    assert max_active == 3
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
        RangeClient(),
        2,
        object(),
        len(payload),
        temporary,
        stall_timeout=1,
        request_size=request_size,
    )

    assert temporary.read_bytes() == payload
    assert parts[0].start + first_completed in offsets


def test_parallel_download_shards_can_be_discarded_for_serial_fallback(tmp_path) -> None:
    temporary = tmp_path / "fallback.part"
    parts = _prepare_parts(temporary, 16384, 2, 4096)
    for part in parts:
        part.path.write_bytes(b"partial")
    temporary.with_name(f"{temporary.name}.assembling").write_bytes(b"partial")

    discard_parallel_download(temporary)

    assert not list(tmp_path.glob("*.chunk-*"))
    assert not list(tmp_path.glob("*.parallel.json"))
    assert not list(tmp_path.glob("*.assembling"))
