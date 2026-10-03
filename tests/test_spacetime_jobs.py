from __future__ import annotations

import os
from dataclasses import replace

import pytest

from claw.config import Settings
from claw.jobs import JobStore
from claw.spacetime_jobs import SpacetimeJobStore, get_job_store


def test_local_claim_due_is_exclusive(settings: Settings) -> None:
    store = JobStore(settings)
    store.add_from_openclaw(
        {
            "name": "a",
            "schedule": {"kind": "at", "at": "2020-01-01T00:00:00Z"},
            "payload": {"kind": "agentTurn", "message": "one"},
        },
        session_key="main",
    )
    first = store.claim_due(worker_id="w1")
    second = store.claim_due(worker_id="w2")
    assert first is not None
    assert first.status == "running"
    assert second is None


def test_get_job_store_defaults_to_file(settings: Settings) -> None:
    assert isinstance(get_job_store(settings), JobStore)


def test_get_job_store_spacetime_when_configured(settings: Settings) -> None:
    s = replace(
        settings,
        spacetime_uri="http://127.0.0.1:3001",
        spacetime_db="claw-jobs",
        spacetime_worker_id="test-worker",
    )
    assert s.spacetime_enabled
    store = get_job_store(s)
    assert isinstance(store, SpacetimeJobStore)


@pytest.mark.skipif(
    os.getenv("CLAW_SPACETIME_IT") != "1",
    reason="Set CLAW_SPACETIME_IT=1 with local SpacetimeDB on :3001",
)
def test_spacetime_exclusive_claim_integration(settings: Settings) -> None:
    s = replace(
        settings,
        spacetime_uri="http://127.0.0.1:3001",
        spacetime_db="claw-jobs",
        spacetime_worker_id="it-worker-a",
    )
    a = SpacetimeJobStore(s)
    b = SpacetimeJobStore(replace(s, spacetime_worker_id="it-worker-b"))
    for job in a.list(include_done=True):
        try:
            a.remove(job.id)
        except Exception:
            pass
    job = a.add_from_openclaw(
        {
            "name": "it",
            "schedule": {"kind": "at", "at": "2020-01-01T00:00:00Z"},
            "payload": {"kind": "agentTurn", "message": "coord"},
        },
        session_key="main",
    )
    c1 = a.claim_due(worker_id="it-worker-a")
    c2 = b.claim_due(worker_id="it-worker-b")
    assert c1 is not None and c1.id == job.id
    assert c2 is None
    c1.output = "ok"
    a.advance_after_run(c1, success=True)
    assert a.get(job.id) is None
