from __future__ import annotations

from datetime import time
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator

from .schedule_utils import is_valid_five_field_cron, normalize_cron


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[\w.-]+$")
    password: str = Field(min_length=10, max_length=256)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


class AdminUserCreate(Credentials):
    is_owner: bool = False


class AdminUserUpdate(BaseModel):
    username: str | None = Field(
        default=None, min_length=3, max_length=64, pattern=r"^[\w.-]+$"
    )
    password: str | None = Field(default=None, min_length=10, max_length=256)
    is_owner: bool | None = None

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_change(self) -> "AdminUserUpdate":
        if self.username is None and self.password is None and self.is_owner is None:
            raise ValueError("至少需要修改一项")
        return self


class TelegramPhoneRequest(BaseModel):
    phone: str = Field(min_length=7, max_length=24, pattern=r"^\+[1-9]\d{6,22}$")


class TelegramCodeRequest(BaseModel):
    code: str = Field(min_length=3, max_length=16, pattern=r"^[\d\s-]+$")


class TelegramPasswordRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


MediaType = Literal[
    "photo",
    "video",
    "audio",
    "voice",
    "document",
    "animation",
    "sticker",
]


class ChatBackupRuleInput(BaseModel):
    enabled: bool = True
    schedule_kind: Literal["weekly", "cron"] = "weekly"
    backup_time: time = time(9, 0)
    weekdays: list[int] = Field(default_factory=lambda: list(range(1, 8)), max_length=7)
    cron_expression: str | None = Field(default=None, max_length=100)
    media_types: list[MediaType] = Field(default_factory=list)
    history_enabled: bool = False
    history_schedule_kind: Literal["weekly", "cron"] = "weekly"
    history_time: time = time(3, 0)
    history_weekdays: list[int] = Field(
        default_factory=lambda: list(range(1, 8)), max_length=7
    )
    history_cron_expression: str | None = Field(default=None, max_length=100)
    history_max_updates: int = Field(default=10, ge=1, le=100)
    history_start_kind: Literal["earliest", "days_ago"] = "earliest"
    history_start_days_ago: int | None = Field(default=None, ge=1, le=36500)
    history_end_kind: Literal["latest", "days_ago"] = "latest"
    history_end_days_ago: int | None = Field(default=None, ge=0, le=36500)

    @field_validator("weekdays")
    @classmethod
    def normalize_weekdays(cls, value: list[int]) -> list[int]:
        if any(day < 1 or day > 7 for day in value):
            raise ValueError("星期必须在 1 到 7 之间")
        return sorted(set(value))

    @field_validator("media_types")
    @classmethod
    def unique_media_types(cls, value: list[MediaType]) -> list[MediaType]:
        return list(dict.fromkeys(value))

    @field_validator("history_weekdays")
    @classmethod
    def normalize_history_weekdays(cls, value: list[int]) -> list[int]:
        if any(day < 1 or day > 7 for day in value):
            raise ValueError("星期必须在 1 到 7 之间")
        return sorted(set(value))

    @field_validator("cron_expression", "history_cron_expression", mode="before")
    @classmethod
    def normalize_cron_expression(cls, value: object) -> object:
        return normalize_cron(value) if isinstance(value, str) and value.strip() else None

    @model_validator(mode="after")
    def validate_schedule(self) -> "ChatBackupRuleInput":
        if self.schedule_kind == "weekly":
            if not self.weekdays:
                raise ValueError("每周备份至少选择一天")
            self.cron_expression = None
        elif not self.cron_expression or not is_valid_five_field_cron(
            self.cron_expression
        ):
            raise ValueError("请输入有效的 5 段 Cron 表达式")
        if self.history_schedule_kind == "weekly":
            if not self.history_weekdays:
                raise ValueError("历史消息更新至少选择一天")
            self.history_cron_expression = None
        elif not self.history_cron_expression or not is_valid_five_field_cron(
            self.history_cron_expression
        ):
            raise ValueError("请输入有效的历史消息更新 Cron 表达式")
        if self.history_start_kind == "earliest":
            self.history_start_days_ago = None
        elif self.history_start_days_ago is None:
            raise ValueError("请输入历史消息更新开始天数")
        if self.history_end_kind == "latest":
            self.history_end_days_ago = None
        elif self.history_end_days_ago is None:
            raise ValueError("请输入历史消息更新结束天数")
        if (
            self.history_start_kind == "days_ago"
            and self.history_end_kind == "days_ago"
            and self.history_start_days_ago is not None
            and self.history_end_days_ago is not None
            and self.history_start_days_ago <= self.history_end_days_ago
        ):
            raise ValueError("检查范围的开始位置必须早于结束位置")
        return self
