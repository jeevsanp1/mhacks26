"""Gateway RPC method handlers."""

from __future__ import annotations

import time
from typing import Any

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from claw.channels.dispatch import dispatch_inbound, start_inbound
from claw.config import PROTOCOL_VERSION, Settings
from claw.events import truncate_payload
from claw.gateway.protocol import ADVERTISED_EVENTS, ADVERTISED_METHODS
from claw.routing import parse_inbound_params
from claw.runner import AgentRunner
from claw.scheduler import run_due_jobs
from claw.spacetime_jobs import get_job_store


def handle_connect(
    params: dict[str, Any],
    *,
    settings: Settings,
    conn_id: str,
) -> dict[str, Any]:
    token = settings.gateway_token
    if token:
        auth = params.get("auth") or {}
        provided = auth.get("token") if isinstance(auth, dict) else None
        if provided != token:
            raise PermissionError("invalid gateway token")

    return {
        "type": "hello-ok",
        "protocol": PROTOCOL_VERSION,
        "server": {"version": "0.1.0", "connId": conn_id},
        "features": {"methods": ADVERTISED_METHODS, "events": ADVERTISED_EVENTS},
        "auth": {"role": params.get("role", "operator")},
        "policy": {
            "maxPayload": 26214400,
            "tickIntervalMs": 15000,
        },
    }


def handle_health(runner: AgentRunner) -> dict[str, Any]:
    store = get_job_store(runner.settings)
    pending = store.list()
    return {
        "ok": True,
        "ts": int(time.time() * 1000),
        "model": runner.settings.model,
        "workspace": str(runner.settings.workspace),
        "activeRuns": len(runner._tasks),
        "cronPending": len(pending),
        "queueMode": runner.settings.queue_mode,
        "heartbeatEvery": runner.settings.heartbeat_every,
        "heartbeatSession": runner.settings.heartbeat_session,
        "agentId": runner.settings.agent_id,
        "dmScope": runner.settings.dm_scope,
        "groupScope": runner.settings.group_scope,
    }


def handle_agent(
    params: dict[str, Any],
    *,
    runner: AgentRunner,
) -> dict[str, Any]:
    message = params.get("message") or params.get("prompt")
    if not message or not isinstance(message, str):
        raise ValueError("params.message is required")

    session_key = params.get("sessionKey") or params.get("session") or "main"
    idem = params.get("idempotencyKey")
    timeout_ms = params.get("timeoutMs")
    timeout_s = float(timeout_ms) / 1000.0 if timeout_ms is not None else None
    queue_mode = params.get("queueMode") or params.get("queue")

    accepted = runner.start_turn(
        message,
        session_key=str(session_key),
        idempotency_key=str(idem) if idem else None,
        timeout_s=timeout_s,
        queue_mode=str(queue_mode) if queue_mode else None,
    )
    return accepted.to_payload()


def handle_chat_history(params: dict[str, Any], *, runner: AgentRunner) -> dict[str, Any]:
    """Session transcript as ordered user/assistant/tool entries (tool calls paired with results)."""
    session_key = str(params.get("sessionKey") or params.get("session") or "main")
    entries: list[dict[str, Any]] = []
    tools_by_id: dict[str, dict[str, Any]] = {}
    for msg in runner.store.load(session_key):
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    if part.content.strip():
                        entries.append({"kind": "user", "text": part.content})
                elif isinstance(part, ToolReturnPart):
                    entry = tools_by_id.get(part.tool_call_id)
                    if entry is None:
                        entry = {"kind": "tool", "name": part.tool_name or "tool"}
                        entries.append(entry)
                    entry["result"] = truncate_payload(part.content)
                    entry["done"] = True
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart) and part.content:
                    entries.append({"kind": "assistant", "text": part.content})
                elif isinstance(part, ToolCallPart):
                    entry = {
                        "kind": "tool",
                        "name": part.tool_name or "tool",
                        "toolCallId": part.tool_call_id,
                        "args": part.args,
                        "done": False,
                    }
                    tools_by_id[part.tool_call_id] = entry
                    entries.append(entry)
    return {"sessionKey": session_key, "entries": entries}


async def handle_agent_wait(
    params: dict[str, Any],
    *,
    runner: AgentRunner,
) -> dict[str, Any]:
    run_id = params.get("runId")
    if not run_id or not isinstance(run_id, str):
        raise ValueError("params.runId is required")
    timeout_ms = params.get("timeoutMs")
    timeout_s = float(timeout_ms) / 1000.0 if timeout_ms is not None else None
    snapshot = await runner.wait(run_id, timeout_s=timeout_s)
    return snapshot.to_payload()


