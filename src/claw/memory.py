"""OpenClaw-style workspace memory: bootstrap files + MEMORY.md + daily notes."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from claw.config import Settings

# Injected into the system prompt (budgeted). MEMORY.md only for private sessions.
BOOTSTRAP_ALWAYS = ("AGENTS.md", "SOUL.md", "IDENTITY.md", "USER.md")
MEMORY_LONG_TERM = "MEMORY.md"
MEMORY_DIR = "memory"

DEFAULT_BOOTSTRAP_MAX_CHARS = 8_000
DEFAULT_BOOTSTRAP_TOTAL_MAX_CHARS = 24_000

_SAFE_REL = re.compile(r"^(MEMORY\.md|memory/[A-Za-z0-9._@+-]+\.md)$")

DEFAULT_AGENTS = """# AGENTS.md — operating instructions

You are Claw, a local operator assistant.

## Memory

- Durable facts belong in `MEMORY.md` (curated, short).
- Running notes belong in `memory/YYYY-MM-DD.md` (daily log).
- Use `memory_search` / `memory_get` to recall.
- When the user says "remember …", call `remember(fact)` immediately (writes MEMORY.md).
- Use `memory_append` for daily running notes.
- Do not rely on chat history alone for long-lived preferences.

## Automations (OpenClaw cron)

- Use the `automations` tool for any scheduled work (not just reminders).
- Prefer `action="add"` with a nested `job` containing `schedule` + `payload`:
  - schedule.kind: `at` | `every` | `cron`
  - payload.kind: `agentTurn` | `systemEvent`
- Jobs are stored under `.claw/cron/jobs/`; Gateway CronService executes them.
- Never use shell `sleep` / OS crontab as a timer.
"""

DEFAULT_SOUL = """# SOUL.md — persona

Be concise, helpful, and direct. Prefer action over ceremony.
"""

DEFAULT_IDENTITY = """# IDENTITY.md

Name: Claw
Vibe: practical local operator
"""

DEFAULT_USER = """# USER.md — user profile

(Fill in preferences and how you like to be addressed.)
"""

DEFAULT_MEMORY = """# MEMORY.md — long-term memory

