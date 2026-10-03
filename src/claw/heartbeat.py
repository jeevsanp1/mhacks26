"""Gateway-owned heartbeat monitor (OpenClaw ambient check-in)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from claw.jobs import parse_every_ms
from claw.runner import AgentRunner
from claw.sessions import sanitize_session_key

log = logging.getLogger(__name__)

HeartbeatHandler = Callable[[dict[str, Any]], Awaitable[None] | None]

DEFAULT_HEARTBEAT_PROMPT = (
    "[heartbeat] This is your periodic ambient check-in (OpenClaw-style). "
    "Review MEMORY.md / recent context quietly. "
    "If nothing needs the user's attention, reply with exactly: HEARTBEAT_OK. "
    "Only send a longer reply if something important is overdue or blocked."
)


def parse_heartbeat_every_s(value: str | None) -> float:
    """Parse `30m` / `1h` / `0m` / `off` → seconds. 0 disables."""
    raw = (value or "").strip().lower()
    if raw in {"", "0", "0m", "0s", "0ms", "off", "false", "none", "disable", "disabled"}:
        return 0.0
    return parse_every_ms(raw) / 1000.0


class HeartbeatService:
    """Periodic main-session turn; defers while the session lane is busy."""

    def __init__(
        self,
        runner: AgentRunner,
        *,
        every_s: float | None = None,
        session_key: str | None = None,
        prompt: str | None = None,
        on_event: HeartbeatHandler | None = None,
    ) -> None:
        settings = runner.settings
        if every_s is None:
            every_s = parse_heartbeat_every_s(settings.heartbeat_every)
        self.runner = runner
        self.every_s = every_s
        self.session_key = sanitize_session_key(
            session_key or settings.heartbeat_session or "main"
        )
        self.prompt = prompt or DEFAULT_HEARTBEAT_PROMPT
        self.on_event = on_event
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.last_fire_at_ms: int | None = None
        self.last_status: str | None = None
        self.skipped_busy = 0

    @property
    def enabled(self) -> bool:
        return self.every_s > 0

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="claw-heartbeat")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def tick_once(self) -> dict[str, Any]:
        """Run one heartbeat attempt (used by tests and cron.run-style force)."""
        if self.runner.is_session_busy(self.session_key):
            self.skipped_busy += 1
            result = {
                "phase": "deferred",
                "reason": "session_busy",
                "sessionKey": self.session_key,
                "skippedBusy": self.skipped_busy,
            }
            if self.on_event is not None:
                maybe = self.on_event(result)
                if maybe is not None:
                    await maybe
            log.info("heartbeat deferred session=%s", self.session_key)
            return result

        self.last_fire_at_ms = int(time.time() * 1000)
        if self.on_event is not None:
            maybe = self.on_event(
                {
                    "phase": "start",
                    "sessionKey": self.session_key,
                    "ts": self.last_fire_at_ms,
                }
            )
            if maybe is not None:
                await maybe

        snap = await self.runner.run_embedded(
            self.prompt,
            session_key=self.session_key,
            queue_mode="followup",
        )
        self.last_status = snap.status
        result = {
            "phase": "end",
            "sessionKey": self.session_key,
            "status": snap.status,
            "runId": snap.run_id,
            "output": snap.output,
            "error": snap.error,
            "ts": int(time.time() * 1000),
        }
        if self.on_event is not None:
            maybe = self.on_event(result)
            if maybe is not None:
                await maybe
        log.info(
            "heartbeat session=%s status=%s", self.session_key, snap.status
        )
        return result

    async def _loop(self) -> None:
        log.info(
            "heartbeat started every=%ss session=%s",
            self.every_s,
            self.session_key,
        )
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.every_s)
                break
            except asyncio.TimeoutError:
                pass
            try:
                await self.tick_once()
            except Exception:
                log.exception("heartbeat tick failed")
