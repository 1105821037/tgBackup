from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str
    mysql_password: str
    mysql_database: str = "tg_backup"

    telegram_api_id: int
    telegram_api_hash: str
    telegram_proxy_type: str = ""
    telegram_proxy_host: str = ""
    telegram_proxy_port: int | None = None
    telegram_proxy_username: str = ""
    telegram_proxy_password: str = ""

    cookie_secure: bool = False
    session_days: int = 30
    auth_login_attempts_per_account: int = 5
    auth_login_attempts_per_ip: int = 30
    auth_login_window_seconds: int = 300
    frontend_origin: str = "http://localhost:5173"
    backup_batch_size: int = 100
    backup_message_retries: int = 3
    backup_fetch_timeout_seconds: int = 60
    backup_stage_timeout_seconds: int = 75
    # A media transfer fails only after this many seconds without byte progress.
    backup_media_timeout_seconds: int = 60
    # Large files are split over independent, read-only Telegram connections.
    telegram_media_parallel_connections_regular: int = 3
    telegram_media_parallel_connections_premium: int = 6
    telegram_media_parallel_threshold_bytes: int = 8 * 1024 * 1024
    telegram_media_request_size_bytes: int = 512 * 1024
    backup_scheduler_interval_seconds: int = 20
    history_update_batch_size: int = 100
    history_update_max_messages_per_run: int = 1000
    telegram_account_operation_concurrency: int = 3
    telegram_runtime_ready_timeout_seconds: int = 45
    telegram_dialog_refresh_seconds: int = 900
    telegram_dialog_refresh_limit: int = 500
    telegram_entity_refresh_interval_seconds: int = 15
    telegram_entity_profile_ttl_hours: int = 24
    telegram_entity_cold_profile_ttl_hours: int = 168
    telegram_entity_worker_lease_seconds: int = 300

    @property
    def database_url(self) -> str:
        user = quote_plus(self.mysql_user)
        password = quote_plus(self.mysql_password)
        database = quote_plus(self.mysql_database)
        return (
            f"mysql+asyncmy://{user}:{password}@{self.mysql_host}:"
            f"{self.mysql_port}/{database}?charset=utf8mb4"
        )

    @property
    def server_database_url(self) -> str:
        user = quote_plus(self.mysql_user)
        password = quote_plus(self.mysql_password)
        return (
            f"mysql+asyncmy://{user}:{password}@{self.mysql_host}:"
            f"{self.mysql_port}/?charset=utf8mb4"
        )

    @property
    def account_sessions_root(self) -> Path:
        return PROJECT_ROOT / "data" / "accounts"

    @property
    def media_root(self) -> Path:
        return PROJECT_ROOT / "data" / "media"

    @property
    def avatar_root(self) -> Path:
        return PROJECT_ROOT / "data" / "avatars"

    @property
    def profile_key_path(self) -> Path:
        return PROJECT_ROOT / "data" / "profile.key"

    @property
    def frontend_dist(self) -> Path:
        return PROJECT_ROOT / "frontend" / "dist"

    @property
    def allowed_frontend_origins(self) -> list[str]:
        origins = {self.frontend_origin.rstrip("/")}
        if self.frontend_origin.rstrip("/") == "http://localhost:5173":
            origins.add("http://127.0.0.1:5173")
        return sorted(origins)

    def telegram_proxy(self) -> dict[str, object] | None:
        proxy_type = self.telegram_proxy_type.strip().lower()
        if not proxy_type:
            return None
        if not self.telegram_proxy_host or not self.telegram_proxy_port:
            raise RuntimeError("代理已启用，但缺少 TELEGRAM_PROXY_HOST/PORT")
        proxy: dict[str, object] = {
            "proxy_type": proxy_type,
            "addr": self.telegram_proxy_host,
            "port": self.telegram_proxy_port,
            "rdns": True,
        }
        if self.telegram_proxy_username:
            proxy["username"] = self.telegram_proxy_username
        if self.telegram_proxy_password:
            proxy["password"] = self.telegram_proxy_password
        return proxy


@lru_cache
def get_settings() -> Settings:
    return Settings()
