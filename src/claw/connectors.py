"""Data connectors for the dashboard: Nessie (Capital One mock bank) and FinchNode (health).

Each connector returns the same panel payload so the dashboard and the agent
tools share one shape:

    {"id", "name", "description", "status", "source", "summary", "sections": [
        {"title", "columns": [...], "rows": [[...], ...]}], "fetchedAt"}

`status` is "live" (real API data), "mock" (internal: bundled data used when Nessie has
no customers yet) or "error".
"""

from __future__ import annotations

import copy
import math
import re
import threading
from datetime import date, datetime, timezone
from typing import Any, Callable

import httpx
from pydantic_ai import ModelRetry, RunContext

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
        "name": "FinchNode",
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


# The bundled mock is mutable so transactions made while Nessie has no customers show up in
# the panel. It lives for the life of the gateway process.
_mock_lock = threading.Lock()


# People the user can send money to, by first name. Ethan is the demo recipient; he is created
# in Nessie on first use when Nessie is live, and is part of the mock otherwise.
_DEMO_ADDRESS = {"street_number": "500", "street_name": "State Street", "city": "Ann Arbor", "state": "MI", "zip": "48104"}
DEMO_PAYEES: dict[str, dict[str, Any]] = {
    "ethan": {
        "customer": {"first_name": "Ethan", "last_name": "Park", "address": _DEMO_ADDRESS},
        "account": {"type": "Checking", "nickname": "Ethan Park", "rewards": 0, "balance": 1500},
    },
}


def _fresh_mock() -> dict[str, Any]:
    state = copy.deepcopy(_NESSIE_MOCK)
    state["activity"] = []
    state["payees"] = [
        {"_id": f"mock-payee-{k}", **copy.deepcopy(v["account"]), "first_name": v["customer"]["first_name"],
         "last_name": v["customer"]["last_name"]}
        for k, v in DEMO_PAYEES.items()
    ]
    return state


_mock_state: dict[str, Any] = _fresh_mock()


def reset_nessie_mock() -> None:
    global _mock_state
    with _mock_lock:
        _mock_state = _fresh_mock()


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
        activity: list[dict[str, Any]] = []
        seen: set[Any] = set()
        for acct in accounts[:5]:
            try:
                got = _nessie_get(client, base, f"/accounts/{acct['_id']}/purchases", key)
            except httpx.HTTPError:
                continue
            if isinstance(got, list):
                purchases.extend(got)
            for label, path in (("Deposit", "deposits"), ("Transfer", "transfers")):
                try:
                    got = _nessie_get(client, base, f"/accounts/{acct['_id']}/{path}", key)
                except httpx.HTTPError:
                    continue
                for item in got if isinstance(got, list) else []:
                    if item.get("_id") in seen:
                        continue
                    seen.add(item.get("_id"))
                    activity.append(
                        {"purchase_date": item.get("transaction_date"), "type": label,
                         "description": item.get("description"), "amount": item.get("amount"),
                         "status": item.get("status")}
                    )
    purchases.sort(key=lambda p: str(p.get("purchase_date") or ""), reverse=True)
    activity.sort(key=lambda a: str(a.get("purchase_date") or ""), reverse=True)
    return {"customers": customers, "accounts": accounts, "purchases": purchases, "activity": activity}


TRANSACTION_KINDS = ("purchase", "deposit", "transfer")
_KIND_PATH = {"purchase": "purchases", "deposit": "deposits", "transfer": "transfers"}


def _match_account(accounts: list[dict[str, Any]], ref: str | None) -> dict[str, Any]:
    """Find an account by id, nickname or type; with no ref prefer Checking."""
    if not accounts:
        raise ValueError("there are no accounts to use")
    if not ref or not ref.strip():
        return next((a for a in accounts if str(a.get("type", "")).lower() == "checking"), accounts[0])
    r = ref.strip().lower()
    for a in accounts:
        if r == str(a.get("_id", "")).lower():
            return a
    for a in accounts:
        if r in f"{a.get('nickname') or ''} {a.get('type') or ''}".lower():
            return a
    names = ", ".join(str(a.get("nickname") or a.get("type") or a.get("_id")) for a in accounts)
    raise ValueError(f"no account matching {ref!r}; choose one of: {names}")


def _own_match(accounts: list[dict[str, Any]], ref: str | None) -> dict[str, Any] | None:
    try:
        return _match_account(accounts, ref) if (ref or "").strip() else None
    except ValueError:
        return None


def _is_person(payee: dict[str, Any], ref: str) -> bool:
    r = ref.strip().lower()
    first, last = str(payee.get("first_name", "")).lower(), str(payee.get("last_name", "")).lower()
    return bool(r) and r in (first, f"{first} {last}".strip(), str(payee.get("_id", "")).lower())


