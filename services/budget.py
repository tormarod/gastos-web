"""
Budgets and savings goals: the numbers behind the home page and Ajustes.

settings.json:
  {"version": 1,
   "budgets": {"Supermercado": 450, ...},              euros per month; missing = no budget
   "fixed_categories": ["Alquiler", ...],              paid in one go, so they have no pace
   "goals": {"monthly_saving": 200, "annual_fund": 2400,
             "fund_name": "Fondo común", "fund_note": "Viajes · Hogar · Ocio"},
   "updated_at": "..."}

Everything here is pure: it takes month summaries (ledger.month_summaries) and
the settings, and returns plain dicts for the templates.

The pace of variable spending is measured against the last movement we have,
not against today: what hasn't been uploaded yet can't be seen.
"""

from __future__ import annotations

import calendar
import math
import re
from datetime import date
from typing import Any, Iterable, Mapping

from services import categorizer as cat
from services import ledger as lg

SETTINGS_VERSION = 1
DEFAULT_FIXED = ["Alquiler", "Suministros", "Telefonía", "Seguros"]
DEFAULT_GOALS: dict[str, Any] = {
    "monthly_saving": 200.0,
    "annual_fund": 2400.0,
    "fund_name": "Fondo común",
    "fund_note": "Viajes · Hogar · Ocio",
}
# Ingresos is not spending, Otros means "needs review" and account adjustments
# are money moving back and forth, so none of them get a budget.
BUDGETABLE: list[str] = [c for c in cat.SPENDING_CATEGORIES if c != cat.ADJUSTMENTS]

MAX_BUDGET = 100_000.0
MAX_GOAL = 1_000_000.0
MAX_NAME = 40
MAX_NOTE = 80
WARN_AT = 85.0          # % of a variable budget where "Cerca del límite" starts
PACE_TOLERANCE = 0.03   # within 3 % of the variable budget counts as "on pace"
PAID_TOLERANCE = 0.5    # euros of rounding when comparing a fixed bill with its budget
STALE_AFTER_DAYS = 7
SUGGEST_MONTHS = 3
SUGGEST_MIN = 5.0
SUGGEST_STEP = 10

_MONTH_RE = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


# ── Months ───────────────────────────────────────────────────────────────────

def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def is_month(value: str | None) -> bool:
    return bool(value and _MONTH_RE.fullmatch(value))


def shift_month(month: str, delta: int) -> str:
    index = int(month[:4]) * 12 + int(month[5:7]) - 1 + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def days_in_month(month: str) -> int:
    return calendar.monthrange(int(month[:4]), int(month[5:7]))[1]


def pick_month(requested: str | None, months: list[dict[str, Any]], today: date) -> str:
    """The month to show: the requested one if valid, kept between the first month with data and today."""
    current = month_key(today)
    if not is_month(requested):
        return current
    assert requested is not None
    first = months[0]["month"] if months else current
    return min(max(requested, min(first, current)), current)


# ── Settings ─────────────────────────────────────────────────────────────────

def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_amount(text: str | None, maximum: float) -> float | None:
    """'450', '450,5', '1.234,56' or '' → euros (None when empty). ValueError if not a valid amount."""
    raw = (text or "").strip().replace(" ", "").replace("€", "")
    if not raw:
        return None
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):
        raw = raw.replace(".", "")  # 2.400 written the Spanish way
    number = _number(raw)
    if number is None or number < 0 or number > maximum:
        raise ValueError(text)
    return round(number, 2)


def settings_view(doc: Mapping[str, Any] | None) -> dict[str, Any]:
    """The stored settings with defaults filled in and anything unknown dropped."""
    doc = doc or {}
    budgets: dict[str, float] = {}
    for name, value in (doc.get("budgets") or {}).items():
        amount = _number(value)
        if name in BUDGETABLE and amount is not None and amount > 0:
            budgets[name] = round(amount, 2)
    stored_fixed = doc.get("fixed_categories")
    fixed = [c for c in (DEFAULT_FIXED if stored_fixed is None else stored_fixed) if c in BUDGETABLE]
    goals = dict(DEFAULT_GOALS)
    stored_goals = doc.get("goals") or {}
    for key in ("monthly_saving", "annual_fund"):
        amount = _number(stored_goals.get(key))
        if amount is not None and amount >= 0:
            goals[key] = amount
    for key in ("fund_name", "fund_note"):
        if isinstance(stored_goals.get(key), str):
            goals[key] = stored_goals[key].strip()
    if not goals["fund_name"]:
        goals["fund_name"] = DEFAULT_GOALS["fund_name"]
    return {"budgets": budgets, "fixed_categories": fixed, "goals": goals}


def set_budgets(doc: dict[str, Any], budgets: Mapping[str, float], fixed: Iterable[str], now: str) -> None:
    doc["version"] = SETTINGS_VERSION
    doc["budgets"] = {c: budgets[c] for c in BUDGETABLE if budgets.get(c)}
    doc["fixed_categories"] = [c for c in BUDGETABLE if c in set(fixed)]
    doc["updated_at"] = now


