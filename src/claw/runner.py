"""Session-serialized agent turns with OpenClaw-style queue + event emission."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic_ai import Agent
from pydantic_ai.run import AgentRunResultEvent

from claw.agent import build_agent
from claw.config import Settings, get_settings
from claw.events import AgentEvent, EventSequencer, map_pydantic_event
from claw.memory import ClawDeps, ensure_workspace
from claw.queue import QueueMode, normalize_queue_mode
from claw.routing import DeliveryContext
from claw.sessions import SessionStore, sanitize_session_key

EventHandler = Callable[[AgentEvent], Awaitable[None] | None]
TerminalStatus = Literal["ok", "error", "timeout"]


@dataclass
class RunSnapshot:
    run_id: str
    session_key: str
    status: TerminalStatus
    started_at: int
    ended_at: int
    error: str | None = None
    output: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "runId": self.run_id,
            "sessionKey": self.session_key,
            "status": self.status,
            "startedAt": self.started_at,
            "endedAt": self.ended_at,
        }
        if self.error:
            payload["error"] = self.error
        if self.output is not None:
            payload["output"] = self.output
        return payload


@dataclass
class AcceptedRun:
    run_id: str
    accepted_at: int
    session_key: str
    queue_mode: QueueMode = "followup"
    queued: bool = False
    delivery: DeliveryContext | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "runId": self.run_id,
            "acceptedAt": self.accepted_at,
            "sessionKey": self.session_key,
            "queueMode": self.queue_mode,
            "queued": self.queued,
        }
        if self.delivery is not None:
            payload["delivery"] = self.delivery.to_payload()
        return payload


@dataclass
class _CollectItem:
    accepted: AcceptedRun
    message: str
    timeout_s: float | None
    delivery: DeliveryContext | None = None


@dataclass
class AgentRunner:
    """Owns per-session lanes, queue modes, idempotency cache, and run waiters."""

    settings: Settings
    store: SessionStore
    agent: Agent
    _handlers: list[EventHandler] = field(default_factory=list)
    _snapshots: dict[str, RunSnapshot] = field(default_factory=dict)
    _waiters: dict[str, list[asyncio.Future[RunSnapshot]]] = field(default_factory=dict)
    _idempotency: dict[str, AcceptedRun] = field(default_factory=dict)
    _tasks: set[asyncio.Task[None]] = field(default_factory=set)
    _session_tasks: dict[str, set[asyncio.Task[None]]] = field(default_factory=dict)
    _active: dict[str, str] = field(default_factory=dict)  # session -> run_id
    _collect_buf: dict[str, list[_CollectItem]] = field(default_factory=dict)

    @classmethod
    def create(cls, settings: Settings | None = None) -> AgentRunner:
        settings = settings or get_settings()
        settings.sessions_dir.mkdir(parents=True, exist_ok=True)
        ensure_workspace(settings)
        return cls(
            settings=settings,
            store=SessionStore(settings),
            agent=build_agent(settings),
        )

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        self._handlers.append(handler)

        def unsubscribe() -> None:
            if handler in self._handlers:
                self._handlers.remove(handler)

        return unsubscribe

    async def _emit(self, event: AgentEvent) -> None:
        for handler in list(self._handlers):
            result = handler(event)
            if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
                await result  # type: ignore[arg-type]

    def is_session_busy(self, session_key: str = "main") -> bool:
        """True if a turn is active or collect-buffered for this session."""
        key = sanitize_session_key(session_key)
        if key in self._active:
            return True
        if self._collect_buf.get(key):
            return True
        tasks = self._session_tasks.get(key) or set()
        return any(not t.done() for t in tasks)

    def start_turn(
        self,
        message: str,
        *,
        session_key: str = "main",
        idempotency_key: str | None = None,
        timeout_s: float | None = None,
        queue_mode: str | None = None,
        delivery: DeliveryContext | None = None,
    ) -> AcceptedRun:
        session_key = sanitize_session_key(session_key)
        mode = normalize_queue_mode(
            queue_mode, default=normalize_queue_mode(self.settings.queue_mode)
        )
        if idempotency_key:
            cached = self._idempotency.get(idempotency_key)
            if cached is not None:
                return cached

        run_id = str(uuid.uuid4())
        accepted = AcceptedRun(
            run_id=run_id,
            accepted_at=int(time.time() * 1000),
            session_key=session_key,
            queue_mode=mode,
            queued=False,
            delivery=delivery,
        )
        if idempotency_key:
            self._idempotency[idempotency_key] = accepted
            if len(self._idempotency) > 256:
                for key in list(self._idempotency)[:64]:
                    self._idempotency.pop(key, None)

        busy = self.is_session_busy(session_key)

        if mode == "collect" and busy:
            accepted.queued = True
            self._collect_buf.setdefault(session_key, []).append(
                _CollectItem(
                    accepted=accepted,
                    message=message,
                    timeout_s=timeout_s,
                    delivery=delivery,
                )
            )
            return accepted

        if mode in {"interrupt", "steer"} and busy:
            self._cancel_session(session_key, reason=mode)

        if mode == "steer" and busy:
            message = (
                f"{message}\n\n"
                "(Prior turn was interrupted to apply this steer — "
                "respond with the latest user intent.)"
            )

        self._spawn(
            run_id,
            session_key,
            message,
            timeout_s=timeout_s,
            finish_run_ids=[run_id],
            delivery=delivery,
        )
        return accepted

    def _resolve_snapshot(self, snapshot: RunSnapshot) -> None:
        """Synchronously publish a terminal snapshot to waiters."""
        self._snapshots[snapshot.run_id] = snapshot
        for fut in self._waiters.pop(snapshot.run_id, []):
            if not fut.done():
                fut.set_result(snapshot)

    def _cancel_session(self, session_key: str, *, reason: str) -> None:
        # Drop collect buffer — interrupt/steer replace pending work.
        # Finish orphaned collect waiters so agent.wait cannot hang forever.
        buf = self._collect_buf.pop(session_key, [])
        now = int(time.time() * 1000)
        for item in buf:
            self._resolve_snapshot(
                RunSnapshot(
                    run_id=item.accepted.run_id,
                    session_key=session_key,
                    status="error",
                    started_at=item.accepted.accepted_at,
                    ended_at=now,
                    error=f"dropped by {reason}",
                )
            )
        for task in list(self._session_tasks.get(session_key) or ()):
            if not task.done():
                task.cancel()
        self._active.pop(session_key, None)

    def _spawn(
        self,
        run_id: str,
        session_key: str,
        message: str,
        *,
        timeout_s: float | None,
        finish_run_ids: list[str],
        delivery: DeliveryContext | None = None,
    ) -> None:
        task = asyncio.create_task(
            self._run_turn(
                run_id,
                session_key,
                message,
                timeout_s=timeout_s,
                finish_run_ids=finish_run_ids,
                delivery=delivery,
            ),
            name=f"agent-run-{run_id}",
        )
        self._tasks.add(task)
        self._session_tasks.setdefault(session_key, set()).add(task)

        def _done(t: asyncio.Task[None]) -> None:
            self._tasks.discard(t)
            bucket = self._session_tasks.get(session_key)
            if bucket is not None:
                bucket.discard(t)
                if not bucket:
                    self._session_tasks.pop(session_key, None)

        task.add_done_callback(_done)

    async def run_embedded(
        self,
        message: str,
        *,
        session_key: str = "main",
        timeout_s: float | None = None,
        on_event: EventHandler | None = None,
        queue_mode: str | None = None,
    ) -> RunSnapshot:
        """Run a turn and wait for completion (CLI `agent exec`)."""
        unsub = self.subscribe(on_event) if on_event else None
        try:
            accepted = self.start_turn(
                message,
                session_key=session_key,
                timeout_s=timeout_s,
                queue_mode=queue_mode,
            )
            return await self.wait(accepted.run_id, timeout_s=timeout_s)
        finally:
            if unsub:
                unsub()

    async def wait(
        self,
        run_id: str,
        *,
        timeout_s: float | None = None,
    ) -> RunSnapshot:
        if run_id in self._snapshots:
            return self._snapshots[run_id]

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[RunSnapshot] = loop.create_future()
        self._waiters.setdefault(run_id, []).append(fut)
        try:
            if timeout_s is None:
                return await fut
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            snap = RunSnapshot(
                run_id=run_id,
                session_key="",
                status="timeout",
                started_at=int(time.time() * 1000),
                ended_at=int(time.time() * 1000),
                error="agent.wait timed out",
            )
            return snap

    async def _finish(self, snapshot: RunSnapshot, *, also: list[str] | None = None) -> None:
        ids = [snapshot.run_id]
        if also:
            for rid in also:
                if rid not in ids:
                    ids.append(rid)
        for rid in ids:
            snap = (
                snapshot
                if rid == snapshot.run_id
                else RunSnapshot(
                    run_id=rid,
                    session_key=snapshot.session_key,
                    status=snapshot.status,
                    started_at=snapshot.started_at,
                    ended_at=snapshot.ended_at,
                    error=snapshot.error,
                    output=snapshot.output,
                )
            )
            self._resolve_snapshot(snap)

    def _drain_collect(self, session_key: str) -> None:
        buf = self._collect_buf.pop(session_key, [])
        if not buf:
            return
        combined = "\n\n".join(item.message for item in buf)
        finish_ids = [item.accepted.run_id for item in buf]
        primary = buf[0]
        timeout_s = primary.timeout_s
        for item in buf[1:]:
            if item.timeout_s is not None:
                if timeout_s is None:
                    timeout_s = item.timeout_s
                else:
                    timeout_s = max(timeout_s, item.timeout_s)
        delivery = next(
            (item.delivery for item in buf if item.delivery is not None), None
        )
        self._spawn(
            primary.accepted.run_id,
            session_key,
            combined,
            timeout_s=timeout_s,
            finish_run_ids=finish_ids,
            delivery=delivery,
        )

    async def _run_turn(
        self,
        run_id: str,
        session_key: str,
        message: str,
        *,
        timeout_s: float | None,
        finish_run_ids: list[str],
        delivery: DeliveryContext | None = None,
    ) -> None:
        seq = EventSequencer(run_id=run_id, session_key=session_key)
        started_at = int(time.time() * 1000)
        lock = self.store.lock_for(session_key)
        also = [rid for rid in finish_run_ids if rid != run_id]

        drain_collect = False
        try:
            async with lock:
                self._active[session_key] = run_id
                await self._emit(seq.lifecycle("start", startedAt=started_at))
                output: str | None = None
                try:
                    history = self.store.load(session_key)

                    async def _consume() -> str | None:
                        result_output: str | None = None
                        deps = ClawDeps(
                            settings=self.settings,
                            session_key=session_key,
                            delivery=delivery,
                        )
                        async with self.agent.run_stream_events(
                            message,
                            message_history=history,
                            deps=deps,
                        ) as event_stream:
                            async for event in event_stream:
                                try:
                                    mapped_events = map_pydantic_event(seq, event)
                                except Exception:
                                    mapped_events = []
                                for mapped in mapped_events:
                                    try:
                                        await self._emit(mapped)
                                    except Exception:
                                        pass
                                if isinstance(event, AgentRunResultEvent):
                                    result = event.result
                                    self.store.append(session_key, result.new_messages())
                                    out = result.output
                                    result_output = (
                                        out
                                        if isinstance(out, str)
                                        else (None if out is None else str(out))
                                    )
                        return result_output

                    if timeout_s is not None:
                        output = await asyncio.wait_for(_consume(), timeout=timeout_s)
                    else:
                        output = await _consume()

                    ended_at = int(time.time() * 1000)
                    await self._emit(
                        seq.lifecycle(
                            "end", status="ok", startedAt=started_at, endedAt=ended_at
                        )
                    )
                    await self._finish(
                        RunSnapshot(
                            run_id=run_id,
                            session_key=session_key,
                            status="ok",
                            started_at=started_at,
                            ended_at=ended_at,
                            output=output,
                        ),
                        also=also,
                    )
                except asyncio.TimeoutError:
                    ended_at = int(time.time() * 1000)
                    err = "run timed out"
                    await self._emit(
                        seq.lifecycle(
                            "error",
                            status="timeout",
                            error=err,
                            startedAt=started_at,
                            endedAt=ended_at,
                        )
                    )
                    await self._finish(
                        RunSnapshot(
                            run_id=run_id,
                            session_key=session_key,
                            status="timeout",
                            started_at=started_at,
                            ended_at=ended_at,
                            error=err,
                        ),
                        also=also,
                    )
                except Exception as exc:  # noqa: BLE001 — surface to client
                    ended_at = int(time.time() * 1000)
                    err = f"{type(exc).__name__}: {exc}"
                    await self._emit(
                        seq.lifecycle(
                            "error",
                            status="error",
                            error=err,
                            startedAt=started_at,
                            endedAt=ended_at,
                        )
                    )
                    await self._finish(
                        RunSnapshot(
                            run_id=run_id,
                            session_key=session_key,
                            status="error",
                            started_at=started_at,
                            ended_at=ended_at,
                            error=err,
                        ),
                        also=also,
                    )
                finally:
                    self._active.pop(session_key, None)
                    drain_collect = True
        except asyncio.CancelledError:
            ended_at = int(time.time() * 1000)
            err = "interrupted"
            try:
                await self._emit(
                    seq.lifecycle(
                        "error",
                        status="error",
                        error=err,
                        startedAt=started_at,
                        endedAt=ended_at,
                    )
                )
            except Exception:
                pass
            await self._finish(
                RunSnapshot(
                    run_id=run_id,
                    session_key=session_key,
                    status="error",
                    started_at=started_at,
                    ended_at=ended_at,
                    error=err,
                ),
                also=also,
            )
            self._active.pop(session_key, None)
            raise
        finally:
            # Drain after the session lock is released to avoid deadlock.
            if drain_collect:
                self._drain_collect(session_key)
