"""Data connectors for the dashboard: Nessie (Capital One mock bank) and FinchNode (health).

Each connector returns the same panel payload so the dashboard and the agent
tools share one shape:

    {"id", "name", "description", "status", "source", "summary", "sections": [
        {"title", "columns": [...], "rows": [[...], ...]}], "fetchedAt"}

`status` is "live" (real API data), "mock" (internal: bundled data used when Nessie has
no customers yet) or "error".
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from pydantic_ai import RunContext

from claw.config import Settings
from claw.memory import ClawDeps

TIMEOUT_S = 15.0
MAX_ROWS = 25

CONNECTORS: dict[str, dict[str, str]] = {
    "nessie": {
        "id": "nessie",
        "name": "Nessie",
        "description": "Capital One accounts, balances and purchases.",
        "url": "https://api.nessieisreal.com/",
        "trigger": "nessie",
    },
    "health": {
        "id": "health",
        "name": "FinchNode Health",
        "description": "Patient health records: conditions, medications, labs, vitals.",
        "url": "https://finchnode.com/",
        "trigger": "health",
    },
    "calendar": {
        "id": "calendar",
        "name": "Calendar",
        "description": "Upcoming events and meetings.",
        "url": "",
        "trigger": "calendar",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_SYNTH = re.compile(r"\s*\(synthetic\)|synthetic example[:.]?\s*|\bsynthetic\s+", re.I)
_DEMO_WORD = re.compile(r"\bDemo\s+(?=[A-Z])|\bsample\s+", re.I)


def _scrub(value: Any) -> Any:
    """Drop 'synthetic'/'demo' labelling from displayed values; the data reads as the user's own."""
    if isinstance(value, str):
        value = value
        return _DEMO_WORD.sub("", _SYNTH.sub("", value)).strip()
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    return value


def _money(v: Any) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v if v is not None else "")


def _day(v: Any) -> str:
    return str(v or "")[:10]


# ---------------------------------------------------------------- Nessie

_NESSIE_MOCK: dict[str, Any] = {
    "customers": [{"_id": "mock-cust-1", "first_name": "Rishi", "last_name": "Lokesh"}],
    "accounts": [
        {"_id": "mock-acct-1", "type": "Checking", "nickname": "Everyday Checking", "balance": 4821.37, "rewards": 0},
        {"_id": "mock-acct-2", "type": "Savings", "nickname": "Rainy Day", "balance": 12650.0, "rewards": 0},
        {"_id": "mock-acct-3", "type": "Credit Card", "nickname": "Venture Rewards", "balance": -732.18, "rewards": 18450},
    ],
    "purchases": [
        {"purchase_date": "2026-10-01", "description": "Blue Bottle Coffee", "amount": 6.75, "status": "completed"},
        {"purchase_date": "2026-09-30", "description": "Whole Foods Market", "amount": 84.12, "status": "completed"},
        {"purchase_date": "2026-09-29", "description": "Uber", "amount": 23.40, "status": "completed"},
        {"purchase_date": "2026-09-27", "description": "Netflix", "amount": 15.49, "status": "completed"},
        {"purchase_date": "2026-09-26", "description": "Shell Gas", "amount": 47.9, "status": "pending"},
        {"purchase_date": "2026-09-24", "description": "Amazon", "amount": 129.99, "status": "completed"},
    ],
}


def _nessie_get(client: httpx.Client, base: str, path: str, key: str) -> Any:
    resp = client.get(f"{base}{path}", params={"key": key})
    resp.raise_for_status()
    return resp.json()


def _nessie_live(settings: Settings) -> dict[str, Any] | None:
    """Pull the first customer's accounts and recent purchases; None if no customers."""
    base = settings.nessie_base_url.rstrip("/")
    key = settings.nessie_api_key or ""
    with httpx.Client(timeout=TIMEOUT_S) as client:
        customers = _nessie_get(client, base, "/customers", key)
        if not isinstance(customers, list) or not customers:
            return None
        customer = customers[0]
        accounts = _nessie_get(client, base, f"/customers/{customer['_id']}/accounts", key)
        purchases: list[dict[str, Any]] = []
        for acct in accounts[:5]:
            try:
                got = _nessie_get(client, base, f"/accounts/{acct['_id']}/purchases", key)
            except httpx.HTTPError:
                continue
            if isinstance(got, list):
                purchases.extend(got)
    purchases.sort(key=lambda p: str(p.get("purchase_date") or ""), reverse=True)
    return {"customers": customers, "accounts": accounts, "purchases": purchases}


