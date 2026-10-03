from __future__ import annotations

import time

from claw.config import Settings
from claw.sessions import SessionStore, session_filename


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
