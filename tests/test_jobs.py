from __future__ import annotations

import time

import pytest

from claw.config import Settings
from claw.jobs import (
    JobStore,
    compute_next_run_at_ms,
    parse_delay,
    recover_job_from_flat,
    scheduled_prompt,
)
from claw.runner import AgentRunner
from claw.scheduler import run_due_jobs


def test_parse_delay_relative() -> None:
    before = int(time.time() * 1000)
    at = parse_delay("1m")
    assert before + 55_000 <= at <= before + 65_000


def test_parse_delay_invalid() -> None:
    with pytest.raises(ValueError):
        parse_delay("nope")


def test_compute_next_every_and_cron() -> None:
    now = int(time.time() * 1000)
    nxt = compute_next_run_at_ms(
        {"kind": "every", "everyMs": 60_000, "anchorMs": now},
        now_ms=now,
        after_ms=now,
    )
    assert nxt == now + 60_000
    cron_next = compute_next_run_at_ms(
        {"kind": "cron", "expr": "0 0 * * *"},
        now_ms=now,
        after_ms=now,
    )
    assert cron_next > now


def test_recover_flat_and_nested() -> None:
    flat = recover_job_from_flat({"at": "1m", "message": "hi", "name": "x"})
    assert flat is not None
    assert flat["schedule"]["kind"] == "at"
    assert flat["payload"]["kind"] == "agentTurn"
    nested = recover_job_from_flat(
        {
            "job": {
                "schedule": {"kind": "every", "everyMs": 1000},
                "payload": {"kind": "systemEvent", "text": "tick"},
            }
        }
    )
    assert nested is not None
    assert nested["schedule"]["kind"] == "every"


def test_job_store_generic_shapes(settings: Settings) -> None:
    store = JobStore(settings)
    oneshot = store.add_from_openclaw(
        {
            "name": "oneshot",
            "schedule": {"kind": "at", "at": "1s"},
            "sessionTarget": "current",
            "payload": {"kind": "agentTurn", "message": "do work"},
        },
        session_key="main",
    )
    assert oneshot.delete_after_run is True
    assert oneshot.schedule["kind"] == "at"

    recurring = store.add_from_openclaw(
        {
            "name": "heartbeat-ish",
            "schedule": {"kind": "every", "everyMs": 60_000},
            "sessionTarget": "main",
            "payload": {"kind": "systemEvent", "text": "check queue"},
        },
        session_key="main",
    )
    assert recurring.delete_after_run is False
    assert recurring.schedule["kind"] == "every"
    assert "[automation:oneshot]" in scheduled_prompt(oneshot)


@pytest.mark.asyncio
async def test_cron_run_due_wakes_agent(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    store = JobStore(settings)
    job = store.add(
        delay="1s",
        message="Say exactly: scheduled-ok",
        session_key="sched",
        name="auto",
    )
    job.next_run_at_ms = int(time.time() * 1000) - 1
    store.save(job)
    results = await run_due_jobs(runner, session_key="sched")
    assert len(results) == 1
    _done, snap = results[0]
    assert snap.status == "ok"
    # one-shot deleteAfterRun removes successful jobs
    assert store.get(job.id) is None


@pytest.mark.asyncio
async def test_every_job_reschedules(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    store = JobStore(settings)
    job = store.add_from_openclaw(
        {
            "name": "tick",
            "schedule": {"kind": "every", "everyMs": 3_600_000},
            "payload": {"kind": "agentTurn", "message": "tick"},
            "deleteAfterRun": False,
        },
        session_key="every-sess",
    )
    first = job.next_run_at_ms
    job.next_run_at_ms = int(time.time() * 1000) - 1
    store.save(job)
    await run_due_jobs(runner, session_key="every-sess")
    refreshed = store.get(job.id)
    assert refreshed is not None
    assert refreshed.status == "pending"
    assert refreshed.next_run_at_ms > first or refreshed.next_run_at_ms > int(
        time.time() * 1000
    )
