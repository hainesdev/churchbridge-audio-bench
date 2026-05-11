from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from urllib.parse import urlencode, urlsplit, urlunsplit

from .models import DisplayEvent


class DisplayFeedClient:
    """Small benchmark-owned display feed client.

    This borrows the current `/api/display/v1` event contract from
    `churchbridge-ai`, but keeps the benchmark controller independent from that
    repository's server internals.
    """

    def __init__(self, base_url: str, church_id: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._church_id = church_id

    def websocket_url(self) -> str:
        parts = urlsplit(self._base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        path = "/api/display/v1"
        query = urlencode({"church_id": self._church_id})
        return urlunsplit((scheme, parts.netloc, path, query, ""))

    async def connect_and_stream(self) -> AsyncIterator[DisplayEvent]:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError(
                "DisplayFeedClient requires the `websockets` package in the controller environment."
            ) from exc

        async with websockets.connect(self.websocket_url()) as websocket:
            while True:
                message = await websocket.recv()
                if isinstance(message, bytes):
                    message = message.decode("utf-8")
                payload = json.loads(message)
                yield DisplayEvent.from_payload(payload)

    async def collect_for_duration(self, seconds: float) -> list[DisplayEvent]:
        deadline = asyncio.get_running_loop().time() + max(seconds, 0)
        events: list[DisplayEvent] = []
        generator = self.connect_and_stream()
        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    event = await asyncio.wait_for(generator.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    break
                events.append(event)
        finally:
            await generator.aclose()
        return events
