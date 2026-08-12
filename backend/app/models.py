from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    Time,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    telegram_account: Mapped[TelegramAccount | None] = relationship(
        back_populates="user", uselist=False
    )


class WebSession(Base):
    __tablename__ = "web_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship()

    __table_args__ = (Index("ix_web_sessions_user_expires", "user_id", "expires_at"),)


class TelegramAccount(Base):
    __tablename__ = "telegram_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_masked: Mapped[str | None] = mapped_column(String(32))
    session_path: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="telegram_account")


class TelegramLoginAttempt(Base):
    __tablename__ = "telegram_login_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    candidate_session_path: Mapped[str] = mapped_column(String(512), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    phone_code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="code_sent")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TelegramEntity(Base):
    __tablename__ = "telegram_entities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_account_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_accounts.id", ondelete="CASCADE"), nullable=False
    )
    peer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entity_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    phone_ciphertext: Mapped[str | None] = mapped_column(Text)
    about: Mapped[str | None] = mapped_column(Text)
    photo_id: Mapped[int | None] = mapped_column(BigInteger)
    is_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_scam: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_fake: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    access_state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="available"
    )
    current_profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    current_version: Mapped[int] = mapped_column(nullable=False, default=1)
    profile_json: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    first_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_basic_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_full_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_photo_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    next_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_telegram_entities_account_peer",
            "telegram_account_id",
            "peer_id",
            unique=True,
        ),
        Index(
            "ix_telegram_entities_account_refresh",
            "telegram_account_id",
            "next_refresh_at",
        ),
        Index("ix_telegram_entities_username", "telegram_account_id", "username"),
    )


class TelegramEntityVersion(Base):
    __tablename__ = "telegram_entity_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_entities.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(nullable=False)
    profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    phone_ciphertext: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_telegram_entity_versions_entity_version",
            "entity_id",
            "version",
            unique=True,
        ),
        Index("ix_telegram_entity_versions_observed", "entity_id", "observed_at"),
    )


class TelegramEntityPhoto(Base):
    __tablename__ = "telegram_entity_photos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_entities.id", ondelete="CASCADE"), nullable=False
    )
    telegram_photo_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    variant: Mapped[str] = mapped_column(String(16), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="available")
    first_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_telegram_entity_photos_entity_photo_variant",
            "entity_id",
            "telegram_photo_id",
            "variant",
            unique=True,
        ),
    )


class TelegramEntityMetricDaily(Base):
    __tablename__ = "telegram_entity_metric_daily"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_entities.id", ondelete="CASCADE"), nullable=False
    )
    sample_date: Mapped[date] = mapped_column(Date, nullable=False)
    participants_count: Mapped[int | None] = mapped_column(BigInteger)
    online_count: Mapped[int | None] = mapped_column(BigInteger)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_telegram_entity_metric_entity_date",
            "entity_id",
            "sample_date",
            unique=True,
        ),
    )


class TelegramContact(Base):
    __tablename__ = "telegram_contacts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_account_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_accounts.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_entities.id", ondelete="CASCADE"), nullable=False
    )
    is_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_mutual_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_telegram_contacts_account_entity",
            "telegram_account_id",
            "entity_id",
            unique=True,
        ),
    )


class TelegramEntityRefreshJob(Base):
    __tablename__ = "telegram_entity_refresh_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_entities.id", ondelete="CASCADE"), nullable=False
    )
    refresh_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    priority: Mapped[int] = mapped_column(nullable=False, default=50)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_telegram_entity_refresh_entity_kind",
            "entity_id",
            "refresh_kind",
            unique=True,
        ),
        Index(
            "ix_telegram_entity_refresh_due",
            "status",
            "next_run_at",
            "priority",
        ),
    )


class TelegramDialog(Base):
    __tablename__ = "telegram_dialogs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_account_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_accounts.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_entities.id", ondelete="SET NULL")
    )
    peer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    unread_count: Mapped[int] = mapped_column(nullable=False, default=0)
    unread_mentions_count: Mapped[int] = mapped_column(nullable=False, default=0)
    last_message_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_telegram_dialogs_account_peer",
            "telegram_account_id",
            "peer_id",
            unique=True,
        ),
        Index(
            "ix_telegram_dialogs_account_available_date",
            "telegram_account_id",
            "is_available",
            "last_message_date",
        ),
    )


class ChatBackupRule(Base):
    __tablename__ = "chat_backup_rules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    telegram_account_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_accounts.id", ondelete="CASCADE"), nullable=False
    )
    peer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_title: Mapped[str] = mapped_column(String(255), nullable=False)
    chat_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    schedule_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="weekly")
    backup_time: Mapped[time] = mapped_column(Time, nullable=False)
    weekdays: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    cron_expression: Mapped[str | None] = mapped_column(String(100))
    media_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    history_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    history_schedule_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="weekly"
    )
    history_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(3, 0))
    history_weekdays: Mapped[list[int] | None] = mapped_column(JSON)
    history_cron_expression: Mapped[str | None] = mapped_column(String(100))
    history_max_updates: Mapped[int] = mapped_column(nullable=False, default=10)
    history_start_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="earliest"
    )
    history_start_days_ago: Mapped[int | None] = mapped_column()
    history_end_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="latest"
    )
    history_end_days_ago: Mapped[int | None] = mapped_column()
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index(
            "uq_chat_backup_rules_account_peer",
            "telegram_account_id",
            "peer_id",
            unique=True,
        ),
        Index("ix_chat_backup_rules_user_enabled", "user_id", "enabled"),
        Index("ix_chat_backup_rules_user_removed", "user_id", "removed_at"),
    )