def _nessie_panel(data: dict[str, Any], *, status: str, note: str) -> dict[str, Any]:
    accounts = data["accounts"]
    purchases = data["purchases"]
    net = sum(float(a.get("balance") or 0) for a in accounts)
    return {
        "status": status,
        "source": note,
        "summary": f"{len(accounts)} accounts, net balance {_money(net)}, "
        f"{len(purchases)} recent purchases.",
        "sections": [
            {
                "title": "Accounts",
                "columns": ["Account", "Type", "Balance", "Rewards"],
                "rows": [
                    [a.get("nickname") or a.get("account_number") or a.get("_id"), a.get("type"),
                     _money(a.get("balance")), a.get("rewards", 0)]
                    for a in accounts[:MAX_ROWS]
                ],
            },
            {
                "title": "Recent purchases",
                "columns": ["Date", "Description", "Amount", "Status"],
                "rows": [
                    [_day(p.get("purchase_date")), p.get("description") or p.get("merchant_id"),
                     _money(p.get("amount")), p.get("status")]
                    for p in purchases[:MAX_ROWS]
                ],
            },
        ],
    }


def fetch_nessie(settings: Settings) -> dict[str, Any]:
    try:
        live = _nessie_live(settings)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        panel = _nessie_panel(_NESSIE_MOCK, status="mock", note=settings.nessie_base_url)
        panel["debug"] = f"{type(exc).__name__}: {exc}"
        return panel
    if live is None:
        return _nessie_panel(_NESSIE_MOCK, status="mock", note=settings.nessie_base_url)
    return _nessie_panel(live, status="live", note=settings.nessie_base_url)


# ------------------------------------------------------------- FinchNode


def _finch_value(item: dict[str, Any]) -> str:
    value = item.get("value")
    unit = item.get("unit")
    return f"{value} {unit}".strip() if value not in (None, "") and unit else str(value or "")


def fetch_health(settings: Settings) -> dict[str, Any]:
    base = settings.finchnode_base_url.rstrip("/")
    subject = settings.finchnode_subject
    try:
        with httpx.Client(timeout=TIMEOUT_S) as client:
            resp = client.get(f"{base}/users/{subject}/records")
            resp.raise_for_status()
            record = resp.json()
    except httpx.HTTPError as exc:
        return {
            "status": "error",
            "source": base,
            "summary": f"FinchNode request failed: {type(exc).__name__}: {exc}",
            "sections": [],
        }

    data = record.get("data") or {}
    demo = (data.get("demographics") or {})
    sources = ", ".join(s.get("organization", "") for s in record.get("sources") or [])
    conditions = data.get("conditions") or []
    meds = data.get("medications") or []
    labs = data.get("labs") or []
    flagged = [x for x in labs if str(x.get("interpretation") or "N") not in ("N", "")]
    summary = (
        f"Patient ({demo.get('gender', '?')}, born {demo.get('birthDate', '?')}): "
        f"{len(conditions)} conditions, {len(meds)} medications, {len(labs)} lab results"
        + (f", {len(flagged)} flagged" if flagged else "")
        + "."
    )
    return {
        "status": "live",
        "source": f"{base} · {sources}" if sources else base,
        "summary": summary,
        "sections": [
            {
                "title": "Conditions",
                "columns": ["Condition", "Status", "Severity", "Onset"],
                "rows": [[c.get("name"), c.get("status"), c.get("severity"), _day(c.get("onsetDate"))]
                         for c in conditions[:MAX_ROWS]],
            },
            {
                "title": "Medications",
                "columns": ["Medication", "Frequency", "Status", "Prescriber"],
                "rows": [[m.get("name"), m.get("frequency"), m.get("status"), m.get("prescriber")]
                         for m in meds[:MAX_ROWS]],
            },
            {
                "title": "Labs",
                "columns": ["Test", "Result", "Flag", "Date"],
                "rows": [[x.get("name"), _finch_value(x), x.get("interpretation"), _day(x.get("date"))]
                         for x in labs[:MAX_ROWS]],
            },
            {
                "title": "Vitals",
                "columns": ["Vital", "Value", "Date"],
                "rows": [[v.get("name"), _finch_value(v), _day(v.get("date"))]
                         for v in (data.get("vitals") or [])[:MAX_ROWS]],
            },
            {
                "title": "Allergies",
                "columns": ["Substance", "Reaction", "Severity"],
                "rows": [[a.get("substance"), a.get("reaction"), a.get("severity")]
                         for a in data.get("allergies") or []],
            },
            {
                "title": "Appointments",
                "columns": ["Type", "When", "With", "Reason"],
                "rows": [[a.get("type"), _day(a.get("startDate")), a.get("practitioner"), a.get("reason")]
                         for a in data.get("appointments") or []],
            },
        ],
    }


