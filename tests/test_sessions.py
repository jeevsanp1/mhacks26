from __future__ import annotations

import time

from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)

from claw.config import Settings
from claw.sessions import SessionStore, session_filename, strip_screenshots


def test_resolve_startup_prefers_last_active(settings: Settings) -> None:
    store = SessionStore(settings)
    store.save("main", [])
    store.save("agent:main:chat-old", [])
    store.remember_active("agent:main:chat-new")
    assert (
        store.resolve_startup_key(None, new_chat=False)
        == "agent:main:chat-new"
    )


def test_resolve_startup_falls_back_to_most_recent_mtime(settings: Settings) -> None:
    store = SessionStore(settings)
    store.save("main", [])
    time.sleep(0.02)
    store.save("agent:main:chat-newer", [])
    # No .last_session pointer
    if store._last_path.exists():
        store._last_path.unlink()
    key = store.resolve_startup_key(None, new_chat=False)
    assert key == "agent:main:chat-newer"


def test_new_chat_remembers_active(settings: Settings) -> None:
    store = SessionStore(settings)
    key = store.resolve_startup_key(None, new_chat=True, agent_id="main")
    assert key.startswith("agent:main:chat-")
    assert store.last_active_key() == key


def test_session_filename_roundtrip(settings: Settings) -> None:
    key = "agent:main:chat-20261003-120000-abc"
    path = SessionStore(settings).path_for(key)
    assert path.name == session_filename(key)
    assert path.stem.replace("=", ":") == key


def test_strip_screenshots_drops_image_parts() -> None:
    png = BinaryContent(data=b"\x89PNG" + b"x" * 200, media_type="image/png")
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="computer",
                    content="screenshot=1280x720 (screen=1512x982, monitor=1).",
                    tool_call_id="c1",
                ),
                UserPromptPart(content=[png]),
            ]
        ),
        ModelResponse(parts=[TextPart(content="done")]),
        ModelRequest(parts=[UserPromptPart(content="hello")]),
    ]
    cleaned = strip_screenshots(messages)
    assert len(cleaned) == 3
    req0 = cleaned[0]
    assert isinstance(req0, ModelRequest)
    assert len(req0.parts) == 1
    assert isinstance(req0.parts[0], ToolReturnPart)
    assert "screenshot=1280x720" in str(req0.parts[0].content)
    assert isinstance(cleaned[1], ModelResponse)
    assert isinstance(cleaned[2], ModelRequest)
    assert cleaned[2].parts[0].content == "hello"


def test_append_strips_screenshots_from_disk(settings: Settings) -> None:
    store = SessionStore(settings)
    png = BinaryContent(data=b"\x89PNG" + b"y" * 5000, media_type="image/png")
    store.append(
        "shot-session",
        [
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="computer",
                        content="clicked ok",
                        tool_call_id="c2",
                    ),
                    UserPromptPart(content=[png]),
                ]
            )
        ],
    )
    raw = store.path_for("shot-session").read_text(encoding="utf-8")
    assert "iVBORw" not in raw and "\\u0089PNG" not in raw
    assert "y" * 100 not in raw
    loaded = store.load("shot-session")
    assert len(loaded) == 1
    assert isinstance(loaded[0].parts[0], ToolReturnPart)
