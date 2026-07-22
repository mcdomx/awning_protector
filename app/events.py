import asyncio
from typing import Any, Dict, Set


class EventBroadcaster:
    """Generic SSE pub/sub: bounded per-subscriber queues, drop-on-full."""

    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def publish(self, message: Dict[str, Any]) -> None:
        dead: Set[asyncio.Queue] = set()
        for q in list(self._subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                dead.add(q)
        self._subscribers -= dead


app_events = EventBroadcaster()
