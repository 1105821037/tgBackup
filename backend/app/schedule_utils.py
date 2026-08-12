from __future__ import annotations

from datetime import datetime, time

from croniter import croniter


def normalize_cron(value: str) -> str:
    return " ".join(value.strip().split())


def is_valid_five_field_cron(value: str) -> bool:
    expression = normalize_cron(value)
    return len(expression.split()) == 5 and croniter.is_valid(expression, strict=True)


def due_schedule_slot(
    schedule_kind: str,
    scheduled_time: time,
    weekdays: list[int] | None,
    cron_expression: str | None,
    now: datetime,
) -> str | None:
    local_now = now
    if schedule_kind == "cron":
        expression = normalize_cron(cron_expression or "")
        current_minute = local_now.replace(second=0, microsecond=0)
        if not is_valid_five_field_cron(expression) or not croniter.match(
            expression, current_minute
        ):
            return None
        return current_minute.strftime("%Y-%m-%d@%H:%M")
    if local_now.isoweekday() not in (weekdays or []):
        return None
    if (local_now.hour, local_now.minute) < (
        scheduled_time.hour,
        scheduled_time.minute,
    ):
        return None
    return f"{local_now.date().isoformat()}@{scheduled_time.strftime('%H:%M')}"
