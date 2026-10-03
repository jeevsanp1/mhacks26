"""Disk-backed chat session store."""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from claw.config import Settings


# Allow OpenClaw-style colons in logical session keys (agent:main:main).
_SAFE_KEY = re.compile(r"[^a-zA-Z0-9._@+:-]+")


def sanitize_session_key(session_key: str) -> str:
    key = (session_key or "main").strip() or "main"
    key = _SAFE_KEY.sub("_", key)
    return key[:200]


def session_filename(session_key: str) -> str:
    """Encode logical session key for the filesystem (':' → '=')."""
    return sanitize_session_key(session_key).replace(":", "=") + ".json"


def new_session_key(agent_id: str = "main") -> str:
    """Fresh private UI chat under the agent namespace."""
    from claw.routing import normalize_agent_id

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"agent:{normalize_agent_id(agent_id)}:chat-{stamp}-{uuid.uuid4().hex[:6]}"


class SessionStore:
    def __init__(self, settings: Settings) -> None:
        self._dir = settings.sessions_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_path = self._dir / ".last_session"

    def path_for(self, session_key: str) -> Path:
        return self._dir / session_filename(session_key)

    def remember_active(self, session_key: str) -> None:
        """Persist the session the UI should reopen next time."""
        key = sanitize_session_key(session_key)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._last_path.write_text(key + "\n", encoding="utf-8")

    def last_active_key(self) -> str | None:
        if not self._last_path.exists():
            return None
        key = self._last_path.read_text(encoding="utf-8").strip()
        return sanitize_session_key(key) if key else None

    def most_recent_key(self) -> str | None:
        """Session with the newest transcript mtime (excludes pointer files)."""
        if not self._dir.is_dir():
            return None
        newest: Path | None = None
        newest_mtime = -1.0
        for path in self._dir.glob("*.json"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime >= newest_mtime:
                newest_mtime = mtime
                newest = path
        if newest is None:
            return None
        # Reverse session_filename encoding: '=' → ':'
        stem = newest.stem.replace("=", ":")
        return sanitize_session_key(stem)

    def resolve_startup_key(
        self,
        session_key: str | None = None,
        *,
        new_chat: bool = False,
        agent_id: str = "main",
    ) -> str:
        """Pick which session to open: explicit, /new, last active, or newest file."""
        if new_chat:
            key = new_session_key(agent_id)
            self.remember_active(key)
            return key
        if session_key:
            key = sanitize_session_key(session_key)
            self.remember_active(key)
            return key
        key = self.last_active_key() or self.most_recent_key() or "main"
        return sanitize_session_key(key)

    def lock_for(self, session_key: str) -> asyncio.Lock:
        key = sanitize_session_key(session_key)
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def load(self, session_key: str) -> list[ModelMessage]:
        path = self.path_for(session_key)
        if not path.exists():
            return []
        raw = path.read_bytes()
        if not raw.strip():
            return []
        return ModelMessagesTypeAdapter.validate_json(raw)

    def save(self, session_key: str, messages: list[ModelMessage]) -> None:
        path = self.path_for(session_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = ModelMessagesTypeAdapter.dump_json(messages, indent=2)
        path.write_bytes(data)

    def append(self, session_key: str, new_messages: list[ModelMessage]) -> list[ModelMessage]:
        history = self.load(session_key)
        history.extend(new_messages)
        self.save(session_key, history)
        return history

    def clear(self, session_key: str) -> bool:
        """Delete one session transcript. Returns True if a file was removed."""
        path = self.path_for(session_key)
        if path.exists():
            path.unlink()
            return True
        return False

    def clear_all(self) -> int:
        """Delete every session transcript. Returns count of files removed."""
        n = 0
        if not self._dir.is_dir():
            return 0
        for path in self._dir.glob("*.json"):
            path.unlink()
            n += 1
        if self._last_path.exists():
            self._last_path.unlink()
        return n

    def metadata(self, session_key: str) -> dict:
        path = self.path_for(session_key)
        return {
            "sessionKey": sanitize_session_key(session_key),
            "path": str(path),
            "exists": path.exists(),
            "messageCount": len(self.load(session_key)) if path.exists() else 0,
        }


def dump_messages_json(messages: list[ModelMessage]) -> bytes:
    return ModelMessagesTypeAdapter.dump_json(messages)
