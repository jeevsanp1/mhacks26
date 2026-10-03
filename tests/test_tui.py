from __future__ import annotations

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from textual.widgets import Collapsible, Markdown

from claw.config import Settings
from claw.events import EventSequencer
from claw.tui import (
    ChatApp,
    bubble_markdown,
    conversation_plain,
    format_tool_body,
    format_tool_group_body,
    group_tool_title,
    history_to_chat_lines,
    last_assistant_plain,
    tool_title,
)


def test_history_to_chat_lines_skips_empty() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart(content="hello")]),
        ModelResponse(parts=[TextPart(content="world")]),
        ModelRequest(parts=[UserPromptPart(content="   ")]),
    ]
    assert history_to_chat_lines(messages) == [
        ("user", "hello"),
        ("assistant", "world"),
    ]


def test_history_keeps_tool_between_assistant_segments() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart(content="weather?")]),
        ModelResponse(
            parts=[
                TextPart(content="Let me look."),
                ToolCallPart(
                    tool_name="web_search",
                    args={"query": "ann arbor weather"},
                    tool_call_id="c1",
                ),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="web_search",
                    content="Sunny 70F",
                    tool_call_id="c1",
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="It's sunny and 70F.")]),
    ]
    lines = history_to_chat_lines(messages)
    assert lines[0] == ("user", "weather?")
    assert lines[1] == ("assistant", "Let me look.")
    assert lines[2][0] == "tool"
    assert "web_search" in lines[2][1]
    assert "ann arbor weather" in lines[2][1]
    assert lines[3][0] == "tool"
    assert "Sunny 70F" in lines[3][1]
    assert lines[4] == ("assistant", "It's sunny and 70F.")


def test_format_tool_body_and_title() -> None:
    assert "⚙ web_search" == tool_title("web_search")
    assert "✓ web_search" == tool_title("web_search", done=True)
    body = format_tool_body(args={"q": "hi"}, result="ok")
    assert "args" in body and "hi" in body
    assert "result" in body and "ok" in body
    assert group_tool_title(["read_file", "read_file"]) == "⚙ read_file ×2"
    assert group_tool_title(["read_file", "read_file"], done=True) == "✓ read_file ×2"
    assert "── read_file ──" in format_tool_group_body(
        [("read_file", {"path": "a"}, "ok")]
    )


def test_bubble_markdown_preserves_emphasis() -> None:
    source = bubble_markdown("assistant", "say **bold** and *italic*")
    assert source.startswith("**Claw**")
    assert "**bold**" in source
    assert "*italic*" in source


@pytest.mark.asyncio
async def test_tui_compose_and_send(settings: Settings) -> None:
    app = ChatApp(settings=settings, session_key="tui")
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt")
        assert prompt is not None
        await pilot.click("#prompt")
        await pilot.press(*"hi")
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        sources = [w.source for w in app.query("#log Markdown")]
        text = "\n".join(sources)
        assert "**You**" in text
        assert "hi" in text
        assert "**Claw**" in text


@pytest.mark.asyncio
async def test_tui_tool_events_are_collapsible_and_ordered(settings: Settings) -> None:
    """Assistant text after a tool call must mount below the tool card."""
    app = ChatApp(settings=settings, session_key="tools")
    async with app.run_test() as pilot:
        log = app.query_one("#log")
        log.remove_children()
        app._reset_turn_widgets()
        seq = EventSequencer(run_id="r1", session_key="tools")

        await app._apply_event(seq.assistant_delta("Before tools. "))
        await app._apply_event(
            seq.tool(
                phase="call",
                tool_name="web_search",
                tool_call_id="t1",
                args={"query": "weather"},
            )
        )
        await app._apply_event(
            seq.tool(
                phase="result",
                tool_name="web_search",
                tool_call_id="t1",
                result="Sunny",
            )
        )
        await app._apply_event(seq.assistant_delta("After tools."))
        await app._finalize_assistant_stream()
        await pilot.pause()

        children = list(log.children)
        md_idxs = [i for i, c in enumerate(children) if isinstance(c, Markdown)]
        tool_idxs = [i for i, c in enumerate(children) if isinstance(c, Collapsible)]
        assert len(md_idxs) >= 2
        assert len(tool_idxs) == 1
        assert md_idxs[0] < tool_idxs[0] < md_idxs[1]

        card = children[tool_idxs[0]]
        assert isinstance(card, Collapsible)
        assert card.collapsed is True
        assert "web_search" in card.title
        assert "✓" in card.title
        body_text = str(app._tool_groups[0]["body"].content)
        assert "weather" in body_text
        assert "Sunny" in body_text
        sources = [w.source for w in app.query("#log Markdown")]
        assert any("Before tools." in s for s in sources)
        assert any("After tools." in s for s in sources)


