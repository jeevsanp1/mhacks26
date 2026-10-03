"""Typer CLI: gateway, agent, agent exec."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from typing import Any, Optional

import typer
import websockets

from claw.channels.dispatch import dispatch_inbound
from claw.config import get_settings
from claw.events import AgentEvent
from claw.gateway.protocol import dumps, parse_frame, req
from claw.gateway.server import run_gateway
from claw.memory import reset_from_scratch
from claw.routing import Peer, build_session_key, normalize_peer_kind
from claw.runner import AgentRunner
from claw.scheduler import run_due_jobs
from claw.sessions import new_session_key
from claw.spacetime_jobs import get_job_store
from claw.tui import run_tui

app = typer.Typer(
    name="claw",
    help="OpenClaw-style AI harness built on Pydantic AI",
    no_args_is_help=True,
)
agent_app = typer.Typer(help="Run agent turns", no_args_is_help=False)
channel_app = typer.Typer(
    help="Multi-channel inbound (OpenClaw-style routing)",
    no_args_is_help=True,
)
automations_app = typer.Typer(
    help="Generic OpenClaw-style automations/cron jobs",
    no_args_is_help=False,
)
app.add_typer(agent_app, name="agent")
app.add_typer(channel_app, name="channel")
app.add_typer(automations_app, name="automations")
app.add_typer(automations_app, name="cron")  # OpenClaw alias
app.add_typer(automations_app, name="remind")  # convenience alias


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


@app.command("tui")
def tui_cmd(
    session: Optional[str] = typer.Option(
        None,
        "--session",
        "-s",
        help="Session key (default: last active / most recent chat)",
    ),
    new_chat: bool = typer.Option(
        False, "--new", "-n", help="Start a fresh chat (new session key)"
    ),
) -> None:
    """Interactive terminal chat with the embedded agent (no gateway required)."""
    run_tui(session_key=session, new_chat=new_chat)


@automations_app.callback(invoke_without_command=True)
def automations_main(
    ctx: typer.Context,
    at: Optional[str] = typer.Option(
        None, "--at", help="One-shot: 1m, 90s, ISO (schedule.kind=at)"
    ),
    every: Optional[str] = typer.Option(
        None, "--every", help="Interval: 10m, 1h (schedule.kind=every)"
    ),
    cron: Optional[str] = typer.Option(
        None, "--cron", help='Cron expr: "0 9 * * *" (schedule.kind=cron)'
    ),
    message: Optional[str] = typer.Option(
        None, "--message", "-m", help="agentTurn payload message"
    ),
    system_event: Optional[str] = typer.Option(
        None, "--system-event", help="systemEvent payload text"
    ),
    session: str = typer.Option("main", "--session", "-s", help="Session key"),
    session_target: str = typer.Option(
        "current", "--session-target", help="main|current|isolated"
    ),
    name: str = typer.Option("automation", "--name", help="Job name"),
    delete_after_run: Optional[bool] = typer.Option(
        None, "--delete-after-run/--keep", help="Delete after successful run"
    ),
) -> None:
    """Add a generic automation job (OpenClaw `automations add`)."""
    if ctx.invoked_subcommand is not None:
        return
    if not message and not system_event:
        _err(
            "usage: claw automations --at 1m -m \"…\"\n"
            "       claw automations --every 10m -m \"…\"\n"
            "       claw automations --cron \"0 9 * * *\" -m \"…\""
        )
        raise typer.Exit(2)
    if sum(1 for x in (at, every, cron) if x) != 1:
        _err("specify exactly one of --at / --every / --cron")
        raise typer.Exit(2)

    if at:
        schedule: dict[str, Any] = {"kind": "at", "at": at}
    elif every:
        schedule = {"kind": "every", "everyMs": every}
    else:
        schedule = {"kind": "cron", "expr": cron}

    if system_event:
        payload: dict[str, Any] = {"kind": "systemEvent", "text": system_event}
    else:
        payload = {"kind": "agentTurn", "message": message}

    spec: dict[str, Any] = {
        "name": name,
        "schedule": schedule,
        "sessionTarget": session_target,
        "wakeMode": "now",
        "payload": payload,
        "sessionKey": session,
    }
    if delete_after_run is not None:
        spec["deleteAfterRun"] = delete_after_run

    settings = get_settings()
    job = get_job_store(settings).add_from_openclaw(spec, session_key=session)
    print(json.dumps(job.to_openclaw_summary(), indent=2))
    _err("Keep `claw gateway` running so CronService executes due jobs.")


@automations_app.command("list")
def automations_list(
    session: Optional[str] = typer.Option(None, "--session", "-s"),
    all_jobs: bool = typer.Option(False, "--all", help="Include done/cancelled"),
) -> None:
    """List automation jobs."""
    settings = get_settings()
    jobs = get_job_store(settings).list(session_key=session, include_done=all_jobs)
    if not jobs:
        print("no jobs")
        return
    for job in jobs:
        print(
            f"{job.id[:8]}  {job.status:9}  {job.schedule_kind:5}  {job.run_at_iso}  "
            f"target={job.session_target} session={job.session_key}  "
            f"{job.name}: {job.message[:80]}"
        )


@automations_app.command("watch")
def remind_watch(
    session: Optional[str] = typer.Option(
        None, "--session", "-s", help="Only this session (default: all)"
    ),
    poll: float = typer.Option(1.0, "--poll", help="Seconds between due checks"),
) -> None:
    """Headless scheduler: run due jobs as they fire (no TUI)."""
    settings = get_settings()
    runner = AgentRunner.create(settings)
    _err(f"watching jobs (poll={poll}s). Ctrl+C to stop.")

    async def _watch() -> None:
        while True:
            async def on_event(ev: AgentEvent) -> None:
                if ev.stream == "assistant":
                    sys.stdout.write(str(ev.data.get("delta") or ""))
                    sys.stdout.flush()

            results = await run_due_jobs(
                runner, session_key=session, on_event=on_event
            )
            for job, snap in results:
                if not (snap.output or "").strip():
                    print()
                _err(f"fired {job.id[:8]} status={snap.status}")
            await asyncio.sleep(poll)

    try:
        asyncio.run(_watch())
    except KeyboardInterrupt:
        _err("stopped")


@app.command("gateway")
def gateway_cmd(
    host: Optional[str] = typer.Option(None, help="Bind host (default GATEWAY_HOST)"),
    port: Optional[int] = typer.Option(None, help="Bind port (default GATEWAY_PORT)"),
) -> None:
    """Start the long-lived WebSocket gateway (default 127.0.0.1:18789)."""
    settings = get_settings()
    h = host or settings.gateway_host
    p = port if port is not None else settings.gateway_port
    _err(f"claw gateway listening on ws://{h}:{p}/ws")
    _err(
        "Note: Coder/Shell tools run on the host workspace. "
        "Gateway binds localhost by default."
    )
    run_gateway(settings, host=h, port=p)


@app.command("reset")
def reset_cmd(
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation and wipe agent state"
    ),
) -> None:
    """Wipe chats, MEMORY.md, persona files, and cron jobs; re-seed defaults.

    Does not delete your coding repo (`CLAW_WORKSPACE`). Only `.claw` agent home.
    """
    settings = get_settings()
    if not yes:
        typer.confirm(
            f"Delete sessions, memory, and {settings.agent_home}?",
            abort=True,
        )
    counts = reset_from_scratch(settings)
    print(
        f"reset ok — sessions={counts['sessions']} jobs={counts['jobs']} "
        f"workspace={settings.agent_home}"
    )


@agent_app.command("new")
def agent_new() -> None:
    """Print a fresh session key (use with `claw tui -s …` or `claw agent -s …`)."""
    settings = get_settings()
    key = new_session_key(settings.agent_id)
    print(key)
    _err(f"start with: claw tui -s {key}")


@channel_app.command("inbound")
def channel_inbound(
    message: str = typer.Option(..., "--message", "-m", help="Inbound text"),
    channel: str = typer.Option("cli", "--channel", "-c", help="Channel id"),
    peer_id: str = typer.Option(..., "--peer-id", help="Sender / room id"),
    peer_kind: str = typer.Option(
        "direct", "--peer-kind", help="direct|group|channel|thread"
    ),
    account_id: str = typer.Option("default", "--account-id"),
    agent_id: Optional[str] = typer.Option(None, "--agent-id"),
    queue: Optional[str] = typer.Option(None, "--queue"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Simulate a channel message (routes to OpenClaw-shaped sessionKey)."""
    settings = get_settings()
    runner = AgentRunner.create(settings)
    payload = {
        "message": message,
        "channel": channel,
        "peer": {"kind": peer_kind, "id": peer_id},
        "accountId": account_id,
        "agentId": agent_id or settings.agent_id,
        "queueMode": queue,
    }

    async def _run() -> dict[str, Any]:
        return await dispatch_inbound(runner, payload, queue_mode=queue, wait=True)

    result = asyncio.run(_run())
    route = result.get("route") or {}
    _err(f"sessionKey={route.get('sessionKey')} delivery={route.get('delivery')}")
    out = (result.get("outbound") or {}).get("text") or (result.get("run") or {}).get(
        "output"
    )
    if out:
        print(out)
    status = (result.get("run") or {}).get("status")
    if status and status != "ok":
        _err(f"{status}: {(result.get('run') or {}).get('error')}")
        raise typer.Exit(1)
    if verbose:
        print(json.dumps(result, indent=2, default=str), file=sys.stderr)