def handle_channel_inbound(
    params: dict[str, Any],
    *,
    runner: AgentRunner,
) -> dict[str, Any]:
    """Accept a normalized multi-channel message; return route + runId immediately."""
    inbound = parse_inbound_params(params)
    timeout_ms = params.get("timeoutMs")
    timeout_s = float(timeout_ms) / 1000.0 if timeout_ms is not None else None
    queue_mode = params.get("queueMode") or params.get("queue")
    idem = params.get("idempotencyKey")
    route, accepted = start_inbound(
        runner,
        inbound,
        queue_mode=str(queue_mode) if queue_mode else None,
        timeout_s=timeout_s,
        idempotency_key=str(idem) if idem else None,
    )
    return {
        "route": route.to_payload(),
        "accepted": accepted.to_payload(),
        "runId": accepted.run_id,
        "sessionKey": route.session_key,
        "delivery": route.delivery.to_payload(),
    }


async def handle_channel_inbound_wait(
    params: dict[str, Any],
    *,
    runner: AgentRunner,
) -> dict[str, Any]:
    """Route + run + wait (HTTP convenience)."""
    timeout_ms = params.get("timeoutMs")
    timeout_s = float(timeout_ms) / 1000.0 if timeout_ms is not None else None
    queue_mode = params.get("queueMode") or params.get("queue")
    return await dispatch_inbound(
        runner,
        params,
        queue_mode=str(queue_mode) if queue_mode else None,
        timeout_s=timeout_s,
        wait=True,
    )


def handle_cron_add(
    params: dict[str, Any],
    *,
    runner: AgentRunner,
) -> dict[str, Any]:
    """OpenClaw-compatible cron.add — persist a generic automation job."""
    from claw.jobs import recover_job_from_flat

    store = get_job_store(runner.settings)
    session_key = str(params.get("sessionKey") or params.get("session") or "main")
    spec = recover_job_from_flat(params)
    if spec is None:
        raise ValueError(
            "cron.add requires job={schedule,payload,...} "
            "or flat schedule fields + message/text"
        )
    job = store.add_from_openclaw(spec, session_key=session_key)
    return job.to_openclaw_summary()


def handle_cron_list(
    params: dict[str, Any],
    *,
    runner: AgentRunner,
) -> dict[str, Any]:
    store = get_job_store(runner.settings)
    session_key = params.get("sessionKey")
    include_disabled = bool(params.get("includeDisabled") or params.get("all"))
    jobs = store.list(
        session_key=str(session_key) if session_key else None,
        include_done=include_disabled,
    )
    return {
        "jobs": [j.to_openclaw_summary() for j in jobs],
        "total": len(jobs),
    }


def handle_cron_update(
    params: dict[str, Any],
    *,
    runner: AgentRunner,
) -> dict[str, Any]:
    job_id = params.get("jobId") or params.get("id")
    if not job_id:
        raise ValueError("cron.update requires jobId")
    from claw.jobs import recover_job_from_flat

    patch = params.get("job") if isinstance(params.get("job"), dict) else None
    if patch is None:
        patch = recover_job_from_flat(params) or {}
    return get_job_store(runner.settings).update(str(job_id), patch).to_openclaw_summary()


def handle_cron_get(
    params: dict[str, Any],
    *,
    runner: AgentRunner,
) -> dict[str, Any]:
    job_id = params.get("jobId") or params.get("id")
    if not job_id:
        raise ValueError("cron.get requires jobId")
    job = get_job_store(runner.settings).get(str(job_id))
    if job is None:
        raise ValueError(f"job not found: {job_id}")
    return job.to_openclaw_summary()


def handle_cron_remove(
    params: dict[str, Any],
    *,
    runner: AgentRunner,
) -> dict[str, Any]:
    job_id = params.get("jobId") or params.get("id")
    if not job_id:
        raise ValueError("cron.remove requires jobId")
    store = get_job_store(runner.settings)
    job = store.cancel(str(job_id))
    if job is None:
        raise ValueError(f"job not found: {job_id}")
    return {"ok": True, "id": job.id, "status": job.status}


async def handle_cron_run(
    params: dict[str, Any],
    *,
    runner: AgentRunner,
) -> dict[str, Any]:
    """Run due jobs now (mode=due) or force one jobId."""
    mode = str(params.get("mode") or "due")
    store = get_job_store(runner.settings)
    if mode == "force":
        job_id = params.get("jobId") or params.get("id")
        if not job_id:
            raise ValueError("cron.run mode=force requires jobId")
        job = store.get(str(job_id))
        if job is None:
            raise ValueError(f"job not found: {job_id}")
        job.next_run_at_ms = int(time.time() * 1000) - 1
        job.status = "pending"
        job.enabled = True
        store.save(job)
    results = await run_due_jobs(
        runner,
        session_key=str(params["sessionKey"]) if params.get("sessionKey") else None,
    )
    return {
        "ran": [
            {"job": j.to_openclaw_summary(), "run": s.to_payload()}
            for j, s in results
        ]
    }