def set_goals(doc: dict[str, Any], goals: Mapping[str, Any], now: str) -> None:
    doc["version"] = SETTINGS_VERSION
    doc["goals"] = {
        "monthly_saving": goals["monthly_saving"],
        "annual_fund": goals["annual_fund"],
        "fund_name": str(goals["fund_name"]).strip()[:MAX_NAME] or DEFAULT_GOALS["fund_name"],
        "fund_note": str(goals["fund_note"]).strip()[:MAX_NOTE],
    }
    doc["updated_at"] = now


# ── Suggestions ──────────────────────────────────────────────────────────────

def recent_months(months: list[dict[str, Any]], before: str, count: int = SUGGEST_MONTHS) -> list[dict[str, Any]]:
    return [m for m in months if m["month"] < before][-count:]


def suggest_budgets(months: list[dict[str, Any]], before: str) -> dict[str, Any]:
    """
    Average spending per category over the last complete months before `before`,
    rounded up to 10 €. Categories under 5 € a month are left without a budget.
    """
    window = recent_months(months, before)
    if not window:
        return {"months": [], "averages": {}, "suggested": {}, "total_average": None}
    n = len(window)
    averages = {c: round(sum(m["summary"].get(c, 0.0) for m in window) / n, 2) for c in BUDGETABLE}
    suggested = {
        c: float(math.ceil(avg / SUGGEST_STEP) * SUGGEST_STEP)
        for c, avg in averages.items() if avg >= SUGGEST_MIN
    }
    return {
        "months": [m["month"] for m in window],
        "averages": averages,
        "suggested": suggested,
        "total_average": round(sum(m["total_expense"] for m in window) / n, 2),
    }


# ── Savings fund ─────────────────────────────────────────────────────────────

def fund_progress(months: list[dict[str, Any]], goals: Mapping[str, Any], up_to: str, today: date) -> dict[str, Any]:
    """
    What the months of up_to's year have left over, counting only months that
    are already over (a month in progress still owes most of its bills).
    """
    year = up_to[:4]
    current = month_key(today)
    closed = [m for m in months if m["month"][:4] == year and m["month"] <= up_to and m["month"] < current]
    saved = round(sum(m["balance"] for m in closed if m["balance"] > 0), 2)
    goal = float(goals["annual_fund"])
    pct = saved / goal * 100 if goal > 0 else 0.0
    return {
        "name": goals["fund_name"],
        "note": goals["fund_note"],
        "year": year,
        "goal": goal,
        "saved": saved,
        "pct": round(pct, 1),
        "fill": round(min(pct, 100.0), 1),
        "remaining": round(max(goal - saved, 0.0), 2),
        "months": len(closed),
    }


# ── The month overview ───────────────────────────────────────────────────────

def _empty_month(month: str) -> dict[str, Any]:
    return {"month": month, "transactions": [], "summary": {}, "income": 0.0, "total_expense": 0.0, "balance": 0.0}


def _meter(name: str, spent: float, budget: float, *, can_warn: bool) -> dict[str, Any]:
    pct = max(spent, 0.0) / budget * 100
    if spent > budget + 0.005:
        state = "over"
    elif can_warn and pct >= WARN_AT:
        state = "warn"
    else:
        state = "ok"
    return {
        "name": name,
        "spent": round(spent, 2),
        "budget": budget,
        "pct": round(pct, 1),
        "fill": round(min(pct, 100.0), 1),
        "state": state,
        "remaining": round(budget - spent, 2),
        "over_by": round(spent - budget, 2),
    }


_STATE_ORDER = {"over": 0, "warn": 1, "ok": 2}


