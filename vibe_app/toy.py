from __future__ import annotations

import asyncio
import sys
import time
from typing import Optional

from buttplug import ButtplugClient, ButtplugDevice, DeviceOutputCommand, OutputType

from .actions import Action, setpoints
from .config import Config
from .runtime_state import RateLimitState


class ToyController:
    def __init__(self, config: Config, rate_limit: RateLimitState):
        self.config = config
        self.rate_limit = rate_limit
        self.client = ButtplugClient("vibe-app")
        self.device: Optional[ButtplugDevice] = None
        self._connected = False
        self._scanning = False
        self._queue: asyncio.Queue[Action] = asyncio.Queue()
        self._pending: list[Action] = []
        self._current: Optional[Action] = None
        self._queued_seconds = 0.0
        self._worker_task: Optional[asyncio.Task] = None
        self._current_cancel: Optional[asyncio.Event] = None
        self._cooldown_until = 0.0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def device_name(self) -> Optional[str]:
        return self.device.name if self.device else None

    def status(self) -> dict:
        return {"connected": self._connected, "device_name": self.device_name}

    def _on_device_added(self, device: ButtplugDevice) -> None:
        """Only reacts to our already-chosen toy reconnecting - the scan itself may see
        several nearby devices and we don't want to log each one, just our own connect/disconnect.
        """
        if self.device is not None and not self._connected and device.name == self.device.name:
            self.device = device
            self._connected = True
            print(f"[toy] connected: {device.name}", file=sys.stderr)

    def _on_device_removed(self, device: ButtplugDevice) -> None:
        if self.device is not None and device.index == self.device.index:
            self._connected = False
            print(f"[toy] disconnected: {device.name}", file=sys.stderr)

    async def connect_and_scan(self) -> None:
        self.client.on_device_added = self._on_device_added
        self.client.on_device_removed = self._on_device_removed
        await self.client.connect(f"ws://127.0.0.1:{self.config.engine_websocket_port}")
        await self._scan_and_choose()
        self._worker_task = asyncio.create_task(self._worker())

    async def rescan(self) -> dict:
        """Scan again for devices, e.g. if the toy wasn't on/paired at startup, or dropped
        and needs re-pairing - without restarting the whole app.
        """
        if not self.client.connected:
            return {"ok": False, "error": "not connected to the buttplug engine"}
        if self._scanning:
            return {"ok": False, "error": "a scan is already in progress"}
        if self._connected:
            return {"ok": True, "device_name": self.device_name, "connected": True}

        self._scanning = True
        try:
            await self._scan_and_choose()
        finally:
            self._scanning = False

        if not self._connected:
            return {"ok": False, "error": "no devices found"}
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker())
        return {"ok": True, "device_name": self.device_name, "connected": True}

    async def _scan_and_choose(self) -> None:
        print("[toy] scanning for devices - turn your toy on now", file=sys.stderr)
        await self.client.start_scanning()
        await asyncio.sleep(self.config.scan_timeout_s)
        await self.client.stop_scanning()

        if not self.client.devices:
            print("[toy] no devices found. Restart the app once your toy is on and paired.", file=sys.stderr)
            return

        chosen = None
        if self.config.device_name_filter:
            for d in self.client.devices.values():
                if self.config.device_name_filter.lower() in d.name.lower():
                    chosen = d
                    break
        if chosen is None:
            chosen = next(iter(self.client.devices.values()))

        if not chosen.has_output(OutputType.VIBRATE):
            print(f"[toy] warning: {chosen.name} has no vibrate output - actions will be no-ops", file=sys.stderr)

        self.device = chosen
        self._connected = True
        print(f"[toy] connected: {self.device.name}", file=sys.stderr)

    def enqueue(self, action: Action) -> str:
        """Try to queue an action for playback.

        Returns "ok", or the reason it was rejected: "rate_limited" (still cooling down from
        the last accepted action) or "queue_full" (over the safety cap).
        """
        now = time.monotonic()
        if now < self._cooldown_until:
            return "rate_limited"
        if self._queued_seconds + action.duration_s > self.config.max_queued_seconds:
            return "queue_full"
        self._queued_seconds += action.duration_s
        self._pending.append(action)
        self._queue.put_nowait(action)
        self._cooldown_until = now + action.duration_s + self.rate_limit.buffer_s
        return "ok"

    @property
    def cooldown_remaining_s(self) -> float:
        return max(0.0, self._cooldown_until - time.monotonic())

    def queue_status(self) -> dict:
        """What's currently playing and what's queued behind it, for the dashboard."""

        def describe(action: Action) -> dict:
            return {
                "intensity": action.intensity,
                "duration_s": action.duration_s,
                "pattern": action.pattern,
                "source_event": action.source_event,
                "source_account": action.source_account,
            }

        return {
            "current": describe(self._current) if self._current else None,
            "pending": [describe(a) for a in self._pending],
        }

    async def _worker(self) -> None:
        while True:
            action = await self._queue.get()
            if self._pending and self._pending[0] is action:
                self._pending.pop(0)
            self._queued_seconds = max(0.0, self._queued_seconds - action.duration_s)
            self._current = action
            self._current_cancel = asyncio.Event()
            try:
                await self._play(action, self._current_cancel)
            except Exception as e:
                print(f"[toy] error playing action: {e}", file=sys.stderr)
            finally:
                self._current = None
                self._current_cancel = None

    async def _play(self, action: Action, cancel: asyncio.Event) -> None:
        if self.device is None or not self.device.has_output(OutputType.VIBRATE):
            return
        last_t = 0.0
        for t, level in setpoints(action):
            if cancel.is_set():
                break
            await self.device.run_output(DeviceOutputCommand(OutputType.VIBRATE, level))
            wait = max(0.0, (t - last_t))
            last_t = t
            await asyncio.sleep(wait)
        await self.device.run_output(DeviceOutputCommand(OutputType.VIBRATE, 0.0))

    async def panic_stop(self) -> None:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._pending.clear()
        self._queued_seconds = 0.0
        if self._current_cancel is not None:
            self._current_cancel.set()
        if self.device is not None:
            await self.device.stop()

    async def disconnect(self) -> None:
        try:
            await self.panic_stop()
        except Exception:
            pass
        if self.client.connected:
            await self.client.disconnect()