@channel_app.command("key")
def channel_key(
    channel: str = typer.Option(..., "--channel", "-c"),
    peer_id: str = typer.Option(..., "--peer-id"),
    peer_kind: str = typer.Option("direct", "--peer-kind"),
    account_id: str = typer.Option("default", "--account-id"),
) -> None:
    """Print the sessionKey that would be used for a channel peer."""
    settings = get_settings()
    key = build_session_key(
        agent_id=settings.agent_id,
        channel=channel,
        account_id=account_id,
        peer=Peer(kind=normalize_peer_kind(peer_kind), id=peer_id),
        dm_scope=settings.dm_scope,  # type: ignore[arg-type]
        group_scope=settings.group_scope,  # type: ignore[arg-type]
        identity_links=settings.identity_links,
    )
    print(key)


@agent_app.callback(invoke_without_command=True)
def agent_main(
    ctx: typer.Context,
    message: Optional[str] = typer.Option(
        None, "--message", "-m", help="User message / prompt"
    ),
    session: str = typer.Option("main", "--session", "-s", help="Session key"),
    url: Optional[str] = typer.Option(
        None, "--url", help="Gateway WebSocket URL (default ws://host:port/ws)"
    ),
    timeout_ms: Optional[int] = typer.Option(
        None, "--timeout-ms", help="Optional run timeout in milliseconds"
    ),
    queue: Optional[str] = typer.Option(
        None,
        "--queue",
        help="Queue mode: steer|followup|collect|interrupt (default CLAW_QUEUE_MODE)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print tool events"),
) -> None:
    """Run one agent turn through the gateway (connect → agent → stream → agent.wait)."""
    if ctx.invoked_subcommand is not None:
        return
    if not message:
        _err("error: -m/--message is required")
        raise typer.Exit(2)
    settings = get_settings()
    ws_url = url or f"ws://{settings.gateway_host}:{settings.gateway_port}/ws"
    code = asyncio.run(
        _agent_via_gateway(
            message=message,
            session_key=session,
            ws_url=ws_url,
            token=settings.gateway_token,
            timeout_ms=timeout_ms,
            queue_mode=queue,
            verbose=verbose,
        )
    )
    raise typer.Exit(code)