def month_overview(
    months: list[dict[str, Any]],
    month: str,
    settings: Mapping[str, Any],
    today: date,
    last_date: str | None,
) -> dict[str, Any]:
    """Everything the home page shows for one month."""
    by_month = {m["month"]: m for m in months}
    data = by_month.get(month) or _empty_month(month)
    summary: dict[str, float] = data["summary"]
    budgets: dict[str, float] = settings["budgets"]
    fixed = set(settings["fixed_categories"])
    goals = settings["goals"]
    current = month_key(today)

    days = days_in_month(month)
    if last_date and last_date >= f"{month}-{days:02d}":
        cutoff_day = days
    elif last_date and last_date[:7] == month:
        cutoff_day = int(last_date[8:10])
    else:
        cutoff_day = 0
    complete = cutoff_day == days
    fraction = cutoff_day / days
    tick = None if complete else round(fraction * 100, 1)

    spent = data["total_expense"]
    fixed_spent = round(sum(v for c, v in summary.items() if c in fixed), 2)
    variable_spent = round(spent - fixed_spent, 2)
    fixed_budget = round(sum(b for c, b in budgets.items() if c in fixed), 2)
    variable_budget = round(sum(b for c, b in budgets.items() if c not in fixed), 2)
    budget_total = round(fixed_budget + variable_budget, 2)

    # Variable categories with a budget, problems first
    meters = [
        _meter(c, summary.get(c, 0.0), b, can_warn=not complete)
        for c, b in budgets.items() if c not in fixed
    ]
    active = [m for m in meters if m["spent"] > 0.005 or m["state"] != "ok"]
    active.sort(key=lambda m: (_STATE_ORDER[m["state"]], -m["pct"], m["name"]))
    untouched = sorted((m for m in meters if m not in active), key=lambda m: -m["budget"])

    # Fixed bills: paid, still pending or over budget. Once the month is over,
    # a bill that came in under budget is simply paid, and one that never came is missing.
    fixed_rows = []
    for c, b in sorted(((c, b) for c, b in budgets.items() if c in fixed), key=lambda cb: -cb[1]):
        paid = summary.get(c, 0.0)
        if paid > b + PAID_TOLERANCE:
            state = "over"
        elif paid >= b - PAID_TOLERANCE or (complete and paid > 0.005):
            state = "paid"
        else:
            state = "missing" if complete else "pending"
        fixed_rows.append({"name": c, "spent": round(paid, 2), "budget": b, "state": state,
                           "over_by": round(paid - b, 2)})
    fixed_pending = round(sum(max(r["budget"] - r["spent"], 0.0) for r in fixed_rows if r["state"] == "pending"), 2)

    review = sum(1 for tx in data["transactions"] if lg.needs_review(tx))
    unbudgeted = sorted(
        ({"name": c, "spent": round(v, 2), "review": review if c == cat.UNCATEGORIZED else 0}
         for c, v in summary.items() if c not in budgets and abs(v) >= 0.005),
        key=lambda r: -r["spent"],
    )

    pace = None
    if variable_budget > 0 and not complete:
        expected = variable_budget * fraction
        diff = expected - variable_spent
        tolerance = variable_budget * PACE_TOLERANCE
        remaining = variable_budget - variable_spent
        days_left = days - cutoff_day
        pace = {
            "expected": round(expected, 2),
            "diff": round(abs(diff), 2),
            "state": "below" if diff > tolerance else "above" if diff < -tolerance else "on",
            "remaining": round(remaining, 2),
            "days_left": days_left,
            "per_day": round(remaining / days_left, 2) if days_left and remaining > 0 else None,
            "pct": round(max(variable_spent, 0.0) / variable_budget * 100, 1),
            "fill": round(min(max(variable_spent, 0.0) / variable_budget * 100, 100.0), 1),
            "over": variable_spent > variable_budget + 0.005,
        }

    saving = None
    if complete:
        saving = {"projected": False, "amount": data["balance"], "income_pending": False}
    elif budgets:
        projected_expense = sum(max(summary.get(c, 0.0), b) for c, b in budgets.items())
        projected_expense += sum(v for c, v in summary.items() if c not in budgets)
        saving = {
            "projected": True,
            "amount": round(data["income"] - projected_expense, 2),
            "income": data["income"],
            "expense": round(projected_expense, 2),
            "income_pending": data["income"] <= 0,
        }
    if saving is not None:
        saving["goal"] = float(goals["monthly_saving"])
        saving["meets"] = saving["amount"] >= saving["goal"]

    spending = sorted(((c, v) for c, v in summary.items() if v > 0.005), key=lambda cv: -cv[1])
    top = spending[0][1] if spending else 0.0
    recent = recent_months(months, month)

    days_since = None
    if last_date:
        days_since = (today - date.fromisoformat(last_date)).days
    first = months[0]["month"] if months else current

    return {
        "month": month,
        "is_current": month == current,
        "prev_month": shift_month(month, -1) if month > first else None,
        "next_month": shift_month(month, 1) if month < current else None,
        "last_date": last_date,
        "cutoff_day": cutoff_day,
        "data_until": f"{month}-{cutoff_day:02d}" if cutoff_day else None,
        "stale": month == current and (days_since is None or days_since > STALE_AFTER_DAYS),
        "days_since": days_since,
        "complete": complete,
        "tick": tick,
        "has_budgets": bool(budgets),
        "spent": spent,
        "income": data["income"],
        "balance": data["balance"],
        "budget_total": budget_total,
        "budget_pct": round(spent / budget_total * 100) if budget_total else None,
        "fixed": {"spent": fixed_spent, "budget": fixed_budget, "pending": fixed_pending, "rows": fixed_rows},
        "variable": {"spent": variable_spent, "budget": variable_budget},
        "pace": pace,
        "meters": active,
        "untouched": untouched,
        "unbudgeted": unbudgeted,
        "spending": [{"name": c, "spent": round(v, 2), "fill": round(v / top * 100, 1)} for c, v in spending],
        "recent_average": round(sum(m["total_expense"] for m in recent) / len(recent), 2) if recent else None,
        "recent_months": [m["month"] for m in recent],
        "review": review,
        "saving": saving,
        "fund": fund_progress(months, goals, month, today),
    }
