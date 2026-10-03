from __future__ import annotations

from datetime import date

import pytest
from pydantic_ai.messages import ModelRequest

from claw.agent import build_agent
from claw.config import Settings
from claw.memory import (
    MemoryStore,
    ensure_workspace,
    is_private_session,
    load_bootstrap_context,
)
from claw.runner import AgentRunner


def test_ensure_workspace_seeds_bootstrap(settings: Settings) -> None:
    root = ensure_workspace(settings)
    assert root == settings.agent_home
    assert (root / "AGENTS.md").is_file()
    assert (root / "MEMORY.md").is_file()
    assert (root / "memory").is_dir()
    # idempotent — does not overwrite
    (root / "MEMORY.md").write_text("# custom\n\n- keep me\n", encoding="utf-8")
    ensure_workspace(settings)
    assert "keep me" in (root / "MEMORY.md").read_text(encoding="utf-8")


def test_bootstrap_injects_memory_for_private_sessions(settings: Settings) -> None:
    ensure_workspace(settings)
    (settings.agent_home / "MEMORY.md").write_text(
        "# MEMORY.md\n\n- favorite color is blue\n",
        encoding="utf-8",
    )
    ctx = load_bootstrap_context(settings.agent_home, "main")
    assert "favorite color is blue" in ctx
    assert "Project Context" in ctx

    group_ctx = load_bootstrap_context(settings.agent_home, "group:team")
    assert "favorite color is blue" not in group_ctx


def test_is_private_session() -> None:
    assert is_private_session("main")
    assert is_private_session("demo")
    assert not is_private_session("group:x")
    assert not is_private_session("cron:job")


def test_memory_store_append_get_search(settings: Settings) -> None:
    ensure_workspace(settings)
    store = MemoryStore(settings.agent_home)
    assert "appended" in store.append("likes TypeScript", path="MEMORY.md")
    assert "TypeScript" in store.get("MEMORY.md")
    assert "TypeScript" in store.search("TypeScript")

    assert "appended" in store.append("shipped memory tools", path="daily")
    daily = settings.agent_home / "memory" / f"{date.today().isoformat()}.md"
    assert daily.is_file()
    assert "shipped memory tools" in store.get("daily")


def test_memory_path_rejects_escape(settings: Settings) -> None:
    store = MemoryStore(settings.agent_home)
    with pytest.raises(ValueError):
        store.get("../.env")
    with pytest.raises(ValueError):
        store.append("x", path="secrets.txt")


@pytest.mark.asyncio
async def test_runner_injects_memory_into_instructions(settings: Settings) -> None:
    ensure_workspace(settings)
    (settings.agent_home / "MEMORY.md").write_text(
        "# MEMORY.md\n\n- favorite color is blue\n",
        encoding="utf-8",
    )
    runner = AgentRunner.create(settings)
    snap = await runner.run_embedded("ping", session_key="main")
    assert snap.status == "ok"
    history = runner.store.load("main")
    requests = [m for m in history if isinstance(m, ModelRequest)]
    assert requests
    assert any(
        m.instructions and "favorite color is blue" in m.instructions for m in requests
    )


def test_remember_routes_renames_to_identity(settings: Settings) -> None:
    from claw.memory import MemoryStore, extract_rename, ensure_workspace

    ensure_workspace(settings)
    assert extract_rename("call you Reba") == "Reba"
    assert extract_rename("The user renamed me to Reba") == "Reba"
    assert extract_rename("call me Bob") is None  # user's name, not agent
    assert extract_rename("favorite color is blue") is None

    store = MemoryStore(settings.agent_home)
    store.append("The user renamed me to Claw", path="MEMORY.md")
    out = store.remember("call you Reba")
    assert "IDENTITY.md" in out
    identity = (settings.agent_home / "IDENTITY.md").read_text(encoding="utf-8")
    assert "Name: Reba" in identity
    memory = store.get("MEMORY.md")
    assert "renamed me" not in memory.lower()


@pytest.mark.asyncio
async def test_remember_tool_writes_long_term_memory(settings: Settings) -> None:
    ensure_workspace(settings)
    agent = build_agent(settings)
    from claw.memory import ClawDeps
    from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    async def model_fn(messages: list[ModelMessage], info) -> ModelResponse:  # noqa: ANN001
        tool_results = [
            p
            for m in messages
            if hasattr(m, "parts")
            for p in getattr(m, "parts", [])
            if getattr(p, "part_kind", None) == "tool-return"
        ]
        if not tool_results:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="remember",
                        args={"fact": "favorite color is blue"},
                        tool_call_id="r1",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(content="Got it — saved your favorite color.")])

    result = await agent.run(
        "Remember that my favorite color is blue",
        deps=ClawDeps(settings=settings, session_key="main"),
        model=FunctionModel(model_fn),
    )
    store = MemoryStore(settings.agent_home)
    assert "favorite color is blue" in store.get("MEMORY.md")
    assert "blue" in store.search("favorite color")
    assert "saved" in result.output.lower()


def test_reset_from_scratch_wipes_memory_sessions_and_jobs(settings: Settings) -> None:
    from claw.jobs import JobStore
    from claw.memory import reset_from_scratch
    from claw.sessions import SessionStore

    ensure_workspace(settings)
    (settings.agent_home / "MEMORY.md").write_text(
        "# MEMORY.md\n\n- secret fact\n", encoding="utf-8"
    )
    (settings.agent_home / "memory" / "2026-01-01.md").write_text("- daily\n")
    sessions = SessionStore(settings)
    sessions.save("demo", [])
    path = sessions.path_for("demo")
    path.write_text("[]", encoding="utf-8")
    JobStore(settings).add(delay="1m", message="ping", session_key="demo")

    counts = reset_from_scratch(settings)
    assert counts["sessions"] >= 1
    assert counts["jobs"] >= 1
    assert not sessions.path_for("demo").exists()
    mem = (settings.agent_home / "MEMORY.md").read_text(encoding="utf-8")
    assert "secret fact" not in mem
    assert "long-term memory" in mem
    assert not (settings.agent_home / "memory" / "2026-01-01.md").exists()
    assert JobStore(settings).list(include_done=True) == []