@agent_app.command("exec")
def agent_exec(
    message: str = typer.Option(..., "--message", "-m", help="User message / prompt"),
    session: str = typer.Option("main", "--session", "-s", help="Session key"),
    timeout_ms: Optional[int] = typer.Option(
        None, "--timeout-ms", help="Optional run timeout in milliseconds"
    ),
    queue: Optional[str] = typer.Option(
        None,
        "--queue",
        help="Queue mode: steer|followup|collect|interrupt (default CLAW_QUEUE_MODE)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print tool events"),
) -> None:
    """Run one embedded agent turn without connecting to a gateway."""
    settings = get_settings()
    runner = AgentRunner.create(settings)
    timeout_s = (timeout_ms / 1000.0) if timeout_ms is not None else None

    async def on_event(ev: AgentEvent) -> None:
        _print_event(ev, verbose=verbose)

    snap = asyncio.run(
        runner.run_embedded(
            message,
            session_key=session,
            timeout_s=timeout_s,
            on_event=on_event,
            queue_mode=queue,
        )
    )
    if snap.output:
        # When streaming already printed assistant deltas, avoid duplicate final line
        # unless nothing was streamed (test model often only has final output).
        pass
    if snap.status != "ok":
        _err(f"{snap.status}: {snap.error or ''}")
        raise typer.Exit(1)
    # Ensure a trailing newline after streamed deltas
    sys.stdout.write("\n")
    sys.stdout.flush()