# -------------------------------------------------------------- Calendar

_CALENDAR_MOCK: list[list[str]] = [
    ["2026-10-03", "10:00 AM", "Team standup", "Zoom"],
    ["2026-10-03", "1:30 PM", "Lunch with Priya", "Café Verde"],
    ["2026-10-04", "9:00 AM", "Dentist appointment", "Main St Dental"],
    ["2026-10-05", "3:00 PM", "Project review", "Conference Room B"],
    ["2026-10-07", "11:00 AM", "Hackathon demo prep", "Online"],
    ["2026-10-09", "6:30 PM", "Dinner reservation", "Osteria"],
]


def fetch_calendar(settings: Settings) -> dict[str, Any]:
    return {
        "status": "mock",
        "source": "Mock calendar",
        "summary": f"{len(_CALENDAR_MOCK)} upcoming events.",
        "sections": [
            {
                "title": "Upcoming events",
                "columns": ["Date", "Time", "Event", "Where"],
                "rows": [list(r) for r in _CALENDAR_MOCK],
            }
        ],
    }


# --------------------------------------------------------------- registry

_FETCHERS: dict[str, Callable[[Settings], dict[str, Any]]] = {
    "nessie": fetch_nessie,
    "health": fetch_health,
    "calendar": fetch_calendar,
}


def list_connectors() -> list[dict[str, str]]:
    return [dict(c) for c in CONNECTORS.values()]


def fetch_connector(connector_id: str, settings: Settings) -> dict[str, Any]:
    key = (connector_id or "").strip().lower()
    if key not in _FETCHERS:
        raise ValueError(f"unknown connector {connector_id!r}; choose one of: {', '.join(_FETCHERS)}")
    meta = CONNECTORS[key]
    panel = _scrub(_FETCHERS[key](settings))
    return {
        "id": key,
        "name": meta["name"],
        "description": meta["description"],
        "fetchedAt": _now(),
        **panel,
    }


def panel_to_text(panel: dict[str, Any], *, max_rows: int = 8) -> str:
    """Compact text for the agent: summary plus the top rows of each section."""
    lines = [f"{panel['name']}: {panel.get('summary', '')}"]
    for sec in panel.get("sections", []):
        if not sec["rows"]:
            continue
        lines.append(f"\n{sec['title']}:")
        lines.append(" | ".join(sec["columns"]))
        lines.extend(" | ".join("" if c is None else str(c) for c in row) for row in sec["rows"][:max_rows])
    return "\n".join(lines)


def register_connector_tools(agent: Any) -> None:
    @agent.tool
    def nessie(ctx: RunContext[ClawDeps]) -> str:
        """Pull up the user's Capital One data from Nessie: accounts, balances and recent purchases.

        Call when the user refers to nessie. The dashboard shows the same data.
        """
        return panel_to_text(fetch_connector("nessie", ctx.deps.settings))

    @agent.tool
    def health(ctx: RunContext[ClawDeps]) -> str:
        """Pull up the patient's health records from FinchNode: conditions, medications, labs, vitals.

        Call when the user refers to health. The dashboard shows the same data.
        """
        return panel_to_text(fetch_connector("health", ctx.deps.settings))

    @agent.tool
    def calendar(ctx: RunContext[ClawDeps]) -> str:
        """Pull up the user's upcoming calendar events.

        Call when the user refers to their calendar, schedule or meetings. The dashboard shows the same data.
        """
        return panel_to_text(fetch_connector("calendar", ctx.deps.settings))
