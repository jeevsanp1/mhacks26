"""SpacetimeDB-backed job coordination (atomic claim across workers)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import quote

import httpx

from claw.config import Settings
from claw.jobs import CronJob, JobStore, compute_next_run_at_ms
from claw.sessions import sanitize_session_key

log = logging.getLogger(__name__)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


class SpacetimeJobStore:
    """Job store that coordinates via SpacetimeDB reducers + SQL reads.

    Multiple gateway processes can safely call :meth:`claim_due` — the
    ``claimDueJob`` reducer runs in a transaction and assigns at most one job.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        worker_id: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not settings.spacetime_uri or not settings.spacetime_db:
            raise ValueError("CLAW_SPACETIME_URI and CLAW_SPACETIME_DB are required")
        self.settings = settings
        self.worker_id = worker_id or settings.spacetime_worker_id
        self._base = settings.spacetime_uri.rstrip("/")
        self._db = settings.spacetime_db
        self._token = settings.spacetime_token
        self._client = client or httpx.Client(timeout=15.0)
        # Local mirror for OpenClaw tools that still expect file paths (optional).
        self._mirror = JobStore(settings)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _call(self, reducer: str, args: list[Any]) -> None:
        url = f"{self._base}/v1/database/{quote(self._db, safe='')}/call/{quote(reducer, safe='')}"
        resp = self._client.post(url, headers=self._headers(), json=args)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"spacetime {reducer} failed ({resp.status_code}): {resp.text[:500]}"
            )

    def _sql(self, query: str) -> list[dict[str, Any]]:
        url = f"{self._base}/v1/database/{quote(self._db, safe='')}/sql"
        resp = self._client.post(
            url,
            headers=self._headers(),
            content=query.encode("utf-8"),
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"spacetime sql failed ({resp.status_code}): {resp.text[:500]}"
            )
        payload = resp.json()
        if not payload:
            return []
        # Response: [{ schema, rows }] — rows are arrays matching schema field order
        first = payload[0]
        schema = first.get("schema") or {}
        elements = schema.get("elements") or []
        names: list[str] = []
        for el in elements:
            name = el.get("name")
            if isinstance(name, dict):
                names.append(str(name.get("some") or ""))
            elif isinstance(name, str):
                names.append(name)
            else:
                names.append("")
        rows_out: list[dict[str, Any]] = []
        for row in first.get("rows") or []:
            if isinstance(row, dict):
                rows_out.append(row)
                continue
            if isinstance(row, list):
                rows_out.append(
                    {names[i]: row[i] for i in range(min(len(names), len(row)))}
                )
                continue
            rows_out.append({"value": row})
        return rows_out

    def _row_to_job(self, row: dict[str, Any]) -> CronJob:
        schedule = json.loads(row.get("schedule_json") or "{}")
        payload = json.loads(row.get("payload_json") or "{}")
        delivery_raw = row.get("delivery_json") or ""
        delivery = json.loads(delivery_raw) if delivery_raw else None
        last = int(row.get("last_run_at_ms") or 0)
        return CronJob(
            id=str(row["id"]),
            name=str(row.get("name") or "automation"),
            enabled=bool(row.get("enabled", True)),
            schedule=schedule,
            session_target=str(row.get("session_target") or "current"),
            wake_mode=str(row.get("wake_mode") or "now"),
            payload=payload,
            delete_after_run=bool(row.get("delete_after_run", True)),
            next_run_at_ms=int(row.get("next_run_at_ms") or 0),
            created_at_ms=int(row.get("created_at_ms") or 0),
            status=str(row.get("status") or "pending"),
            session_key=str(row.get("session_key") or "main"),
            error=(str(row["error"]) if row.get("error") else None),
            output=(str(row["output"]) if row.get("output") else None),
            last_run_at_ms=last or None,
            delivery=delivery if isinstance(delivery, dict) else None,
        )

    def path_for(self, job_id: str):
        return self._mirror.path_for(job_id)

    def add_from_openclaw(self, job_spec: dict[str, Any], *, session_key: str) -> CronJob:
        # Reuse local normalization, then push to Spacetime instead of only disk.
        job = self._mirror.add_from_openclaw(job_spec, session_key=session_key)
        self._push_add(job)
        return job

    def _push_add(self, job: CronJob) -> None:
        self._call(
            "add_job",
            [
                job.id,
                job.name,
                job.enabled,
                _json_dumps(job.schedule),
                _json_dumps(job.payload),
                job.session_target,
                job.wake_mode,
                job.delete_after_run,
                job.next_run_at_ms,
                job.created_at_ms,
                job.session_key,
                _json_dumps(job.delivery) if job.delivery else "",
            ],
        )

    def add(self, **kwargs: Any) -> CronJob:
        job = self._mirror.add(**kwargs)
        self._push_add(job)
        return job

    def save(self, job: CronJob) -> None:
        self._mirror.save(job)
        self._call(
            "update_job",
            [
                job.id,
                job.name,
                job.enabled,
                _json_dumps(job.schedule),
                _json_dumps(job.payload),
                job.session_target,
                job.wake_mode,
                job.delete_after_run,
                job.next_run_at_ms,
                job.session_key,
                job.status,
                _json_dumps(job.delivery) if job.delivery else "",
            ],
        )

    def get(self, job_id: str) -> CronJob | None:
        rows = self._sql(f"SELECT * FROM job WHERE id = '{_sql_escape(job_id)}'")
        if not rows:
            return self._mirror.get(job_id)
        return self._row_to_job(rows[0])

    def list(
        self,
        *,
        session_key: str | None = None,
        include_done: bool = False,
    ) -> list[CronJob]:
        rows = self._sql("SELECT * FROM job")
        jobs = [self._row_to_job(r) for r in rows]
        if session_key:
            key = sanitize_session_key(session_key)
            jobs = [j for j in jobs if j.session_key == key]
        if not include_done:
            jobs = [j for j in jobs if j.status in {"pending", "running"} and j.enabled]
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
        """Atomically claim one due job. Returns None if nothing due."""
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        worker = worker_id or self.worker_id
        # Reclaim stuck running jobs first (default 15m)
        try:
            self._call("reclaim_stale", [now, 15 * 60_000])
        except Exception:  # noqa: BLE001
            log.debug("reclaim_stale failed", exc_info=True)
        self._call("claim_due_job", [worker, now, session_key or ""])
        # Spacetime SQL is limited — filter/sort in Python.
        rows = self._sql(
            f"SELECT * FROM job WHERE claimed_by = '{_sql_escape(worker)}'"
        )
        running = [
            self._row_to_job(r)
            for r in rows
            if str(r.get("status") or "") == "running"
        ]
        if not running:
            return None
        running.sort(key=lambda j: j.last_run_at_ms or 0, reverse=True)
        job = running[0]
        # Keep local mirror in sync for tools/TUI that still read files
        self._mirror.save(job)
        return job

    def remove(self, job_id: str) -> bool:
        try:
            self._call("remove_job", [job_id])
        except RuntimeError as exc:
            if "not found" in str(exc).lower():
                return self._mirror.remove(job_id)
            raise
        self._mirror.remove(job_id)
        return True

    def cancel(self, job_id: str) -> CronJob | None:
        try:
            self._call("cancel_job", [job_id])
        except RuntimeError:
            return self._mirror.cancel(job_id)
        job = self.get(job_id)
        if job is not None:
            self._mirror.save(job)
        return job

    def update(self, job_id: str, patch: dict[str, Any]) -> CronJob:
        # Normalize via local store helpers then push
        local = self._mirror.get(job_id) or self.get(job_id)
        if local is None:
            raise ValueError(f"job not found: {job_id}")
        self._mirror.save(local)
        updated = self._mirror.update(job_id, patch)
        self.save(updated)
        return updated

    def advance_after_run(self, job: CronJob, *, success: bool) -> None:
        kind = job.schedule_kind
        now = int(time.time() * 1000)
        if success and job.delete_after_run and kind == "at":
            self._call(
                "complete_job",
                [
                    job.id,
                    self.worker_id,
                    True,
                    job.output or "",
                    "",
                    True,  # remove
                    "done",
                    0,
                    False,
                ],
            )
            self._mirror.remove(job.id)
            return

        if kind in {"every", "cron"} and job.enabled:
            next_ms = compute_next_run_at_ms(job.schedule, after_ms=now)
            self._call(
                "complete_job",
                [
                    job.id,
                    self.worker_id,
                    success,
                    job.output or "",
                    "" if success else (job.error or ""),
                    False,
                    "pending",
                    next_ms,
                    True,
                ],
            )
            job.status = "pending"
            job.next_run_at_ms = next_ms
            job.error = None if success else job.error
            self._mirror.save(job)
            return

        status = "done" if success else "error"
        self._call(
            "complete_job",
            [
                job.id,
                self.worker_id,
                success,
                job.output or "",
                "" if success else (job.error or "error"),
                False,
                status,
                job.next_run_at_ms,
                success,
            ],
        )
        job.status = status
        if not success:
            job.enabled = False
        self._mirror.save(job)


def _sql_escape(value: str) -> str:
    return value.replace("'", "''")


def get_job_store(settings: Settings) -> JobStore | SpacetimeJobStore:
    """Return Spacetime-backed store when configured, else local file JobStore."""
    if settings.spacetime_enabled:
        return SpacetimeJobStore(settings)
    return JobStore(settings)
