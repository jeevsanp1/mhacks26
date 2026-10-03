"""Generic OpenClaw-shaped cron/automation job store + next-run computation."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from croniter import croniter

from claw.config import Settings
from claw.sessions import sanitize_session_key

_REL = re.compile(
    r"^\s*(?:(?P<days>\d+)\s*d)?\s*(?:(?P<hours>\d+)\s*h)?\s*"
    r"(?:(?P<minutes>\d+)\s*m)?\s*(?:(?P<seconds>\d+)\s*s)?\s*$",
    re.IGNORECASE,
)

_EVERY = re.compile(
    r"^\s*(?:every\s+)?(?P<n>\d+)\s*(?P<u>ms|s|m|h|d)\s*$",
    re.IGNORECASE,
)


def parse_absolute_or_relative_ms(value: str, *, now_ms: int | None = None) -> int:
    """ISO timestamp or relative duration (`1m`, `90s`, `1h`) → epoch ms."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("empty time")
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    try:
        iso = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        pass

    m = _REL.match(raw)
    if m and any(m.group(g) for g in ("days", "hours", "minutes", "seconds")):
        seconds = (
            int(m.group("days") or 0) * 86400
            + int(m.group("hours") or 0) * 3600
            + int(m.group("minutes") or 0) * 60
            + int(m.group("seconds") or 0)
        )
        if seconds <= 0:
            raise ValueError("duration must be > 0")
        return now + seconds * 1000
    raise ValueError(f"invalid time {value!r}; use ISO or relative like 1m / 90s")


# Back-compat name used by tests/CLI
parse_delay = parse_absolute_or_relative_ms


def parse_every_ms(value: str | int) -> int:
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("everyMs must be > 0")
        return value
    raw = value.strip()
    m = _EVERY.match(raw) or _REL.match(raw)
    if m is None:
        raise ValueError(f"invalid every interval {value!r}")
    if "n" in m.groupdict() and m.group("n"):
        n = int(m.group("n"))
        u = (m.group("u") or "s").lower()
        mult = {"ms": 1, "s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}[u]
        return n * mult
    seconds = (
        int(m.group("days") or 0) * 86400
        + int(m.group("hours") or 0) * 3600
        + int(m.group("minutes") or 0) * 60
        + int(m.group("seconds") or 0)
    )
    if seconds <= 0:
        raise ValueError("every interval must be > 0")
    return seconds * 1000


def ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def compute_next_run_at_ms(
    schedule: dict[str, Any],
    *,
    now_ms: int | None = None,
    after_ms: int | None = None,
) -> int:
    """Compute next fire time from OpenClaw schedule {kind: at|every|cron, ...}."""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    base = after_ms if after_ms is not None else now
    kind = (schedule.get("kind") or "at").strip().lower()

    if kind == "at":
        at = schedule.get("at")
        if at is None:
            raise ValueError("schedule.at required for kind=at")
        if isinstance(at, (int, float)):
            return int(at)
        return parse_absolute_or_relative_ms(str(at), now_ms=now)

    if kind == "every":
        every = schedule.get("everyMs")
        if every is None and schedule.get("every") is not None:
            every = schedule["every"]
        if every is None:
            raise ValueError("schedule.everyMs required for kind=every")
        every_ms = parse_every_ms(every)  # type: ignore[arg-type]
        anchor = schedule.get("anchorMs")
        if anchor is None:
            return base + every_ms
        anchor_i = int(anchor)
        if base < anchor_i:
            return anchor_i
        elapsed = base - anchor_i
        steps = elapsed // every_ms + 1
        return anchor_i + steps * every_ms

    if kind == "cron":
        expr = schedule.get("expr") or schedule.get("cron")
        if not expr or not isinstance(expr, str):
            raise ValueError("schedule.expr required for kind=cron")
        # croniter uses local/naive; feed UTC then convert
        dt = datetime.fromtimestamp(base / 1000, tz=timezone.utc)
        itr = croniter(expr, dt)
        nxt = itr.get_next(datetime)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        return int(nxt.timestamp() * 1000)

    raise ValueError(f"unsupported schedule.kind {kind!r}; use at|every|cron")


@dataclass
class CronJob:
    """OpenClaw CronJob wire shape (subset) + local runtime fields."""

    id: str
    name: str
    enabled: bool
    schedule: dict[str, Any]
    session_target: str
    wake_mode: str
    payload: dict[str, Any]
    delete_after_run: bool
    next_run_at_ms: int
    created_at_ms: int
    status: str = "pending"
    session_key: str = "main"
    error: str | None = None
    output: str | None = None
    last_run_at_ms: int | None = None
    delivery: dict[str, Any] | None = None

    @property
    def message(self) -> str:
        return str(self.payload.get("message") or self.payload.get("text") or "")

    @property
    def run_at_ms(self) -> int:
        return self.next_run_at_ms

    @property
    def run_at_iso(self) -> str:
        return ms_to_iso(self.next_run_at_ms)

    @property
    def schedule_kind(self) -> str:
        return str(self.schedule.get("kind") or "at")

    def to_openclaw_summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "schedule": self.schedule,
            "sessionTarget": self.session_target,
            "wakeMode": self.wake_mode,
            "payload": self.payload,
            "deleteAfterRun": self.delete_after_run,
            "nextRunAtMs": self.next_run_at_ms,
            "nextRunAt": self.run_at_iso,
            "status": self.status,
            "sessionKey": self.session_key,
        }
        if self.delivery is not None:
            out["delivery"] = self.delivery
        if self.error:
            out["error"] = self.error
        if self.last_run_at_ms is not None:
            out["lastRunAtMs"] = self.last_run_at_ms
        return out