Curated durable facts and decisions. Keep this short; put detail in `memory/`.
"""


@dataclass(frozen=True)
class ClawDeps:
    """Per-run deps so bootstrap/memory can see the active session."""

    settings: Settings
    session_key: str = "main"
    delivery: object | None = None  # claw.routing.DeliveryContext | None


def ensure_workspace(settings: Settings) -> Path:
    """Create agent home + default bootstrap/memory files if missing."""
    root = settings.agent_home
    root.mkdir(parents=True, exist_ok=True)
    (root / MEMORY_DIR).mkdir(parents=True, exist_ok=True)

    defaults = {
        "AGENTS.md": DEFAULT_AGENTS,
        "SOUL.md": DEFAULT_SOUL,
        "IDENTITY.md": DEFAULT_IDENTITY,
        "USER.md": DEFAULT_USER,
        MEMORY_LONG_TERM: DEFAULT_MEMORY,
    }
    for name, content in defaults.items():
        path = root / name
        if not path.exists():
            path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return root


def reset_from_scratch(settings: Settings) -> dict[str, int]:
    """Wipe chats, cron jobs, and agent home; re-seed default bootstrap files.

    Does not touch the coding `CLAW_WORKSPACE` (repo files). Only `.claw` agent
    state: sessions, cron jobs, MEMORY.md / persona files.
    """
    from claw.sessions import SessionStore

    n_sessions = SessionStore(settings).clear_all()
    n_jobs = 0
    for jobs_dir in (settings.state_dir / "cron" / "jobs", settings.state_dir / "jobs"):
        if jobs_dir.is_dir():
            for path in jobs_dir.glob("*.json"):
                path.unlink()
                n_jobs += 1
    home = settings.agent_home
    had_home = 1 if home.exists() else 0
    if home.exists():
        shutil.rmtree(home)
    ensure_workspace(settings)
    return {"sessions": n_sessions, "jobs": n_jobs, "workspace": had_home}


def is_private_session(session_key: str) -> bool:
    """MEMORY.md is for private sessions (not group/channel/cron lanes)."""
    from claw.routing import is_private_session_key

    return is_private_session_key(session_key)


def today_memory_path(workspace: Path, day: date | None = None) -> Path:
    d = day or date.today()
    return workspace / MEMORY_DIR / f"{d.isoformat()}.md"


def resolve_memory_path(workspace: Path, path: str) -> Path:
    """Resolve a memory-relative path; reject anything outside MEMORY.md / memory/."""
    rel = path.strip().lstrip("./")
    if rel in {"", "memory", "memory/"}:
        rel = MEMORY_LONG_TERM
    if rel == "daily":
        return today_memory_path(workspace)
    if not _SAFE_REL.match(rel):
        raise ValueError(
            "path must be MEMORY.md or memory/<name>.md "
            "(or 'daily' for today's note)"
        )
    resolved = (workspace / rel).resolve()
    workspace_resolved = workspace.resolve()
    if not str(resolved).startswith(str(workspace_resolved)):
        raise ValueError("path escapes workspace")
    return resolved


def _truncate(text: str, limit: int, *, label: str) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n…[truncated {label}: {len(text) - limit} chars omitted]"


def load_bootstrap_context(
    workspace: Path,
    session_key: str = "main",
    *,
    max_chars: int = DEFAULT_BOOTSTRAP_MAX_CHARS,
    total_max_chars: int = DEFAULT_BOOTSTRAP_TOTAL_MAX_CHARS,
) -> str:
    """Build the Project Context block injected into instructions each turn."""
    parts: list[str] = []
    used = 0

    def add(name: str, required_marker: bool) -> None:
        nonlocal used
        path = workspace / name
        remaining = total_max_chars - used
        if remaining <= 0:
            return
        if not path.is_file():
            if required_marker:
                block = f"## {name}\n\n(missing file)"
                parts.append(block)
                used += len(block)
            return
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return
        body = _truncate(raw, min(max_chars, remaining), label=name)
        block = f"## {name}\n\n{body}"
        parts.append(block)
        used += len(block)

    for name in BOOTSTRAP_ALWAYS:
        # USER.md is optional; others get a missing marker if absent.
        add(name, required_marker=name != "USER.md")

    if is_private_session(session_key):
        add(MEMORY_LONG_TERM, required_marker=False)

    if not parts:
        return ""
    return "# Project Context\n\n" + "\n\n".join(parts)


class MemoryStore:
    """Read/search/append workspace memory files."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        (self.workspace / MEMORY_DIR).mkdir(parents=True, exist_ok=True)

    def get(
        self,
        path: str = MEMORY_LONG_TERM,
        *,
        from_line: int | None = None,
        to_line: int | None = None,
    ) -> str:
        file_path = resolve_memory_path(self.workspace, path)
        if not file_path.is_file():
            return f"(no memory file at {path})"
        text = file_path.read_text(encoding="utf-8")
        if from_line is None and to_line is None:
            return text
        lines = text.splitlines()
        start = max((from_line or 1) - 1, 0)
        end = to_line if to_line is not None else len(lines)
        end = max(end, start)
        excerpt = "\n".join(lines[start:end])
        return excerpt if excerpt else "(empty range)"

    def append(self, text: str, *, path: str = "daily") -> str:
        note = text.strip()
        if not note:
            raise ValueError("text must be non-empty")
        file_path = resolve_memory_path(self.workspace, path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        prefix = ""
        if file_path.exists() and file_path.stat().st_size > 0:
            existing = file_path.read_text(encoding="utf-8")
            if not existing.endswith("\n"):
                prefix = "\n"
        else:
            if file_path.name == MEMORY_LONG_TERM:
                prefix = DEFAULT_MEMORY.rstrip() + "\n\n"
            else:
                prefix = f"# {file_path.stem}\n\n"
        with file_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{prefix}- {note}\n")
        rel = file_path.relative_to(self.workspace.resolve())
        return f"appended to {rel}"

    def search(self, query: str, *, limit: int = 8) -> str:
        q = query.strip()
        if not q:
            raise ValueError("query must be non-empty")
        tokens = [t.lower() for t in re.split(r"\s+", q) if t]
        hits: list[tuple[int, str, str]] = []

        candidates: list[Path] = []
        long_term = self.workspace / MEMORY_LONG_TERM
        if long_term.is_file():
            candidates.append(long_term)
        mem_dir = self.workspace / MEMORY_DIR
        if mem_dir.is_dir():
            candidates.extend(sorted(mem_dir.glob("*.md"), reverse=True))

        for file_path in candidates:
            try:
                text = file_path.read_text(encoding="utf-8")
            except OSError:
                continue
            lower = text.lower()
            score = sum(lower.count(tok) for tok in tokens)
            if score <= 0:
                continue
            rel = str(file_path.relative_to(self.workspace.resolve()))
            # Prefer the most relevant lines
            best_lines: list[str] = []
            for line in text.splitlines():
                ll = line.lower()
                if any(tok in ll for tok in tokens):
                    best_lines.append(line.strip())
                if len(best_lines) >= 3:
                    break
            snippet = " | ".join(best_lines) if best_lines else text[:200].replace("\n", " ")
            hits.append((score, rel, snippet))

        hits.sort(key=lambda h: (-h[0], h[1]))
        if not hits:
            return f"No memory hits for {query!r}."
        lines = [f"- {rel} (score={score}): {snippet}" for score, rel, snippet in hits[:limit]]
        return "Memory search results:\n" + "\n".join(lines)