class ChatBackupState(Base):
    __tablename__ = "chat_backup_states"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("chat_backup_rules.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    last_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="idle")
    last_schedule_key: Mapped[str | None] = mapped_column(String(32))
    consecutive_failures: Mapped[int] = mapped_column(nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    retry_after_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class BackupRun(Base):
    __tablename__ = "backup_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("chat_backup_rules.id", ondelete="CASCADE"), nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    schedule_key: Mapped[str | None] = mapped_column(String(32))
    start_cursor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    end_cursor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    fetched_count: Mapped[int] = mapped_column(nullable=False, default=0)
    stored_count: Mapped[int] = mapped_column(nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(nullable=False, default=0)
    media_count: Mapped[int] = mapped_column(nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_backup_runs_rule_started", "rule_id", "started_at"),
        Index("ix_backup_runs_status", "status"),
    )


class ArchivedMessage(Base):
    __tablename__ = "archived_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_account_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_accounts.id", ondelete="CASCADE"), nullable=False
    )
    peer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sender_id: Mapped[int | None] = mapped_column(BigInteger)
    sender_entity_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_entities.id", ondelete="SET NULL")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash_schema: Mapped[int] = mapped_column(nullable=False, default=3)
    current_version: Mapped[int] = mapped_column(nullable=False, default=1)
    history_update_count: Mapped[int] = mapped_column(nullable=False, default=0)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    volatile_metadata_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    last_history_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_archived_messages_account_peer_message",
            "telegram_account_id",
            "peer_id",
            "message_id",
            unique=True,
        ),
        Index("ix_archived_messages_peer_sent", "telegram_account_id", "peer_id", "sent_at"),
    )


class MessageVersion(Base):
    __tablename__ = "message_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    archived_message_id: Mapped[int] = mapped_column(
        ForeignKey("archived_messages.id", ondelete="CASCADE"), nullable=False
    )
    sender_entity_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_entity_versions.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    content_kind: Mapped[str] = mapped_column(String(48), nullable=False, default="text")
    content_json: Mapped[dict[str, object] | None] = mapped_column(JSON)
    edit_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_message_versions_message_version",
            "archived_message_id",
            "version",
            unique=True,
        ),
        Index("ix_message_versions_message_observed", "archived_message_id", "observed_at"),
    )


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_version_id: Mapped[int] = mapped_column(
        ForeignKey("message_versions.id", ondelete="CASCADE"), nullable=False
    )
    media_type: Mapped[str] = mapped_column(String(24), nullable=False)
    telegram_media_id: Mapped[str | None] = mapped_column(String(128))
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    original_name: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_media_assets_version", "message_version_id"),)


class BackupItemEvent(Base):
    __tablename__ = "backup_item_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("backup_runs.id", ondelete="CASCADE"), nullable=False
    )
    peer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_backup_item_events_run", "run_id"),)


class HistoryUpdateState(Base):
    __tablename__ = "history_update_states"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("chat_backup_rules.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="idle")
    last_schedule_key: Mapped[str | None] = mapped_column(String(32))
    sweep_cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class HistoryUpdateRun(Base):
    __tablename__ = "history_update_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("chat_backup_rules.id", ondelete="CASCADE"), nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    candidate_count: Mapped[int] = mapped_column(nullable=False, default=0)
    checked_count: Mapped[int] = mapped_column(nullable=False, default=0)
    changed_count: Mapped[int] = mapped_column(nullable=False, default=0)
    deleted_count: Mapped[int] = mapped_column(nullable=False, default=0)
    media_completed_count: Mapped[int] = mapped_column(nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_history_update_runs_rule_started", "rule_id", "started_at"),)


class MessageMetricDaily(Base):
    __tablename__ = "message_metric_daily"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    archived_message_id: Mapped[int] = mapped_column(
        ForeignKey("archived_messages.id", ondelete="CASCADE"), nullable=False
    )
    sample_date: Mapped[date] = mapped_column(Date, nullable=False)
    views: Mapped[int | None] = mapped_column(BigInteger)
    forwards: Mapped[int | None] = mapped_column(BigInteger)
    replies: Mapped[int | None] = mapped_column(BigInteger)
    reactions_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "uq_message_metric_daily_message_date",
            "archived_message_id",
            "sample_date",
            unique=True,
        ),
    )


class HistoryUpdateItemEvent(Base):
    __tablename__ = "history_update_item_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("history_update_runs.id", ondelete="CASCADE"), nullable=False
    )
    archived_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("archived_messages.id", ondelete="SET NULL")
    )
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_history_update_item_events_run", "run_id"),)