# Legacy alias for older imports
Job = CronJob


def normalize_job_spec(job_spec: dict[str, Any]) -> dict[str, Any]:
    """Accept camelCase / snake_case OpenClaw job fields."""
    aliases = {
        "session_target": "sessionTarget",
        "wake_mode": "wakeMode",
        "delete_after_run": "deleteAfterRun",
        "session_key": "sessionKey",
    }
    out = dict(job_spec)
    for snake, camel in aliases.items():
        if snake in out and camel not in out:
            out[camel] = out[snake]
    return out


def recover_job_from_flat(params: dict[str, Any]) -> dict[str, Any] | None:
    """OpenClaw-style: models sometimes flatten job fields beside action."""
    if isinstance(params.get("job"), dict) and params["job"]:
        return normalize_job_spec(params["job"])

    schedule: dict[str, Any] = {}
    if params.get("at") is not None:
        schedule = {"kind": "at", "at": params["at"]}
    elif params.get("every") is not None or params.get("everyMs") is not None:
        schedule = {
            "kind": "every",
            "everyMs": params.get("everyMs") or params.get("every"),
        }
        if params.get("anchorMs") is not None:
            schedule["anchorMs"] = params["anchorMs"]
    elif params.get("cron") is not None or params.get("expr") is not None:
        schedule = {
            "kind": "cron",
            "expr": params.get("expr") or params.get("cron"),
        }
        if params.get("tz"):
            schedule["tz"] = params["tz"]
    elif isinstance(params.get("schedule"), dict):
        schedule = dict(params["schedule"])

    payload: dict[str, Any] = {}
    if isinstance(params.get("payload"), dict):
        payload = dict(params["payload"])
    elif params.get("message") is not None:
        payload = {"kind": "agentTurn", "message": params["message"]}
    elif params.get("text") is not None or params.get("systemEvent") is not None:
        payload = {
            "kind": "systemEvent",
            "text": params.get("text") or params.get("systemEvent"),
        }

    if not schedule and not payload:
        return None

    job: dict[str, Any] = {}
    if schedule:
        job["schedule"] = schedule
    if payload:
        job["payload"] = payload
    for key in (
        "name",
        "sessionTarget",
        "wakeMode",
        "deleteAfterRun",
        "sessionKey",
        "enabled",
        "delivery",
    ):
        if key in params:
            job[key] = params[key]
    return normalize_job_spec(job) if job else None


