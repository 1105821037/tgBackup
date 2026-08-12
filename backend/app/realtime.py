from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


@dataclass(eq=False, slots=True)
class RealtimeSubscription:
    user_id: int
    queue: asyncio.Queue[dict[str, object]]

    def push(self, event: dict[str, object]) -> None:
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self.queue.put_nowait(event)


class RealtimeHub:
    def __init__(self, queue_size: int = 100) -> None:
        self._queue_size = queue_size
        self._subscribers: dict[int, set[RealtimeSubscription]] = {}
        self._lock = asyncio.Lock()
        self._sequences = itertools.count(1)

    def event(self, event_type: str, payload: dict[str, Any]) -> dict[str, object]:
        return {
            "version": 1,
            "sequence": next(self._sequences),
            "type": event_type,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "payload": _json_value(payload),
        }

    async def subscribe(self, user_id: int) -> RealtimeSubscription:
        subscription = RealtimeSubscription(
            user_id=user_id,
            queue=asyncio.Queue(maxsize=self._queue_size),
        )
        async with self._lock:
            self._subscribers.setdefault(user_id, set()).add(subscription)
        return subscription

    async def unsubscribe(self, subscription: RealtimeSubscription) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(subscription.user_id)
            if not subscribers:
                return
            subscribers.discard(subscription)
            if not subscribers:
                self._subscribers.pop(subscription.user_id, None)

    async def publish(
        self,
        user_id: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        event = self.event(event_type, payload)
        async with self._lock:
            subscribers = tuple(self._subscribers.get(user_id, ()))
        for subscription in subscribers:
            subscription.push(event)

    async def connection_count(self, user_id: int | None = None) -> int:
        async with self._lock:
            if user_id is not None:
                return len(self._subscribers.get(user_id, ()))
            return sum(len(items) for items in self._subscribers.values())


realtime_hub = RealtimeHub()