@pytest.mark.asyncio
async def test_tui_consecutive_tools_share_one_dropdown(settings: Settings) -> None:
    app = ChatApp(settings=settings, session_key="batch")
    async with app.run_test() as pilot:
        log = app.query_one("#log")
        log.remove_children()
        app._reset_turn_widgets()
        seq = EventSequencer(run_id="r2", session_key="batch")

        for i, path in enumerate(("a.py", "b.py", "c.py")):
            await app._apply_event(
                seq.tool(
                    phase="call",
                    tool_name="read_file",
                    tool_call_id=f"r{i}",
                    args={"path": path},
                )
            )
            await app._apply_event(
                seq.tool(
                    phase="result",
                    tool_name="read_file",
                    tool_call_id=f"r{i}",
                    result=f"contents {path}",
                )
            )
        await app._apply_event(seq.assistant_delta("Done reading."))
        await app._finalize_assistant_stream()
        await pilot.pause()

        tools = [c for c in log.children if isinstance(c, Collapsible)]
        assert len(tools) == 1
        assert "read_file ×3" in tools[0].title
        assert "✓" in tools[0].title
        body = str(app._tool_groups[0]["body"].content)
        assert "a.py" in body and "b.py" in body and "c.py" in body


@pytest.mark.asyncio
async def test_tui_markdown_renders_strong_and_em(settings: Settings) -> None:
    app = ChatApp(settings=settings, session_key="md")
    async with app.run_test() as pilot:
        log = app.query_one("#log")
        widget = Markdown("**bold** and *italic*")
        await log.mount(widget)
        await pilot.pause()
        spans = widget.source
        assert "**bold**" in spans
        styles = set()
        for block in widget.query("MarkdownBlock"):
            content = getattr(block, "_content", None)
            if content is None:
                continue
            for span in getattr(content, "spans", ()):
                style = getattr(span, "style", None)
                if style is not None:
                    styles.add(str(style))
        joined = " ".join(styles).lower()
        assert ".strong" in joined
        assert ".em" in joined


def test_conversation_plain_and_last_reply() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart(content="hello")]),
        ModelResponse(parts=[TextPart(content="world")]),
    ]
    assert conversation_plain(messages) == "You:\nhello\n\nClaw:\nworld"
    assert last_assistant_plain(messages) == "world"
    assert last_assistant_plain(messages, live_buf="streaming") == "streaming"
    assert last_assistant_plain([]) == ""


@pytest.mark.asyncio
async def test_tui_copy_last_and_all(settings: Settings) -> None:
    app = ChatApp(settings=settings, session_key="copy")
    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press(*"hi")
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        app.action_copy_last()
        assert app.clipboard.strip()
        app.action_copy_all()
        assert "You:" in app.clipboard
        assert "hi" in app.clipboard
        assert "Claw:" in app.clipboard


@pytest.mark.asyncio
async def test_tui_slash_new_starts_fresh_session(settings: Settings) -> None:
    app = ChatApp(settings=settings, session_key="keep-me")
    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press(*"hi")
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        old_key = app.session_key
        await pilot.click("#prompt")
        await pilot.press(*"/new")
        await pilot.press("enter")
        await pilot.pause()
        assert app.session_key != old_key
        assert app.session_key.startswith("agent:main:chat-")
        from claw.sessions import SessionStore

        assert SessionStore(settings).last_active_key() == app.session_key
        sources = "\n".join(w.source for w in app.query("#log Markdown"))
        assert "hi" not in sources


@pytest.mark.asyncio
async def test_tui_scratch_yes_wipes_memory(settings: Settings) -> None:
    from claw.memory import ensure_workspace

    ensure_workspace(settings)
    (settings.agent_home / "MEMORY.md").write_text(
        "# MEMORY.md\n\n- wipe me\n", encoding="utf-8"
    )
    app = ChatApp(settings=settings, session_key="doomed")
    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press(*"/scratch")
        await pilot.press("enter")
        await pilot.pause()
        assert "wipe me" in (settings.agent_home / "MEMORY.md").read_text()
        await pilot.click("#prompt")
        for key in "/scratch yes":
            await pilot.press(key)
        await pilot.press("enter")
        await pilot.pause()
        mem = (settings.agent_home / "MEMORY.md").read_text(encoding="utf-8")
        assert "wipe me" not in mem
        assert app.session_key == "main"