async def _agent_via_gateway(
    *,
    message: str,
    session_key: str,
    ws_url: str,
    token: str | None,
    timeout_ms: int | None,
    verbose: bool,
    queue_mode: str | None = None,
) -> int:
    connect_params: dict[str, Any] = {
        "minProtocol": 1,
        "maxProtocol": 1,
        "role": "operator",
        "client": {"id": "cli", "version": "0.1.0", "mode": "operator"},
    }
    if token:
        connect_params["auth"] = {"token": token}

    try:
        async with websockets.connect(ws_url) as ws:
            await ws.send(dumps(req("1", "connect", connect_params)))
            hello_raw = await ws.recv()
            hello = parse_frame(hello_raw)
            if not hello.get("ok"):
                _err(f"connect failed: {hello}")
                return 1

            agent_params: dict[str, Any] = {
                "message": message,
                "sessionKey": session_key,
                "idempotencyKey": str(uuid.uuid4()),
            }
            if timeout_ms is not None:
                agent_params["timeoutMs"] = timeout_ms
            if queue_mode:
                agent_params["queueMode"] = queue_mode

            await ws.send(dumps(req("2", "agent", agent_params)))

            run_id: str | None = None
            assistant_buf: list[str] = []

            while True:
                raw = await ws.recv()
                frame = parse_frame(raw)
                ftype = frame.get("type")

                if ftype == "res" and frame.get("id") == "2":
                    if not frame.get("ok"):
                        _err(f"agent rejected: {frame.get('error')}")
                        return 1
                    payload = frame.get("payload") or {}
                    run_id = payload.get("runId")
                    _err(f"accepted runId={run_id}")
                    wait_params: dict[str, Any] = {"runId": run_id}
                    if timeout_ms is not None:
                        wait_params["timeoutMs"] = timeout_ms
                    await ws.send(dumps(req("3", "agent.wait", wait_params)))
                    continue

                if ftype == "event" and frame.get("event") == "agent":
                    payload = frame.get("payload") or {}
                    if run_id and payload.get("runId") not in (None, run_id):
                        continue
                    stream = payload.get("stream")
                    data = payload.get("data") or {}
                    if stream == "assistant":
                        delta = data.get("delta") or ""
                        assistant_buf.append(delta)
                        sys.stdout.write(delta)
                        sys.stdout.flush()
                    elif stream == "tool" and verbose:
                        args_preview = json.dumps(data.get("args"), default=str)[:200]
                        _err(
                            f"tool {data.get('phase')} {data.get('toolName')} {args_preview}"
                        )
                    elif stream == "lifecycle" and verbose:
                        _err(f"lifecycle {data.get('phase')}")
                    continue

                if ftype == "res" and frame.get("id") == "3":
                    if assistant_buf and not str(assistant_buf[-1]).endswith("\n"):
                        sys.stdout.write("\n")
                    if not frame.get("ok"):
                        _err(f"agent.wait failed: {frame.get('error')}")
                        return 1
                    payload = frame.get("payload") or {}
                    status = payload.get("status")
                    if status == "ok":
                        if not assistant_buf and payload.get("output"):
                            print(payload["output"])
                        return 0
                    _err(f"{status}: {payload.get('error') or ''}")
                    return 1
    except OSError as exc:
        _err(
            f"could not connect to gateway at {ws_url}: {exc}\n"
            "Start it with `claw gateway`, or use `claw agent exec -m ...`"
        )
        return 1


def _print_event(ev: AgentEvent, *, verbose: bool) -> None:
    if ev.stream == "assistant":
        sys.stdout.write(str(ev.data.get("delta") or ""))
        sys.stdout.flush()
    elif ev.stream == "tool" and verbose:
        _err(f"tool {ev.data.get('phase')} {ev.data.get('toolName')}")
    elif ev.stream == "lifecycle" and verbose:
        _err(f"lifecycle {ev.data.get('phase')}")
