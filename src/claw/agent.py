"""Pydantic AI agent factory with harness capabilities and workspace memory."""

from __future__ import annotations

from functools import lru_cache

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import LocalWorkspace, WebSearch
from pydantic_ai_harness import Coder

from claw.automations import register_automations_tool
from claw.config import Settings, get_settings
from claw.memory import (
    ClawDeps,
    MemoryStore,
    ensure_workspace,
    load_bootstrap_context,
)
from claw.web_search import register_web_search_tool

# Re-export for callers that import deps from claw.agent
__all__ = ["ClawDeps", "build_agent", "get_agent", "reset_agent_cache", "INSTRUCTIONS"]

INSTRUCTIONS = (
    "You are Claw, a local operator assistant. "
    "Use tools when they help answer accurately. "
    "Prefer concise, actionable replies. "
    "When changing files or running commands, say what you did. "
    "When the user asks you to remember something durable, call remember(fact). "
    "Use memory_append for daily notes. Recall with memory_search / memory_get. "
    "For current info (weather, news, docs), call web_search (DuckDuckGo).\n\n"
    "Automations (OpenClaw cron): use the `automations` tool for any scheduled work — "
    "one-shots, intervals, or cron expressions — with a `job` object containing "
    "`schedule` + `payload`. Do not use shell sleep/OS crontab as a timer; the "
    "Gateway CronService runs due jobs."
)


def _supports_web_search(model: str) -> bool:
    """Azure Chat Completions rejects native WebSearch; Responses may still lack it."""
    m = model.strip().lower()
    if m.startswith("azure:") or m.startswith("azure-responses:"):
        return False
    return True


def _register_memory(agent: Agent[ClawDeps, str]) -> None:
    @agent.instructions
    def project_context(ctx: RunContext[ClawDeps]) -> str:
        from claw.routing import DeliveryContext, delivery_prompt_block

        ensure_workspace(ctx.deps.settings)
        bootstrap = load_bootstrap_context(
            ctx.deps.settings.agent_home,
            ctx.deps.session_key,
        )
        delivery = ctx.deps.delivery
        if delivery is not None and not isinstance(delivery, DeliveryContext):
            delivery = None
        delivery_block = delivery_prompt_block(
            delivery, session_key=ctx.deps.session_key
        )
        if bootstrap:
            return f"{bootstrap}\n\n{delivery_block}"
        return delivery_block

    @agent.tool
    def memory_get(
        ctx: RunContext[ClawDeps],
        path: str = "MEMORY.md",
        from_line: int | None = None,
        to_line: int | None = None,
    ) -> str:
        """Read MEMORY.md or a memory/<file>.md note. Use path='daily' for today."""
        store = MemoryStore(ctx.deps.settings.agent_home)
        return store.get(path, from_line=from_line, to_line=to_line)

    @agent.tool
    def memory_search(ctx: RunContext[ClawDeps], query: str) -> str:
        """Keyword search across MEMORY.md and memory/*.md."""
        store = MemoryStore(ctx.deps.settings.agent_home)
        return store.search(query)

    @agent.tool
    def remember(ctx: RunContext[ClawDeps], fact: str) -> str:
        """Save a durable fact to long-term MEMORY.md. Prefer this when the user says remember."""
        store = MemoryStore(ctx.deps.settings.agent_home)
        return store.append(fact, path="MEMORY.md")

    @agent.tool
    def memory_append(
        ctx: RunContext[ClawDeps],
        text: str,
        path: str = "daily",
    ) -> str:
        """Append a note. path='MEMORY.md' for long-term, 'daily' (default) for today's log."""
        store = MemoryStore(ctx.deps.settings.agent_home)
        return store.append(text, path=path)


def build_agent(settings: Settings | None = None) -> Agent[ClawDeps, str]:
    """Create a Pydantic AI agent with OpenClaw-style harness + memory."""
    settings = settings or get_settings()
    ensure_workspace(settings)

    if settings.is_test_model:
        agent: Agent[ClawDeps, str] = Agent(
            "test",
            instructions=INSTRUCTIONS,
            deps_type=ClawDeps,
        )
        _register_memory(agent)
        register_automations_tool(agent)
        # Skip live DuckDuckGo: TestModel invokes every tool and would hit the network.
        return agent

    capabilities: list[object] = [
        LocalWorkspace(str(settings.workspace)),
        Coder(),
    ]
    # Native provider web search when supported; DuckDuckGo tool always registered below.
    if _supports_web_search(settings.model):
        capabilities.append(WebSearch())

    agent = Agent(
        settings.model,
        instructions=INSTRUCTIONS,
        deps_type=ClawDeps,
        capabilities=capabilities,  # type: ignore[arg-type]
    )
    _register_memory(agent)
    register_automations_tool(agent)
    register_web_search_tool(agent)
    return agent


@lru_cache(maxsize=1)
def get_agent() -> Agent[ClawDeps, str]:
    return build_agent(get_settings())


def reset_agent_cache() -> None:
    get_agent.cache_clear()
