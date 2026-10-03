from __future__ import annotations

import asyncio
import time

import pytest

from claw.config import Settings
from claw.events import AgentEvent, EventSequencer, map_pydantic_event
from claw.runner import AgentRunner
from claw.sessions import SessionStore
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    PartDeltaEvent,
    TextPartDelta,
    ToolReturnPart,
)


@pytest.mark.asyncio
async def test_embedded_test_model_smoke(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    events: list[AgentEvent] = []

    async def on_event(ev: AgentEvent) -> None:
        events.append(ev)

    snap = await runner.run_embedded("hello", session_key="smoke", on_event=on_event)
    assert snap.status == "ok"
    phases = [e.data.get("phase") for e in events if e.stream == "lifecycle"]
    assert "start" in phases
    assert "end" in phases
    # Session persisted
    store = SessionStore(settings)
    assert len(store.load("smoke")) >= 2


@pytest.mark.asyncio
async def test_start_turn_returns_before_completion(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    started = time.monotonic()
    accepted = runner.start_turn("slow-ish", session_key="race")
    # Immediate accept — must not wait for the model
    assert time.monotonic() - started < 0.5
    assert accepted.run_id
    snap = await runner.wait(accepted.run_id)
    assert snap.status == "ok"


@pytest.mark.asyncio
async def test_session_lock_serializes_turns(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    order: list[str] = []

    async def mark(run_id: str) -> None:
        snap = await runner.wait(run_id)
        order.append(run_id)
        assert snap.status == "ok"

    a = runner.start_turn("first", session_key="lane")
    b = runner.start_turn("second", session_key="lane")
    await asyncio.gather(mark(a.run_id), mark(b.run_id))
    # Both complete; second cannot finish before first because of session lock
    # (order of waiter completion follows finish order)
    assert set(order) == {a.run_id, b.run_id}
    assert order[0] == a.run_id


def test_event_mapping_text_delta() -> None:
    seq = EventSequencer(run_id="r1", session_key="main")
    mapped = map_pydantic_event(
        seq, PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="hi"))
    )
    assert len(mapped) == 1
    assert mapped[0].stream == "assistant"
    assert mapped[0].data["delta"] == "hi"


def test_event_mapping_tool_result_uses_part() -> None:
    seq = EventSequencer(run_id="r1", session_key="main")
    event = FunctionToolResultEvent(
        ToolReturnPart(tool_name="write_file", content="wrote hello.sh", tool_call_id="call-1")
    )
    mapped = map_pydantic_event(seq, event)
    assert len(mapped) == 1
    assert mapped[0].stream == "tool"
    assert mapped[0].data["phase"] == "result"
    assert mapped[0].data["toolName"] == "write_file"
    assert mapped[0].data["toolCallId"] == "call-1"
    assert mapped[0].data["result"] == "wrote hello.sh"


@pytest.mark.asyncio
async def test_idempotency_key_dedupes(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    a = runner.start_turn("x", session_key="idemp", idempotency_key="k1")
    b = runner.start_turn("x", session_key="idemp", idempotency_key="k1")
    assert a.run_id == b.run_id
    await runner.wait(a.run_id)
