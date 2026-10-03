"""Edge / stress coverage for memory, queue, gateway, sessions, heartbeat."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from claw.config import Settings
from claw.gateway.protocol import dumps, req
from claw.gateway.server import create_app
from claw.heartbeat import HeartbeatService
from claw.jobs import JobStore
from claw.memory import (
    MemoryStore,
    ensure_workspace,
    load_bootstrap_context,
    reset_from_scratch,
)
from claw.queue import normalize_queue_mode
from claw.runner import AgentRunner
from claw.sessions import SessionStore, new_session_key, sanitize_session_key


# --- memory / bootstrap edges ---


def test_memory_rejects_path_traversal_and_odd_paths(settings: Settings) -> None:
    ensure_workspace(settings)
    store = MemoryStore(settings.agent_home)
    for bad in (
        "../.env",
        "../../etc/passwd",
        "MEMORY.md/../.env",
        "memory/../../secret.md",
        "notes.md",
        "memory/hi.txt",
        "/etc/passwd",
    ):
        with pytest.raises(ValueError):
            store.get(bad)
        with pytest.raises(ValueError):
            store.append("x", path=bad)


def test_memory_append_rejects_empty(settings: Settings) -> None:
    ensure_workspace(settings)
    store = MemoryStore(settings.agent_home)
    with pytest.raises(ValueError):
        store.append("   ", path="MEMORY.md")
    with pytest.raises(ValueError):
        store.search("")


def test_bootstrap_truncates_huge_memory(settings: Settings) -> None:
    ensure_workspace(settings)
    huge = "# MEMORY.md\n\n" + ("x" * 50_000)
    (settings.agent_home / "MEMORY.md").write_text(huge, encoding="utf-8")
    ctx = load_bootstrap_context(
        settings.agent_home, "main", max_chars=200, total_max_chars=500
    )
    assert "truncated" in ctx
    assert len(ctx) < 2_000


def test_group_session_skips_memory_injection(settings: Settings) -> None:
    ensure_workspace(settings)
    (settings.agent_home / "MEMORY.md").write_text(
        "# MEMORY.md\n\n- secret-edge-fact\n", encoding="utf-8"
    )
    assert "secret-edge-fact" in load_bootstrap_context(settings.agent_home, "main")
    assert "secret-edge-fact" not in load_bootstrap_context(
        settings.agent_home, "group:lobby"
    )
    assert "secret-edge-fact" not in load_bootstrap_context(
        settings.agent_home, "channel:discord"
    )


def test_session_key_sanitize_and_new(settings: Settings) -> None:
    assert sanitize_session_key("a/b c!!") == "a_b_c_"
    assert sanitize_session_key("") == "main"
    key = new_session_key()
    assert key.startswith("agent:main:chat-")
    assert sanitize_session_key(key) == key
    assert ":" in sanitize_session_key("agent:main:main")


# --- runner / queue edges ---


@pytest.mark.asyncio
async def test_run_timeout(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    started = asyncio.Event()
    original = runner.agent.run_stream_events

    class _HangCM:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        async def __aenter__(self) -> Any:
            started.set()
            await asyncio.sleep(10)
            return await self._inner.__aenter__()

        async def __aexit__(self, *args: Any) -> Any:
            return await self._inner.__aexit__(*args)

    def _wrap(*args: Any, **kwargs: Any) -> Any:
        return _HangCM(original(*args, **kwargs))

    runner.agent.run_stream_events = _wrap  # type: ignore[method-assign]
    accepted = runner.start_turn("slow", session_key="to", timeout_s=0.05)
    await started.wait()
    snap = await runner.wait(accepted.run_id)
    assert snap.status == "timeout"


@pytest.mark.asyncio
async def test_parallel_sessions_do_not_block(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    a = runner.start_turn("a", session_key="s1")
    b = runner.start_turn("b", session_key="s2")
    snaps = await asyncio.gather(runner.wait(a.run_id), runner.wait(b.run_id))
    assert all(s.status == "ok" for s in snaps)


@pytest.mark.asyncio
async def test_interrupt_drops_collect_buffer(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    started = asyncio.Event()
    release = asyncio.Event()
    original = runner.agent.run_stream_events

    class _HangCM:
        def __init__(self, inner: Any, prompt: str) -> None:
            self._inner = inner
            self._prompt = prompt

        async def __aenter__(self) -> Any:
            if self._prompt == "hold":
                started.set()
                await release.wait()
            return await self._inner.__aenter__()

        async def __aexit__(self, *args: Any) -> Any:
            return await self._inner.__aexit__(*args)

    def _wrap(prompt: str, *args: Any, **kwargs: Any) -> Any:
        return _HangCM(original(prompt, *args, **kwargs), str(prompt))

    runner.agent.run_stream_events = _wrap  # type: ignore[method-assign]
    hold = runner.start_turn("hold", session_key="edge", queue_mode="followup")
    await started.wait()
    c1 = runner.start_turn("collect-me", session_key="edge", queue_mode="collect")
    assert c1.queued
    intr = runner.start_turn("takeover", session_key="edge", queue_mode="interrupt")
    assert not runner._collect_buf.get("edge")
    snap_hold = await runner.wait(hold.run_id)
    assert snap_hold.status == "error"
    snap_c = await runner.wait(c1.run_id)
    assert snap_c.status == "error"
    assert "dropped by interrupt" in (snap_c.error or "")
    release.set()
    snap_i = await runner.wait(intr.run_id)
    assert snap_i.status == "ok"


@pytest.mark.asyncio
async def test_invalid_queue_mode_raises(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    with pytest.raises(ValueError):
        runner.start_turn("x", queue_mode="banana")


@pytest.mark.asyncio
async def test_rapid_idempotency_under_concurrency(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    accepted = [
        runner.start_turn("same", session_key="id", idempotency_key="edge-k")
        for _ in range(20)
    ]
    assert len({a.run_id for a in accepted}) == 1
    snap = await runner.wait(accepted[0].run_id)
    assert snap.status == "ok"


# --- gateway edges ---


def test_gateway_rejects_empty_agent_message(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    app = create_app(settings, runner)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text(dumps(req("1", "connect", {"role": "operator"})))
            assert ws.receive_json()["ok"] is True
            ws.send_text(dumps(req("2", "agent", {"message": ""})))
            for _ in range(20):
                frame = ws.receive_json()
                if frame.get("type") == "res" and frame.get("id") == "2":
                    assert frame["ok"] is False
                    assert frame["error"]["code"] == "BAD_REQUEST"
                    return
            raise AssertionError("no response")


def test_gateway_rejects_non_connect_first_frame(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    app = create_app(settings, runner)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text(dumps(req("1", "health")))
            frame = ws.receive_json()
            assert frame["ok"] is False
            assert frame["error"]["code"] == "HANDSHAKE_REQUIRED"


def test_gateway_agent_queue_mode_collect(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    app = create_app(settings, runner)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text(dumps(req("1", "connect", {"role": "operator"})))
            assert ws.receive_json()["ok"] is True
            ws.send_text(
                dumps(
                    req(
                        "2",
                        "agent",
                        {
                            "message": "hello",
                            "sessionKey": "gqc",
                            "queueMode": "followup",
                        },
                    )
                )
            )
            for _ in range(50):
                frame = ws.receive_json()
                if frame.get("type") == "res" and frame.get("id") == "2":
                    assert frame["ok"] is True
                    assert frame["payload"]["queueMode"] == "followup"
                    run_id = frame["payload"]["runId"]
                    break
            else:
                raise AssertionError("no accept")
            ws.send_text(dumps(req("3", "agent.wait", {"runId": run_id})))
            for _ in range(100):
                frame = ws.receive_json()
                if frame.get("type") == "res" and frame.get("id") == "3":
                    assert frame["payload"]["status"] == "ok"
                    return
            raise AssertionError("wait failed")


def test_gateway_invalid_queue_mode(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    app = create_app(settings, runner)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text(dumps(req("1", "connect", {"role": "operator"})))
            assert ws.receive_json()["ok"] is True
            ws.send_text(
                dumps(req("2", "agent", {"message": "x", "queueMode": "nope"}))
            )
            for _ in range(20):
                frame = ws.receive_json()
                if frame.get("type") == "res" and frame.get("id") == "2":
                    assert frame["ok"] is False
                    return
            raise AssertionError("expected error")


# --- reset / heartbeat / jobs ---


def test_reset_from_scratch_is_idempotent(settings: Settings) -> None:
    ensure_workspace(settings)
    SessionStore(settings).path_for("x").write_text("[]")
    JobStore(settings).add(delay="1m", message="ping")
    a = reset_from_scratch(settings)
    b = reset_from_scratch(settings)
    assert a["sessions"] >= 1
    assert b["sessions"] == 0
    assert (settings.agent_home / "MEMORY.md").is_file()
    assert JobStore(settings).list(include_done=True) == []


@pytest.mark.asyncio
async def test_heartbeat_disabled_does_not_start(settings: Settings) -> None:
    runner = AgentRunner.create(settings)
    hb = HeartbeatService(runner, every_s=0.0)
    assert not hb.enabled
    await hb.start()
    assert hb._task is None


def test_normalize_queue_mode_whitespace() -> None:
    assert normalize_queue_mode("  Collect ") == "collect"
