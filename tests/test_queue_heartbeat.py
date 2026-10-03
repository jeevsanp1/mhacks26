from __future__ import annotations

import asyncio
from typing import Any

import pytest

from claw.config import Settings
from claw.heartbeat import HeartbeatService, parse_heartbeat_every_s
from claw.queue import normalize_queue_mode
from claw.runner import AgentRunner


def test_normalize_queue_mode() -> None:
    assert normalize_queue_mode("STEER") == "steer"
    assert normalize_queue_mode(None) == "followup"
    with pytest.raises(ValueError):
        normalize_queue_mode("nope")


def test_parse_heartbeat_every() -> None:
    assert parse_heartbeat_every_s("0m") == 0.0
    assert parse_heartbeat_every_s("off") == 0.0
    assert parse_heartbeat_every_s("30m") == 1800.0
    assert parse_heartbeat_every_s("90s") == 90.0


@pytest.mark.asyncio
async def test_followup_serializes(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    order: list[str] = []

    async def mark(run_id: str) -> None:
        snap = await runner.wait(run_id)
        order.append(run_id)
        assert snap.status == "ok"

    a = runner.start_turn("first", session_key="q", queue_mode="followup")
    b = runner.start_turn("second", session_key="q", queue_mode="followup")
    await asyncio.gather(mark(a.run_id), mark(b.run_id))
    assert order[0] == a.run_id
    assert order[1] == b.run_id


@pytest.mark.asyncio
async def test_collect_merges_while_busy(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    started = asyncio.Event()
    release = asyncio.Event()
    original = runner.agent.run_stream_events
    prompts: list[str] = []

    class _HangCM:
        def __init__(self, inner: Any, prompt: str) -> None:
            self._inner = inner
            self._prompt = prompt

        async def __aenter__(self) -> Any:
            prompts.append(self._prompt)
            if self._prompt == "hold":
                started.set()
                await release.wait()
            return await self._inner.__aenter__()

        async def __aexit__(self, *args: Any) -> Any:
            return await self._inner.__aexit__(*args)

    def _wrap(prompt: str, *args: Any, **kwargs: Any) -> Any:
        return _HangCM(original(prompt, *args, **kwargs), str(prompt))

    runner.agent.run_stream_events = _wrap  # type: ignore[method-assign]

    a = runner.start_turn("hold", session_key="collect", queue_mode="followup")
    await started.wait()
    b = runner.start_turn("alpha", session_key="collect", queue_mode="collect")
    c = runner.start_turn("beta", session_key="collect", queue_mode="collect")
    assert b.queued and c.queued
    release.set()
    snap_a = await runner.wait(a.run_id)
    assert snap_a.status == "ok"
    snap_b = await runner.wait(b.run_id)
    snap_c = await runner.wait(c.run_id)
    assert snap_b.status == "ok"
    assert snap_c.status == "ok"
    assert snap_b.output == snap_c.output
    assert any("alpha" in p and "beta" in p for p in prompts)


@pytest.mark.asyncio
async def test_interrupt_cancels_active(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    started = asyncio.Event()
    release = asyncio.Event()
    original = runner.agent.run_stream_events

    class _HangCM:
        def __init__(self, inner: Any, prompt: str) -> None:
            self._inner = inner
            self._prompt = prompt

        async def __aenter__(self) -> Any:
            started.set()
            if self._prompt == "hold":
                await release.wait()
            return await self._inner.__aenter__()

        async def __aexit__(self, *args: Any) -> Any:
            return await self._inner.__aexit__(*args)

    def _wrap(prompt: str, *args: Any, **kwargs: Any) -> Any:
        return _HangCM(original(prompt, *args, **kwargs), str(prompt))

    runner.agent.run_stream_events = _wrap  # type: ignore[method-assign]

    a = runner.start_turn("hold", session_key="int", queue_mode="followup")
    await started.wait()
    b = runner.start_turn("takeover", session_key="int", queue_mode="interrupt")
    snap_a = await runner.wait(a.run_id)
    assert snap_a.status == "error"
    assert snap_a.error == "interrupted"
    release.set()  # in case anything still waiting
    snap_b = await runner.wait(b.run_id)
    assert snap_b.status == "ok"


@pytest.mark.asyncio
async def test_steer_interrupts_like_openclaw_fallback(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    started = asyncio.Event()
    release = asyncio.Event()
    original = runner.agent.run_stream_events
    seen: list[str] = []

    class _HangCM:
        def __init__(self, inner: Any, prompt: str) -> None:
            self._inner = inner
            self._prompt = prompt

        async def __aenter__(self) -> Any:
            seen.append(self._prompt)
            started.set()
            if "takeover" not in self._prompt:
                await release.wait()
            return await self._inner.__aenter__()

        async def __aexit__(self, *args: Any) -> Any:
            return await self._inner.__aexit__(*args)

    def _wrap(prompt: str, *args: Any, **kwargs: Any) -> Any:
        return _HangCM(original(prompt, *args, **kwargs), str(prompt))

    runner.agent.run_stream_events = _wrap  # type: ignore[method-assign]

    a = runner.start_turn("hold", session_key="steer", queue_mode="followup")
    await started.wait()
    started.clear()
    b = runner.start_turn("takeover", session_key="steer", queue_mode="steer")
    snap_a = await runner.wait(a.run_id)
    assert snap_a.status == "error"
    release.set()
    snap_b = await runner.wait(b.run_id)
    assert snap_b.status == "ok"
    assert any("takeover" in p for p in seen)
    assert any("interrupted to apply this steer" in p for p in seen)


@pytest.mark.asyncio
async def test_heartbeat_defers_when_busy(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    started = asyncio.Event()
    release = asyncio.Event()
    original = runner.agent.run_stream_events

    class _HangCM:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        async def __aenter__(self) -> Any:
            started.set()
            await release.wait()
            return await self._inner.__aenter__()

        async def __aexit__(self, *args: Any) -> Any:
            return await self._inner.__aexit__(*args)

    def _wrap(*args: Any, **kwargs: Any) -> Any:
        return _HangCM(original(*args, **kwargs))

    runner.agent.run_stream_events = _wrap  # type: ignore[method-assign]
    runner.start_turn("hold", session_key="main", queue_mode="followup")
    await started.wait()

    hb = HeartbeatService(runner, every_s=0.05, session_key="main")
    result = await hb.tick_once()
    assert result["phase"] == "deferred"
    assert hb.skipped_busy == 1
    release.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_heartbeat_fires_when_idle(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    events: list[dict] = []

    async def on_event(payload: dict) -> None:
        events.append(payload)

    hb = HeartbeatService(
        runner, every_s=0.05, session_key="main", on_event=on_event
    )
    result = await hb.tick_once()
    assert result["phase"] == "end"
    assert result["status"] == "ok"
    assert any(e.get("phase") == "start" for e in events)
    assert any(e.get("phase") == "end" for e in events)
