"""OpenClaw-compatible `automations` / `cron` agent tool (generic job API)."""

from __future__ import annotations

import json
import time
from typing import Any

from pydantic_ai import Agent, RunContext

from claw.jobs import normalize_job_spec, recover_job_from_flat
from claw.memory import ClawDeps
from claw.spacetime_jobs import get_job_store

ACTIONS = (
    "status",
    "list",
    "get",
    "add",
    "update",
    "remove",
    "run",
)


def register_automations_tool(agent: Agent[ClawDeps, str]) -> None:
    @agent.tool
    def automations(
        ctx: RunContext[ClawDeps],
        action: str,
        job: dict[str, Any] | None = None,
        job_id: str | None = None,
        # Flat recoveries (models often flatten beside action — same as OpenClaw)
        name: str | None = None,
        schedule: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        at: str | None = None,
        every: str | int | None = None,
        everyMs: int | None = None,
        cron: str | None = None,
        expr: str | None = None,
        message: str | None = None,
        text: str | None = None,
        sessionTarget: str | None = None,
        wakeMode: str | None = None,
        deleteAfterRun: bool | None = None,
        enabled: bool | None = None,
        includeDisabled: bool = False,
    ) -> str:
        """Gateway automations API (OpenClaw `automations` / `cron` tool).

        Generic scheduled work — not limited to reminders. Prefer a nested `job`
        object; flat fields are accepted and recovered into `job` when needed.

        Actions: status | list | get | add | update | remove | run

        job shape (add/update):
          {
            "name": "...",
            "schedule": {"kind":"at","at":"1m"|"ISO"}
                      | {"kind":"every","everyMs":60000}
                      | {"kind":"cron","expr":"0 9 * * *"},
            "sessionTarget": "main"|"current"|"isolated",
            "wakeMode": "now"|"next-heartbeat",
            "payload": {"kind":"agentTurn","message":"..."}
                     | {"kind":"systemEvent","text":"..."},
            "deleteAfterRun": true|false,
            "enabled": true|false
          }

        Never use shell sleep/OS crontab as a timer — write a job here. The
        Gateway CronService executes due jobs.
        """
        store = get_job_store(ctx.deps.settings)
        act = (action or "").strip().lower()
        # Alias: cron tool name historically used by OpenClaw
        if act == "create":
            act = "add"

        flat: dict[str, Any] = {
            "job": job,
            "name": name,
            "schedule": schedule,
            "payload": payload,
            "at": at,
            "every": every,
            "everyMs": everyMs,
            "cron": cron,
            "expr": expr,
            "message": message,
            "text": text,
            "sessionTarget": sessionTarget,
            "wakeMode": wakeMode,
            "deleteAfterRun": deleteAfterRun,
            "enabled": enabled,
        }
        # Drop Nones so recovery only sees provided fields
        flat = {k: v for k, v in flat.items() if v is not None}

        if act == "status":
            pending = store.list(include_done=False)
            backend = (
                f"spacetime:{ctx.deps.settings.spacetime_db}"
                if ctx.deps.settings.spacetime_enabled
                else str(getattr(store, "_dir", "local"))
            )
            return json.dumps(
                {
                    "enabled": True,
                    "pending": len(pending),
                    "store": backend,
                }
            )

        if act == "list":
            jobs = store.list(
                session_key=None if includeDisabled else ctx.deps.session_key,
                include_done=includeDisabled,
            )
            return json.dumps(
                {"jobs": [j.to_openclaw_summary() for j in jobs], "total": len(jobs)},
                indent=2,
            )

        if act == "get":
            jid = job_id or (job or {}).get("id") if isinstance(job, dict) else job_id
            if not jid:
                return "error: jobId required"
            found = store.get(str(jid))
            if found is None:
                return f"error: job not found: {jid}"
            return json.dumps(found.to_openclaw_summary(), indent=2)

        if act == "add":
            spec = recover_job_from_flat(flat)
            if spec is None:
                return (
                    "error: action=add requires job={schedule,payload,...} "
                    "(or flat at/every/cron + message/text)"
                )
            if "name" not in spec:
                spec["name"] = "automation"
            created = store.add_from_openclaw(spec, session_key=ctx.deps.session_key)
            return json.dumps(created.to_openclaw_summary(), indent=2)

        if act == "update":
            jid = job_id
            if not jid:
                return "error: action=update requires jobId"
            patch = recover_job_from_flat(flat) or {}
            if isinstance(job, dict):
                patch = {**patch, **normalize_patch(job)}
            updated = store.update(str(jid), patch)
            return json.dumps(updated.to_openclaw_summary(), indent=2)

        if act == "remove":
            jid = job_id
            if not jid:
                return "error: action=remove requires jobId"
            removed = store.cancel(str(jid))
            if removed is None:
                return f"error: job not found: {jid}"
            return json.dumps({"ok": True, "id": removed.id, "status": removed.status})

        if act == "run":
            jid = job_id
            if not jid:
                return "error: action=run requires jobId (forces due now)"
            found = store.get(str(jid))
            if found is None:
                return f"error: job not found: {jid}"
            found.next_run_at_ms = int(time.time() * 1000) - 1
            found.status = "pending"
            found.enabled = True
            store.save(found)
            return json.dumps(
                {
                    "ok": True,
                    "id": found.id,
                    "queued": True,
                    "note": "Job marked due; Gateway CronService will execute on next tick",
                }
            )

        return (
            f"error: unknown action {action!r}; "
            f"use {'|'.join(ACTIONS)}"
        )


def normalize_patch(job: dict[str, Any]) -> dict[str, Any]:
    return normalize_job_spec(job)
