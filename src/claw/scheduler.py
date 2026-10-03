"""Gateway-owned cron wake loop (OpenClaw GatewayScheduler analogue)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from claw.events import AgentEvent
from claw.jobs import CronJob, scheduled_prompt
from claw.runner import AgentRunner, RunSnapshot

log = logging.getLogger(__name__)

EventHandler = Callable[[AgentEvent], Awaitable[None] | None]
JobHandler = Callable[[CronJob, RunSnapshot], Awaitable[None] | None]


async def run_due_jobs(
    runner: AgentRunner,
    *,
    session_key: str | None = None,
    on_event: EventHandler | None = None,
    on_job: JobHandler | None = None,
) -> list[tuple[CronJob, RunSnapshot]]:
    """Atomically claim and run due automation jobs (safe across workers)."""
    from claw.spacetime_jobs import get_job_store

    store = get_job_store(runner.settings)
    results: list[tuple[CronJob, RunSnapshot]] = []
    # Drain the due queue one claim at a time so two gateways never run the same job.
    while True:
        job = store.claim_due(session_key=session_key)
        if job is None:
            break
        try:
            snap = await runner.run_embedded(
                scheduled_prompt(job),
                session_key=job.session_key,
                on_event=on_event,
            )
            job.output = snap.output
            job.error = snap.error
            store.advance_after_run(job, success=snap.status == "ok")
            refreshed = store.get(job.id) or job
            results.append((refreshed, snap))
            if on_job is not None:
                maybe = on_job(refreshed, snap)
                if maybe is not None:
                    await maybe
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            store.advance_after_run(job, success=False)
            log.exception("cron job %s failed", job.id)
    return results


class CronService:
    """Long-lived in-process scheduler — executes jobs from the automation store."""

    def __init__(
        self,
        runner: AgentRunner,
        *,
        poll_s: float = 1.0,
        session_key: str | None = None,
    ) -> None:
        self.runner = runner
        self.poll_s = poll_s
        self.session_key = session_key
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="claw-cron")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        log.info("cron service started (poll=%ss)", self.poll_s)
        while not self._stop.is_set():
            try:
                results = await run_due_jobs(
                    self.runner, session_key=self.session_key
                )
                for job, snap in results:
                    log.info(
                        "cron job=%s name=%s status=%s schedule=%s",
                        job.id[:8],
                        job.name,
                        snap.status,
                        job.schedule.get("kind"),
                    )
            except Exception:
                log.exception("cron tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_s)
            except asyncio.TimeoutError:
                continue