class JobStore:
    """Persists automation jobs under `.claw/cron/jobs/`."""

    def __init__(self, settings: Settings) -> None:
        self._dir = settings.state_dir / "cron" / "jobs"
        self._dir.mkdir(parents=True, exist_ok=True)
        legacy = settings.state_dir / "jobs"
        if legacy.is_dir():
            for path in legacy.glob("*.json"):
                dest = self._dir / path.name
                if not dest.exists():
                    dest.write_bytes(path.read_bytes())

    def path_for(self, job_id: str) -> Path:
        return self._dir / f"{job_id}.json"

    def add_from_openclaw(self, job_spec: dict[str, Any], *, session_key: str) -> CronJob:
        """Create from OpenClaw `{ name, schedule, payload, sessionTarget, ... }`."""
        spec = normalize_job_spec(job_spec)
        schedule = spec.get("schedule")
        if not isinstance(schedule, dict):
            raise ValueError("job.schedule is required")
        payload = spec.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("job.payload is required")
        kind = (payload.get("kind") or "agentTurn").strip()
        if kind == "agentTurn" and not payload.get("message"):
            raise ValueError("payload.message required for agentTurn")
        if kind == "systemEvent" and not payload.get("text"):
            raise ValueError("payload.text required for systemEvent")
        if kind not in {"agentTurn", "systemEvent"}:
            raise ValueError(
                f"unsupported payload.kind {kind!r}; use agentTurn|systemEvent"
            )

        now = int(time.time() * 1000)
        next_ms = compute_next_run_at_ms(schedule, now_ms=now)
        sched_kind = (schedule.get("kind") or "at").lower()
        # OpenClaw: one-shots default deleteAfterRun=true; recurring default false
        default_delete = sched_kind == "at"
        delete_after = spec.get("deleteAfterRun")
        if delete_after is None:
            delete_after = default_delete

        # Normalize at schedules to absolute ISO in storage
        stored_schedule = dict(schedule)
        if sched_kind == "at":
            stored_schedule = {"kind": "at", "at": ms_to_iso(next_ms)}
        elif sched_kind == "every":
            every_ms = parse_every_ms(
                schedule.get("everyMs") or schedule.get("every")  # type: ignore[arg-type]
            )
            stored_schedule = {
                "kind": "every",
                "everyMs": every_ms,
                "anchorMs": int(schedule.get("anchorMs") or now),
            }
        elif sched_kind == "cron":
            stored_schedule = {
                "kind": "cron",
                "expr": schedule.get("expr") or schedule.get("cron"),
            }
            if schedule.get("tz"):
                stored_schedule["tz"] = schedule["tz"]

        session = sanitize_session_key(
            str(spec.get("sessionKey") or session_key or "main")
        )
        # sessionTarget=isolated → fresh session key per OpenClaw spirit
        target = str(spec.get("sessionTarget") or "current")
        if target == "isolated":
            session = sanitize_session_key(f"cron-{uuid.uuid4().hex[:12]}")

        job = CronJob(
            id=str(uuid.uuid4()),
            name=str(spec.get("name") or "automation").strip() or "automation",
            enabled=bool(spec.get("enabled", True)),
            schedule=stored_schedule,
            session_target=target,
            wake_mode=str(spec.get("wakeMode") or "now"),
            payload=dict(payload),
            delete_after_run=bool(delete_after),
            next_run_at_ms=next_ms,
            created_at_ms=now,
            session_key=session,
            status="pending",
            delivery=spec.get("delivery") if isinstance(spec.get("delivery"), dict) else None,
        )
        self.save(job)
        return job

    def add(
        self,
        *,
        delay: str,
        message: str,
        session_key: str = "main",
        name: str = "automation",
        session_target: str = "current",
        wake_mode: str = "now",
        delete_after_run: bool = True,
        payload_kind: str = "agentTurn",
    ) -> CronJob:
        """Convenience wrapper for one-shot agentTurn (CLI)."""
        payload = (
            {"kind": "systemEvent", "text": message}
            if payload_kind == "systemEvent"
            else {"kind": "agentTurn", "message": message}
        )
        return self.add_from_openclaw(
            {
                "name": name,
                "schedule": {"kind": "at", "at": delay},
                "sessionTarget": session_target,
                "wakeMode": wake_mode,
                "payload": payload,
                "deleteAfterRun": delete_after_run,
                "sessionKey": session_key,
            },
            session_key=session_key,
        )

    def update(self, job_id: str, patch: dict[str, Any]) -> CronJob:
        job = self.get(job_id)
        if job is None:
            raise ValueError(f"job not found: {job_id}")
        patch = normalize_job_spec(patch)
        if "name" in patch and patch["name"] is not None:
            job.name = str(patch["name"])
        if "enabled" in patch and patch["enabled"] is not None:
            job.enabled = bool(patch["enabled"])
            if job.enabled and job.status in {"cancelled", "done", "error"}:
                job.status = "pending"
        if "sessionTarget" in patch and patch["sessionTarget"] is not None:
            job.session_target = str(patch["sessionTarget"])
        if "wakeMode" in patch and patch["wakeMode"] is not None:
            job.wake_mode = str(patch["wakeMode"])
        if "deleteAfterRun" in patch and patch["deleteAfterRun"] is not None:
            job.delete_after_run = bool(patch["deleteAfterRun"])
        if "sessionKey" in patch and patch["sessionKey"] is not None:
            job.session_key = sanitize_session_key(str(patch["sessionKey"]))
        if "delivery" in patch:
            job.delivery = patch["delivery"] if isinstance(patch["delivery"], dict) else None
        if isinstance(patch.get("payload"), dict):
            job.payload = {**job.payload, **patch["payload"]}
        if isinstance(patch.get("schedule"), dict):
            job.schedule = dict(patch["schedule"])
            job.next_run_at_ms = compute_next_run_at_ms(job.schedule)
            if (job.schedule.get("kind") or "at") == "at":
                job.schedule = {"kind": "at", "at": ms_to_iso(job.next_run_at_ms)}
        self.save(job)
        return job

    def save(self, job: CronJob) -> None:
        self.path_for(job.id).write_text(
            json.dumps(asdict(job), indent=2) + "\n", encoding="utf-8"
        )

    def _load_raw(self, path: Path) -> CronJob | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if "schedule" not in data and "message" in data:
            run_at = int(data["run_at_ms"])
            data = {
                "id": data["id"],
                "name": data.get("name") or "automation",
                "enabled": data.get("status", "pending") in {"pending", "running"},
                "schedule": {"kind": "at", "at": ms_to_iso(run_at)},
                "session_target": "current",
                "wake_mode": "now",
                "payload": {"kind": "agentTurn", "message": data["message"]},
                "delete_after_run": True,
                "next_run_at_ms": run_at,
                "created_at_ms": int(data.get("created_at_ms") or run_at),
                "status": data.get("status") or "pending",
                "session_key": data.get("session_key") or "main",
                "error": data.get("error"),
                "output": data.get("output"),
                "last_run_at_ms": data.get("last_run_at_ms"),
                "delivery": None,
            }
        try:
            return CronJob(**{k: data.get(k) for k in CronJob.__dataclass_fields__})
        except TypeError:
            return None

    def get(self, job_id: str) -> CronJob | None:
        path = self.path_for(job_id)
        if not path.exists():
            return None
        return self._load_raw(path)

    def list(
        self,
        *,
        session_key: str | None = None,
        include_done: bool = False,
    ) -> list[CronJob]:
        jobs: list[CronJob] = []
        for path in sorted(self._dir.glob("*.json")):
            job = self._load_raw(path)
            if job is None:
                continue
            if session_key and job.session_key != sanitize_session_key(session_key):
                continue
            if not include_done and job.status not in {"pending", "running"}:
                continue
            if not include_done and not job.enabled:
                continue
            jobs.append(job)
        jobs.sort(key=lambda j: j.next_run_at_ms)
        return jobs

    def due(
        self,
        *,
        now_ms: int | None = None,
        session_key: str | None = None,
    ) -> list[CronJob]:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        return [
            j
            for j in self.list(session_key=session_key, include_done=False)
            if j.enabled and j.status == "pending" and j.next_run_at_ms <= now
        ]

    def claim_due(
        self,
        *,
        now_ms: int | None = None,
        session_key: str | None = None,
        worker_id: str | None = None,
    ) -> CronJob | None:
        """Claim one due job under a directory lock (single-host coordination)."""
        import fcntl

        now = now_ms if now_ms is not None else int(time.time() * 1000)
        lock_path = self._dir / ".claim.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            due = self.due(now_ms=now, session_key=session_key)
            if not due:
                return None
            job = due[0]
            job.status = "running"
            job.last_run_at_ms = now
            self.save(job)
            return job

    def remove(self, job_id: str) -> bool:
        path = self.path_for(job_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def cancel(self, job_id: str) -> CronJob | None:
        job = self.get(job_id)
        if job is None:
            return None
        job.status = "cancelled"
        job.enabled = False
        self.save(job)
        return job

    def advance_after_run(self, job: CronJob, *, success: bool) -> None:
        """After a run: delete one-shot, or compute next occurrence for recurring."""
        kind = job.schedule_kind
        if success and job.delete_after_run and kind == "at":
            self.remove(job.id)
            return
        if kind in {"every", "cron"} and job.enabled:
            job.status = "pending"
            job.next_run_at_ms = compute_next_run_at_ms(
                job.schedule, after_ms=int(time.time() * 1000)
            )
            job.error = None if success else job.error
            self.save(job)
            return
        # one-shot kept or failed
        job.status = "done" if success else "error"
        if not success:
            job.enabled = False
        self.save(job)


def scheduled_prompt(job: CronJob) -> str:
    """Build the turn prompt for a fired job (generic; payload-driven)."""
    kind = (job.payload.get("kind") or "agentTurn").strip()
    if kind == "systemEvent":
        text = str(job.payload.get("text") or "")
        return (
            f"[automation:{job.name}] System event from cron schedule "
            f"{job.schedule!r}.\n\n{text}"
        )
    message = str(job.payload.get("message") or "")
    return (
        f"[automation:{job.name}] Scheduled agent turn "
        f"(schedule={job.schedule!r}, sessionTarget={job.session_target}).\n\n"
        f"{message}"
    )
