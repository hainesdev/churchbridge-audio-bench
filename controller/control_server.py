from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .models import BenchmarkRunResultMessage, BenchmarkRunSpec, DeviceHello, TelemetryEvent


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BenchmarkControlServer:
    """Small benchmark-owned control server for the iPhone app."""

    def __init__(self, *, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._server: Any = None
        self._connected_event = asyncio.Event()
        self._incoming_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._active_websocket: Any = None

    async def start(self) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError(
                "BenchmarkControlServer requires the `websockets` package in the controller environment."
            ) from exc

        self._server = await websockets.serve(self._handle_client, self._host, self._port)

    async def stop(self) -> None:
        if self._active_websocket is not None:
            await self._active_websocket.close()
            self._active_websocket = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._connected_event.clear()

    async def wait_for_device(self, timeout: float = 60.0) -> DeviceHello:
        await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)
        message = await self._wait_for_message(
            expected_types={"device_hello"},
            timeout=timeout,
        )
        return DeviceHello.from_payload(message)

    async def run_remote_benchmark(
        self,
        *,
        run_spec: BenchmarkRunSpec,
        display_events_task: asyncio.Task[list[Any]],
    ) -> tuple[BenchmarkRunResultMessage, list[TelemetryEvent], list[Any]]:
        await self._send_json({"type": "run_spec", **run_spec.controller_payload()})

        handshake_message = await self._wait_for_message(
            expected_types={"ready", "run_rejected"},
            timeout=20.0,
            predicate=lambda payload: str(payload.get("run_id") or "") == run_spec.run_id,
        )
        if handshake_message["type"] == "run_rejected":
            reason = str(handshake_message.get("reason") or "Run was rejected by the device.")
            raise RuntimeError(reason)

        await self._send_json(
            {
                "type": "playback_started",
                "run_id": run_spec.run_id,
                "started_at": _utc_now_iso(),
            }
        )

        telemetry_events: list[TelemetryEvent] = []
        result_deadline = max(run_spec.run_duration_ms / 1000.0 + 20.0, 20.0)
        while True:
            payload = await self._wait_for_message(
                expected_types={"telemetry", "run_result", "run_rejected"},
                timeout=result_deadline,
            )
            payload_run_id = str(payload.get("run_id") or "")
            if payload_run_id and payload_run_id != run_spec.run_id:
                continue

            message_type = str(payload.get("type") or "")
            if message_type == "telemetry":
                telemetry_events.append(TelemetryEvent.from_payload(payload))
                continue
            if message_type == "run_rejected":
                reason = str(payload.get("reason") or "Run was rejected by the device.")
                raise RuntimeError(reason)

            run_result = BenchmarkRunResultMessage.from_payload(payload)
            await self._send_json(
                {
                    "type": "ack",
                    "run_id": run_spec.run_id,
                    "detail": "controller_saved_artifacts",
                }
            )
            display_events = await display_events_task
            return run_result, telemetry_events, display_events

    async def _handle_client(self, websocket: Any) -> None:
        self._active_websocket = websocket
        self._connected_event.set()
        await self._send_json({"type": "hello", "protocol_version": 1})
        try:
            async for raw_message in websocket:
                if isinstance(raw_message, bytes):
                    raw_message = raw_message.decode("utf-8")
                payload = json.loads(raw_message)
                if isinstance(payload, dict):
                    await self._incoming_queue.put(payload)
        finally:
            self._active_websocket = None
            self._connected_event.clear()

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if self._active_websocket is None:
            raise RuntimeError("No benchmark device is connected to the controller server.")
        await self._active_websocket.send(json.dumps(payload))

    async def _wait_for_message(
        self,
        *,
        expected_types: set[str],
        timeout: float,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        deferred: list[dict[str, Any]] = []

        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError(f"Timed out waiting for controller message types: {sorted(expected_types)}")

                payload = await asyncio.wait_for(self._incoming_queue.get(), timeout=remaining)
                payload_type = str(payload.get("type") or "")
                if payload_type in expected_types and (predicate is None or predicate(payload)):
                    return payload
                deferred.append(payload)
        finally:
            for payload in deferred:
                await self._incoming_queue.put(payload)
