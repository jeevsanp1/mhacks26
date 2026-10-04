"""Interactive terminal chat UI for the embedded agent."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections import Counter
from typing import Any, Literal

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Footer, Header, Input, Markdown, Static
from textual.widgets.markdown import MarkdownStream

from claw.config import Settings, get_settings
from claw.events import AgentEvent
from claw.jobs import scheduled_prompt
from claw.spacetime_jobs import get_job_store
from claw.memory import reset_from_scratch
from claw.queue import normalize_queue_mode
from claw.runner import AgentRunner
from claw.sessions import SessionStore, new_session_key, sanitize_session_key

Role = Literal["user", "assistant", "meta", "tool"]


def escape_rich_markup(text: str) -> str:
    """Prevent Rich from treating paths like ``[/Users/…/file | N lines]`` as tags."""
    # Closing tags are ``[/name]``; absolute paths in tool output trip this.
    return text.replace("[/", "\\[/")


def plain_static(content: str = "", *, classes: str | None = None) -> Static:
    """Static that does not parse Rich markup (safe for tool output / paths)."""
    return Static(content, classes=classes, markup=False)


def _pretty_value(value: Any, *, limit: int = 1200) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, indent=2, default=str)
        except TypeError:
            text = repr(value)
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def format_tool_body(*, args: Any = None, result: Any = None) -> str:
    """Plain detail text shown inside a collapsed tool card."""
    sections: list[str] = []
    args_text = _pretty_value(args)
    if args_text:
        sections.append(f"args\n{args_text}")
    result_text = _pretty_value(result)
    if result_text:
        sections.append(f"result\n{result_text}")
    return "\n\n".join(sections) if sections else "(no details)"


def tool_title(name: str, *, done: bool = False) -> str:
    mark = "✓" if done else "⚙"
    return f"{mark} {name}"


def group_tool_title(names: list[str], *, done: bool = False) -> str:
    """One dropdown label for a run of consecutive tool calls."""
    mark = "✓" if done else "⚙"
    if not names:
        return f"{mark} tools"
    counts = Counter(names)
    if len(counts) == 1:
        name, n = next(iter(counts.items()))
        label = f"{name} ×{n}" if n > 1 else name
    elif len(names) <= 3:
        label = ", ".join(names)
    else:
        label = f"{len(names)} tools"
    return f"{mark} {label}"


def format_tool_group_body(
    entries: list[tuple[str, Any, Any]],
) -> str:
    """Join several tool call details under one collapsible body.

    Each entry is ``(name, args, result)``.
    """
    if not entries:
        return "(no details)"
    blocks: list[str] = []
    for name, args, result in entries:
        header = f"── {name} ──"
        detail = format_tool_body(args=args, result=result)
        blocks.append(f"{header}\n{detail}")
    return "\n\n".join(blocks)


def history_to_chat_lines(messages: list[ModelMessage]) -> list[tuple[Role, str]]:
    """Flatten persisted Pydantic AI messages into displayable chat turns.

    Assistant text and tool calls stay in part order so tools appear between
    text segments the way they streamed live.
    """
    lines: list[tuple[Role, str]] = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    content = part.content
                    if isinstance(content, str) and content.strip():
                        lines.append(("user", content))
                elif isinstance(part, ToolReturnPart):
                    name = part.tool_name or "tool"
                    body = format_tool_body(result=part.content)
                    lines.append(("tool", f"{tool_title(name, done=True)}\n{body}"))
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart) and part.content:
                    lines.append(("assistant", part.content))
                elif isinstance(part, ToolCallPart):
                    name = part.tool_name or "tool"
                    body = format_tool_body(args=part.args)
                    lines.append(("tool", f"{tool_title(name)}\n{body}"))
    return lines


def bubble_markdown(role: Role, text: str, *, headed: bool = True) -> str:
    """Markdown source for a chat bubble, including GFM emphasis."""
    safe = escape_rich_markup(text)
    if role == "user":
        return f"**You**\n\n{safe}"
    if role == "assistant":
        if headed:
            return f"**EWOK**\n\n{safe}"
        return safe
    return safe


def conversation_plain(messages: list[ModelMessage]) -> str:
    """Plain-text transcript suitable for pasting elsewhere."""
    chunks: list[str] = []
    for role, text in history_to_chat_lines(messages):
        if role == "user":
            chunks.append(f"You:\n{text}")
        elif role == "assistant":
            chunks.append(f"EWOK:\n{text}")
        elif role == "tool":
            chunks.append(text)
    return "\n\n".join(chunks)


def last_assistant_plain(messages: list[ModelMessage], live_buf: str = "") -> str:
    if live_buf.strip():
        return live_buf
    for role, text in reversed(history_to_chat_lines(messages)):
        if role == "assistant" and text.strip():
            return text
    return ""


def copy_to_system_clipboard(text: str) -> None:
    """Best-effort OS clipboard write. Skipped under pytest."""
    if os.getenv("PYTEST_CURRENT_TEST"):
        return
    payload = text.encode("utf-8")
    if shutil.which("pbcopy"):
        subprocess.run(["pbcopy"], input=payload, check=False, timeout=2)
        return
    if shutil.which("wl-copy"):
        subprocess.run(["wl-copy"], input=payload, check=False, timeout=2)
        return
    if shutil.which("xclip"):
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=payload,
            check=False,
            timeout=2,
        )


class ChatLog(VerticalScroll):
    can_focus = True
    FOCUS_ON_CLICK = False


class ChatApp(App[None]):
    TITLE = "EWOK"
    CSS = """
    Screen {
        background: #0b1220;
        color: #e5eefc;
    }
    Header {
        background: #111827;
        color: #93c5fd;
    }
    Footer {
        background: #111827;
    }
    #log {
        height: 1fr;
        padding: 1 2;
    }
    Markdown {
        margin-bottom: 1;
        height: auto;
        background: transparent;
    }
    Markdown.user {
        color: #7dd3fc;
        border-left: tall #38bdf8;
        padding-left: 1;
    }
    Markdown.assistant {
        color: #e2e8f0;
        border-left: tall #64748b;
        padding-left: 1;
    }
    MarkdownBlock .em {
        text-style: italic;
    }
    MarkdownBlock .strong {
        text-style: bold;
    }
    Collapsible.tool {
        margin: 0 0 1 0;
        background: #0f172a;
        border-left: tall #475569;
        padding: 0 0 0 1;
        height: auto;
        color: #94a3b8;
    }
    Collapsible.tool > CollapsibleTitle {
        color: #cbd5e1;
        text-style: none;
    }
    .tool-body {
        color: #94a3b8;
        padding: 0 1 1 0;
        height: auto;
    }
    .meta {
        color: #64748b;
        text-style: italic;
        margin-bottom: 1;
    }
    #prompt {
        dock: bottom;
        background: #111827;
        border: tall #1f2937;
        margin: 0 1 1 1;
    }
    """
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+n", "new_chat", "New chat"),
        Binding("ctrl+shift+c", "copy_last", "Copy last"),
        Binding("ctrl+shift+y", "copy_all", "Copy all"),
        Binding("ctrl+l", "clear_log", "Clear view"),
        Binding("escape", "focus_prompt", "Focus input", show=False),
    ]

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        session_key: str = "main",
        runner: AgentRunner | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings or get_settings()
        self.session_key = sanitize_session_key(session_key)
        self.runner = runner or AgentRunner.create(self.settings)
        self.queue_mode = normalize_queue_mode(self.settings.queue_mode)
        self._busy = False
        self._assistant: Markdown | None = None
        self._stream: MarkdownStream | None = None
        self._assistant_buf = ""
        self._turn_has_assistant_header = False
        self._need_new_assistant = True
        # Consecutive tool calls share one Collapsible until assistant text breaks the streak.
        self._tool_groups: list[dict[str, Any]] = []
        self._active_tool_group: int | None = None
        self._tool_id_group: dict[str, int] = {}
        self._tool_names: dict[str, str] = {}
        self._tool_args: dict[str, Any] = {}
        self._tool_results: dict[str, Any] = {}
        self._tool_done: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield ChatLog(id="log")
        yield Input(
            placeholder="Enter send · /new · /queue · /scratch yes · Ctrl+N · Ctrl+Q",
            id="prompt",
        )
        yield Footer()

    def _subtitle(self) -> str:
        return (
            f"{self.settings.model} · session {self.session_key} · "
            f"queue={self.queue_mode}"
        )

    def _reset_turn_widgets(self) -> None:
        self._assistant = None
        self._stream = None
        self._assistant_buf = ""
        self._turn_has_assistant_header = False
        self._need_new_assistant = True
        self._tool_groups.clear()
        self._active_tool_group = None
        self._tool_id_group.clear()
        self._tool_names.clear()
        self._tool_args.clear()
        self._tool_results.clear()
        self._tool_done.clear()

    def _close_tool_group(self) -> None:
        """Stop appending to the current tool dropdown (keep it mounted)."""
        self._active_tool_group = None

    def _refresh_tool_group(self, group_idx: int) -> None:
        if group_idx < 0 or group_idx >= len(self._tool_groups):
            return
        group = self._tool_groups[group_idx]
        ids: list[str] = group["ids"]
        names = [self._tool_names[i] for i in ids]
        entries = [
            (
                self._tool_names[i],
                self._tool_args.get(i),
                self._tool_results.get(i),
            )
            for i in ids
        ]
        done = bool(ids) and all(i in self._tool_done for i in ids)
        group["body"].update(format_tool_group_body(entries))
        group["card"].title = group_tool_title(names, done=done)

    def on_mount(self) -> None:
        self.sub_title = self._subtitle()
        log = self.query_one("#log", ChatLog)
        history = self.runner.store.load(self.session_key)
        lines = history_to_chat_lines(history)
        if not lines:
            log.mount(
                plain_static(
                    "Talk to the local harness. /new fresh chat · "
                    "/queue steer|followup|collect|interrupt · "
                    "/scratch yes wipes memory. Click ▶ tool rows to expand.",
                    classes="meta",
                )
            )
        headed = True
        tool_batch: list[tuple[str, str]] = []

        def flush_tools() -> None:
            nonlocal tool_batch
            if not tool_batch:
                return
            names: list[str] = []
            bodies: list[str] = []
            for title, body in tool_batch:
                # title like "✓ read_file" — strip mark for grouping label
                name = title.split(maxsplit=1)[-1] if title.strip() else "tool"
                names.append(name)
                bodies.append(f"── {name} ──\n{body}" if body else f"── {name} ──")
            log.mount(
                Collapsible(
                    plain_static("\n\n".join(bodies) or "(no details)", classes="tool-body"),
                    title=group_tool_title(names, done=True),
                    collapsed=True,
                    classes="tool",
                )
            )
            tool_batch = []

        for role, text in lines:
            if role == "tool":
                title, _, body = text.partition("\n")
                tool_batch.append((title or "tool", body or "(no details)"))
                headed = False
            elif role == "assistant":
                flush_tools()
                log.mount(
                    Markdown(
                        bubble_markdown("assistant", text, headed=headed),
                        classes="assistant",
                    )
                )
                headed = False
            else:
                flush_tools()
                log.mount(Markdown(bubble_markdown(role, text), classes=role))
                headed = True
        flush_tools()
        log.scroll_end(animate=False)
        self.query_one("#prompt", Input).focus()
        self.set_interval(1.0, self._poll_due_jobs)

    async def _poll_due_jobs(self) -> None:
        if self._busy:
            return
        store = get_job_store(self.settings)
        due = store.due(session_key=self.session_key)
        if not due:
            return
        job = due[0]
        job.status = "running"
        store.save(job)
        self._busy = True  # claim before await so overlapping polls skip
        log = self.query_one("#log", ChatLog)
        await log.mount(
            plain_static(
                f"scheduled · {job.name} · firing now ({job.id[:8]}…)",
                classes="meta",
            )
        )
        await self._send(scheduled_prompt(job), scheduled_job_id=job.id)

    def action_clear_log(self) -> None:
        log = self.query_one("#log", ChatLog)
        log.remove_children()
        log.mount(plain_static("View cleared. Session history on disk is unchanged.", classes="meta"))

    def action_new_chat(self) -> None:
        if self._busy:
            self.notify("Wait for the current turn to finish", severity="warning", timeout=2)
            return
        self._switch_session(new_session_key(), "New chat. Memory on disk is unchanged.")

    def _switch_session(self, session_key: str, note: str) -> None:
        self.session_key = sanitize_session_key(session_key)
        SessionStore(self.settings).remember_active(self.session_key)
        self._reset_turn_widgets()
        log = self.query_one("#log", ChatLog)
        log.remove_children()
        log.mount(plain_static(note, classes="meta"))
        log.scroll_end(animate=False)
        self.sub_title = self._subtitle()
        self.notify(f"session {self.session_key}", timeout=2)

    def _reset_scratch(self) -> None:
        if self._busy:
            self.notify("Wait for the current turn to finish", severity="warning", timeout=2)
            return
        counts = reset_from_scratch(self.settings)
        self._switch_session(
            "main",
            "Scratch reset: chats, MEMORY.md, persona files, and cron jobs wiped. "
            f"Removed {counts['sessions']} session(s), {counts['jobs']} job(s). "
            "Agent workspace re-seeded with defaults.",
        )

    def copy_to_clipboard(self, text: str) -> None:
        super().copy_to_clipboard(text)
        copy_to_system_clipboard(text)

    def action_focus_prompt(self) -> None:
        self.query_one("#prompt", Input).focus()

    def action_copy_last(self) -> None:
        text = last_assistant_plain(
            self.runner.store.load(self.session_key),
            self._assistant_buf,
        )
        self._copy(text, "Copied last reply")

    def action_copy_all(self) -> None:
        text = conversation_plain(self.runner.store.load(self.session_key))
        self._copy(text, "Copied transcript")

    def _copy(self, text: str, ok_message: str) -> None:
        if not text.strip():
            self.notify("Nothing to copy", severity="warning", timeout=2)
            return
        self.copy_to_clipboard(text)
        self.notify(ok_message, timeout=2)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text or self._busy:
            return
        cmd = text.lower()
        if cmd in {"/new", "/chat new"}:
            self.action_new_chat()
            return
        if cmd == "/queue" or cmd.startswith("/queue "):
            log = self.query_one("#log", ChatLog)
            parts = text.split(maxsplit=1)
            if len(parts) == 1:
                await log.mount(
                    plain_static(
                        f"queue mode: {self.queue_mode} "
                        f"(steer|followup|collect|interrupt)",
                        classes="meta",
                    )
                )
                return
            try:
                self.queue_mode = normalize_queue_mode(parts[1])
            except ValueError as exc:
                await log.mount(plain_static(str(exc), classes="meta"))
                return
            self.sub_title = self._subtitle()
            await log.mount(
                plain_static(f"queue mode set to {self.queue_mode}", classes="meta")
            )
            return
        if cmd in {"/scratch", "/reset-all", "/reset"}:
            log = self.query_one("#log", ChatLog)
            await log.mount(
                plain_static(
                    "This wipes all chats, MEMORY.md, and the agent workspace. "
                    "Type `/scratch yes` to confirm.",
                    classes="meta",
                )
            )
            return
        if cmd in {"/scratch yes", "/reset-all yes", "/reset yes"}:
            self._reset_scratch()
            return
        await self._send(text)

    async def _finalize_assistant_stream(self) -> None:
        if self._stream is not None:
            await self._stream.stop()
            self._stream = None
        self._assistant = None
        self._need_new_assistant = True

    async def _ensure_assistant_stream(self) -> MarkdownStream:
        """Mount a new assistant bubble after tools so text stays in order."""
        if self._stream is not None and not self._need_new_assistant:
            return self._stream
        if self._stream is not None:
            await self._finalize_assistant_stream()
        # Assistant text breaks a tool streak — next tools get a new dropdown.
        self._close_tool_group()
        log = self.query_one("#log", ChatLog)
        self._assistant = Markdown("", classes="assistant")
        await log.mount(self._assistant)
        self._stream = Markdown.get_stream(self._assistant)
        if not self._turn_has_assistant_header:
            await self._stream.write("**EWOK**\n\n")
            self._turn_has_assistant_header = True
        self._need_new_assistant = False
        return self._stream

    async def _mount_tool_call(
        self,
        *,
        tool_call_id: str,
        name: str,
        args: Any = None,
    ) -> None:
        await self._finalize_assistant_stream()
        log = self.query_one("#log", ChatLog)
        self._tool_names[tool_call_id] = name
        self._tool_args[tool_call_id] = args
        self._tool_results.pop(tool_call_id, None)
        self._tool_done.discard(tool_call_id)

        if self._active_tool_group is None:
            body = plain_static("", classes="tool-body")
            card = Collapsible(
                body,
                title=group_tool_title([name], done=False),
                collapsed=True,
                classes="tool",
            )
            group = {"card": card, "body": body, "ids": [tool_call_id]}
            self._tool_groups.append(group)
            self._active_tool_group = len(self._tool_groups) - 1
            self._tool_id_group[tool_call_id] = self._active_tool_group
            await log.mount(card)
        else:
            idx = self._active_tool_group
            self._tool_groups[idx]["ids"].append(tool_call_id)
            self._tool_id_group[tool_call_id] = idx

        self._refresh_tool_group(self._active_tool_group)
        log.scroll_end(animate=False)

    async def _update_tool_result(
        self,
        *,
        tool_call_id: str,
        name: str,
        result: Any = None,
    ) -> None:
        log = self.query_one("#log", ChatLog)
        self._tool_names.setdefault(tool_call_id, name)
        self._tool_results[tool_call_id] = result
        self._tool_done.add(tool_call_id)

        idx = self._tool_id_group.get(tool_call_id)
        if idx is None:
            # Result without a prior call — open a one-shot group.
            await self._finalize_assistant_stream()
            body = plain_static("", classes="tool-body")
            card = Collapsible(
                body,
                title=group_tool_title([name], done=True),
                collapsed=True,
                classes="tool",
            )
            group = {"card": card, "body": body, "ids": [tool_call_id]}
            self._tool_groups.append(group)
            idx = len(self._tool_groups) - 1
            self._active_tool_group = idx
            self._tool_id_group[tool_call_id] = idx
            await log.mount(card)

        self._refresh_tool_group(idx)
        log.scroll_end(animate=False)

    async def _send(self, text: str, *, scheduled_job_id: str | None = None) -> None:
        log = self.query_one("#log", ChatLog)
        prompt = self.query_one("#prompt", Input)
        store = get_job_store(self.settings)
        if scheduled_job_id:
            # Don't dump the internal scheduled prompt as a user bubble
            await log.mount(
                plain_static("EWOK waking for scheduled turn…", classes="meta")
            )
        else:
            await log.mount(Markdown(bubble_markdown("user", text), classes="user"))
        SessionStore(self.settings).remember_active(self.session_key)
        self._reset_turn_widgets()
        log.scroll_end(animate=False)

        self._busy = True
        prompt.disabled = True
        self.sub_title = f"{self.settings.model} · running…"

        async def on_event(ev: AgentEvent) -> None:
            await self._apply_event(ev)

        try:
            snap = await self.runner.run_embedded(
                text,
                session_key=self.session_key,
                on_event=on_event,
                queue_mode=self.queue_mode,
            )
            if scheduled_job_id:
                job = store.get(scheduled_job_id)
                if job is not None:
                    job.status = "done" if snap.status == "ok" else "error"
                    job.output = snap.output
                    job.error = snap.error
                    store.save(job)
            if snap.status != "ok":
                await log.mount(
                    plain_static(f"{snap.status}: {snap.error or 'unknown error'}", classes="meta")
                )
            elif not self._assistant_buf and snap.output:
                stream = await self._ensure_assistant_stream()
                self._assistant_buf = snap.output
                await stream.write(snap.output)
        except Exception as exc:  # noqa: BLE001 — surface in the chat pane
            if scheduled_job_id:
                job = store.get(scheduled_job_id)
                if job is not None:
                    job.status = "error"
                    job.error = f"{type(exc).__name__}: {exc}"
                    store.save(job)
            await log.mount(plain_static(f"error: {type(exc).__name__}: {exc}", classes="meta"))
        finally:
            await self._finalize_assistant_stream()
            self._busy = False
            prompt.disabled = False
            self.sub_title = self._subtitle()
            prompt.focus()
            log.scroll_end(animate=False)

    async def _apply_event(self, ev: AgentEvent) -> None:
        log = self.query_one("#log", ChatLog)
        if ev.stream == "assistant":
            delta = str(ev.data.get("delta") or "")
            if not delta:
                return
            self._assistant_buf += delta
            stream = await self._ensure_assistant_stream()
            await stream.write(escape_rich_markup(delta))
            log.scroll_end(animate=False)
        elif ev.stream == "tool":
            name = str(ev.data.get("toolName") or "tool")
            phase = str(ev.data.get("phase") or "")
            call_id = str(ev.data.get("toolCallId") or name)
            if phase == "call":
                await self._mount_tool_call(
                    tool_call_id=call_id,
                    name=name,
                    args=ev.data.get("args"),
                )
            elif phase == "result":
                await self._update_tool_result(
                    tool_call_id=call_id,
                    name=name,
                    result=ev.data.get("result"),
                )
            else:
                await log.mount(plain_static(f"tool {phase} {name}", classes="meta"))
                log.scroll_end(animate=False)


def run_tui(
    *,
    session_key: str | None = None,
    settings: Settings | None = None,
    new_chat: bool = False,
) -> None:
    settings = settings or get_settings()
    key = SessionStore(settings).resolve_startup_key(
        session_key,
        new_chat=new_chat,
        agent_id=settings.agent_id,
    )
    ChatApp(settings=settings, session_key=key).run()