def _nessie_post(client: httpx.Client, base: str, key: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    resp = client.post(f"{base}{path}", params={"key": key}, json=body)
    if resp.status_code >= 400:
        raise RuntimeError(f"Nessie rejected POST {path}: {resp.status_code} {resp.text[:200]}")
    return resp.json().get("objectCreated", {})


def _live_payee(
    client: httpx.Client, base: str, key: str, name: str | None, own_ids: set[Any]
) -> dict[str, Any] | None:
    """Another Nessie customer's first account, found by first or full name; the demo payee is created if absent."""
    ref = (name or "").strip().lower()
    if not ref:
        return None
    customers = _nessie_get(client, base, "/customers", key)
    for c in customers if isinstance(customers, list) else []:
        if _is_person(c, ref):
            accounts = _nessie_get(client, base, f"/customers/{c['_id']}/accounts", key)
            found = next((a for a in accounts if a.get("_id") not in own_ids), None) if isinstance(accounts, list) else None
            if found:
                return found
    demo = DEMO_PAYEES.get(ref.split()[0])
    if demo is None or not _is_person({**demo["customer"], "_id": ""}, ref):
        return None
    cust = _nessie_post(client, base, key, "/customers", demo["customer"])
    return _nessie_post(client, base, key, f"/customers/{cust['_id']}/accounts", demo["account"])


def _account_name(a: dict[str, Any]) -> str:
    return str(a.get("nickname") or a.get("type") or a.get("_id"))


def _post_live(
    client: httpx.Client, base: str, key: str, accounts: list[dict[str, Any]], kind: str,
    amount: float, description: str, account: str | None, to_account: str | None,
    when: str, merchant_id: str | None = None,
) -> dict[str, Any]:
    src = _match_account(accounts, account)
    body: dict[str, Any] = {"medium": "balance", "amount": amount, "description": description}
    if kind == "purchase":
        if merchant_id is None:
            merchants = _nessie_get(client, base, "/merchants", key)
            if not isinstance(merchants, list) or not merchants:
                raise RuntimeError("Nessie has no merchants to spend at")
            merchant_id = merchants[0]["_id"]
        body.update(merchant_id=merchant_id, purchase_date=when)
    elif kind == "transfer":
        dst = _own_match(accounts, to_account) or _live_payee(
            client, base, key, to_account, {a.get("_id") for a in accounts}
        )
        if dst is None:
            _match_account(accounts, to_account)  # raises with the list of valid accounts
            raise ValueError(f"no account or person matching {to_account!r}")
        if dst["_id"] == src["_id"]:
            raise ValueError("a transfer needs two different accounts")
        body.update(payee_id=dst["_id"], transaction_date=when)
    else:
        body["transaction_date"] = when
    resp = client.post(f"{base}/accounts/{src['_id']}/{_KIND_PATH[kind]}", params={"key": key}, json=body)
    if resp.status_code >= 400:
        raise RuntimeError(f"Nessie rejected the {kind}: {resp.status_code} {resp.text[:200]}")
    where = _account_name(src) + (f" to {_account_name(dst)}" if kind == "transfer" else "")
    return {"status": "live", "kind": kind, "message": f"{kind.capitalize()} of {_money(amount)} recorded on {where}."}


def _apply_mock(
    kind: str, amount: float, description: str, account: str | None, to_account: str | None, when: str
) -> dict[str, Any]:
    with _mock_lock:
        accounts = _mock_state["accounts"]
        src = _match_account(accounts, account)
        if kind == "purchase":
            src["balance"] = round(float(src["balance"]) - amount, 2)
            _mock_state["purchases"].insert(
                0, {"purchase_date": when, "description": description, "amount": amount, "status": "completed"}
            )
            where = _account_name(src)
        elif kind == "deposit":
            src["balance"] = round(float(src["balance"]) + amount, 2)
            _mock_state["activity"].insert(
                0, {"purchase_date": when, "type": "Deposit", "description": description, "amount": amount}
            )
            where = _account_name(src)
        else:
            payee = next((p for p in _mock_state["payees"] if _is_person(p, to_account or "")), None)
            dst = _own_match(accounts, to_account) or payee
            if dst is None:
                _match_account(accounts, to_account)  # raises with the list of valid accounts
                raise ValueError(f"no account or person matching {to_account!r}")
            if dst is src:
                raise ValueError("a transfer needs two different accounts")
            src["balance"] = round(float(src["balance"]) - amount, 2)
            dst["balance"] = round(float(dst["balance"]) + amount, 2)
            _mock_state["activity"].insert(
                0, {"purchase_date": when, "type": "Transfer",
                    "description": f"{description} (to {_account_name(dst)})", "amount": amount},
            )
            where = f"{_account_name(src)} to {_account_name(dst)}"
    return {"status": "mock", "kind": kind, "message": f"{kind.capitalize()} of {_money(amount)} recorded on {where}."}


def make_nessie_transaction(
    settings: Settings, kind: str, amount: float, description: str = "",
    account: str | None = None, to_account: str | None = None, when: str | None = None,
) -> dict[str, Any]:
    """Record a purchase, deposit or transfer. Goes to Nessie when it has customers, else the bundled mock.

    Raises ValueError for bad input and RuntimeError when Nessie rejects the request.
    """
    kind = (kind or "").strip().lower()
    if kind not in TRANSACTION_KINDS:
        raise ValueError(f"kind must be one of: {', '.join(TRANSACTION_KINDS)}")
    amount = float(amount)
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("amount must be greater than zero")
    amount = round(amount, 2)
    if kind == "transfer" and not (to_account or "").strip():
        raise ValueError("a transfer needs to_account")
    description = (description or "").strip() or {"purchase": "Purchase", "deposit": "Deposit", "transfer": "Transfer"}[kind]
    when = when or date.today().isoformat()

    base = settings.nessie_base_url.rstrip("/")
    key = settings.nessie_api_key or ""
    with httpx.Client(timeout=TIMEOUT_S) as client:
        try:
            customers = _nessie_get(client, base, "/customers", key)
            accounts = (
                _nessie_get(client, base, f"/customers/{customers[0]['_id']}/accounts", key)
                if isinstance(customers, list) and customers
                else None
            )
        except (httpx.HTTPError, KeyError, ValueError):
            accounts = None
        if accounts:
            try:
                return _post_live(client, base, key, accounts, kind, amount, description, account, to_account, when)
            except httpx.HTTPError as exc:
                raise RuntimeError(f"Could not reach Nessie: {type(exc).__name__}: {exc}") from exc
    return _apply_mock(kind, amount, description, account, to_account, when)


def _nessie_panel(data: dict[str, Any], *, status: str, note: str) -> dict[str, Any]:
    accounts = data["accounts"]
    purchases = data["purchases"]
    activity = data.get("activity") or []
    net = sum(float(a.get("balance") or 0) for a in accounts)
    sections = [
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
    ]
    payees = data.get("payees") or []
    if payees:
        sections.append(
            {
                "title": "Send money to",
                "columns": ["Name", "Account", "Balance"],
                "rows": [
                    [f"{p['first_name']} {p['last_name']}", p.get("type"), _money(p.get("balance"))] for p in payees
                ],
            }
        )
    if activity:
        sections.append(
            {
                "title": "Deposits and transfers",
                "columns": ["Date", "Type", "Description", "Amount"],
                "rows": [
                    [_day(a.get("purchase_date")), a.get("type"), a.get("description"), _money(a.get("amount"))]
                    for a in activity[:MAX_ROWS]
                ],
            }
        )
    return {
        "status": status,
        "source": note,
        "summary": f"{len(accounts)} accounts, net balance {_money(net)}, "
        f"{len(purchases)} recent purchases.",
        "sections": sections,
    }


def fetch_nessie(settings: Settings) -> dict[str, Any]:
    try:
        live = _nessie_live(settings)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        panel = _nessie_panel(_mock_state, status="mock", note=settings.nessie_base_url)
        panel["debug"] = f"{type(exc).__name__}: {exc}"
        return panel
    if live is None:
        return _nessie_panel(_mock_state, status="mock", note=settings.nessie_base_url)
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
    def nessie_transaction(
        ctx: RunContext[ClawDeps],
        kind: str,
        amount: float,
        description: str = "",
        account: str | None = None,
        to_account: str | None = None,
    ) -> str:
        """Make a transaction on the user's Capital One account through Nessie.

        kind is "purchase" (spend at a merchant; description is the merchant or item),
        "deposit" (money in), or "transfer" (move money from `account` to `to_account`).
        account and to_account are matched by nickname or type (e.g. "checking", "savings");
        account defaults to Checking. To send money to a person, pass their first name as
        to_account (e.g. "Ethan"). amount is in dollars and must be positive. The dashboard
        then shows the new transaction and updated balances.
        """
        try:
            result = make_nessie_transaction(
                ctx.deps.settings, kind, amount, description, account=account, to_account=to_account
            )
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc
        except RuntimeError as exc:
            return f"The transaction did not go through: {exc}"
        return result["message"]

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
