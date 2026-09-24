"""
The numbers behind Análisis: a period of months, what they cost on average,
fixed against variable spending, saving against the goal, and every category
month by month. Pure functions over ledger.month_summaries() and the settings
view from services.budget.

Averages only use closed months: the month in progress still owes most of its
bills, so charts draw it (lighter) but it never counts in an average.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from services import budget
from services import categorizer as cat

DEFAULT_PERIOD = "12m"
MIN_PREVIOUS = 3       # closed months needed before comparing with the previous period
TOP_MERCHANTS = 4
SPARK_WIDTH = 64
SPARK_HEIGHT = 18

Month = dict[str, Any]


# ── Periods ──────────────────────────────────────────────────────────────────

def period_options(months: list[Month], today: date) -> list[dict[str, str]]:
    current = budget.month_key(today)
    years = sorted({m["month"][:4] for m in months if m["month"] <= current}, reverse=True)
    return ([{"key": DEFAULT_PERIOD, "label": "Últimos 12 meses"}]
            + [{"key": y, "label": y} for y in years]
            + [{"key": "todo", "label": "Todo"}])


def select_period(months: list[Month], key: str | None, today: date) -> dict[str, Any]:
    """The months of a period, which of them are closed, and the previous period to compare with."""
    current = budget.month_key(today)
    known = [m for m in months if m["month"] <= current]
    options = period_options(months, today)
    if key not in {o["key"] for o in options}:
        key = DEFAULT_PERIOD
    assert key is not None
    if key == "todo":
        chosen, previous = known, []
    elif key == DEFAULT_PERIOD:
        start = budget.shift_month(current, -11)
        before = budget.shift_month(start, -12)
        chosen = [m for m in known if m["month"] >= start]
        previous = [m for m in known if before <= m["month"] < start]
    else:
        chosen = [m for m in known if m["month"][:4] == key]
        previous = [m for m in known if m["month"][:4] == str(int(key) - 1)]
    closed = [m for m in chosen if m["month"] < current]
    in_progress = current if chosen and chosen[-1]["month"] == current else None
    return {
        "key": key,
        "label": next(o["label"] for o in options if o["key"] == key),
        "options": options,
        "months": chosen,
        "closed": closed,
        "previous": previous,
        "current": in_progress,
    }


# ── Month by month ───────────────────────────────────────────────────────────

def fixed_spent(month: Month, fixed: Iterable[str]) -> float:
    fixed = set(fixed)
    return round(sum(v for c, v in month["summary"].items() if c in fixed), 2)


def monthly(period: dict[str, Any], fixed: Iterable[str]) -> list[dict[str, Any]]:
    """Fixed and variable spending, income and what was left, for every month of the period."""
    fixed = set(fixed)
    rows = []
    for m in period["months"]:
        f = fixed_spent(m, fixed)
        rows.append({
            "month": m["month"],
            "fixed": f,
            "variable": round(m["total_expense"] - f, 2),
            "total": m["total_expense"],
            "income": m["income"],
            "saving": m["balance"],
            "in_progress": m["month"] == period["current"],
        })
    return rows


def _averages(months: list[Month], fixed: set[str]) -> dict[str, Any] | None:
    n = len(months)
    if not n:
        return None
    spent = sum(m["total_expense"] for m in months) / n
    fixed_avg = sum(fixed_spent(m, fixed) for m in months) / n
    income = sum(m["income"] for m in months) / n
    return {
        "months": n,
        "spent": round(spent, 2),
        "fixed": round(fixed_avg, 2),
        "variable": round(spent - fixed_avg, 2),
        "income": round(income, 2),
        "saving": round(income - spent, 2),
    }


def _change_pct(new: float, old: float) -> int | None:
    return round((new - old) / old * 100) if old else None


def overview(period: dict[str, Any], fixed: Iterable[str], goal: float) -> dict[str, Any] | None:
    """The figures at the top: averages of the closed months, the goal, and changes against the previous period."""
    fixed = set(fixed)
    now = _averages(period["closed"], fixed)
    if now is None:
        return None
    before = _averages(period["previous"], fixed) if len(period["previous"]) >= MIN_PREVIOUS else None
    changes = None
    if before is not None:
        changes = {
            "spent": _change_pct(now["spent"], before["spent"]),
            "income": _change_pct(now["income"], before["income"]),
            "saving": round(now["saving"] - before["saving"], 2),
        }
    return {
        **now,
        "goal": goal,
        "goal_met": sum(1 for m in period["closed"] if m["balance"] >= goal),
        "changes": changes,
    }


# ── Categories ───────────────────────────────────────────────────────────────

def sparkline(values: list[float]) -> list[dict[str, Any]]:
    """Bars for a small inline chart, each on the series' own scale; the last one is highlighted."""
    if not values:
        return []
    top = max(max(values), 0.0) or 1.0
    step = SPARK_WIDTH / len(values)
    width = max(min(4.0, step - 1.5), 1.0)
    bars = []
    for i, v in enumerate(values):
        x = round(i * step + (step - width) / 2, 2)
        if v <= 0:
            bars.append({"x": x, "y": SPARK_HEIGHT - 1, "w": width, "h": 1, "kind": "zero"})
            continue
        h = max(round(SPARK_HEIGHT * v / top, 2), 1.5)
        kind = "last" if i == len(values) - 1 else "bar"
        bars.append({"x": x, "y": round(SPARK_HEIGHT - h, 2), "w": width, "h": h, "kind": kind})
    return bars


def category_rows(period: dict[str, Any], budgets: dict[str, float], fixed: Iterable[str]) -> list[dict[str, Any]]:
    """Every category with spending in the period: average of the closed months, trend and budget."""
    fixed = set(fixed)
    closed = period["closed"]
    names = {c for m in period["months"] for c, v in m["summary"].items() if abs(v) >= 0.005}
    rows = []
    for name in names:
        series = [round(m["summary"].get(name, 0.0), 2) for m in closed]
        average = round(sum(series) / len(series), 2) if series else None
        if average is not None and abs(average) < 0.5:
            continue  # only spent in the month in progress: nothing to average yet
        total = round(sum(m["summary"].get(name, 0.0) for m in period["months"]), 2)
        limit = budgets.get(name)
        rows.append({
            "name": name,
            "average": average,
            "total": total,
            "series": series,
            "spark": sparkline(series),
            "budget": limit,
            "fixed": name in fixed,
            # over only when it shows: an average of 450,30 € reads as 450 €
            "over": limit is not None and average is not None and average >= limit + 0.5,
        })
    rows.sort(key=lambda r: (-(r["average"] if r["average"] is not None else r["total"]), r["name"]))
    return rows


def category_detail(
    period: dict[str, Any], name: str, budgets: dict[str, float], fixed: Iterable[str],
) -> dict[str, Any]:
    """One category month by month against its budget, and the merchants where it goes."""
    closed = period["closed"]
    values = [
        {"month": m["month"], "value": round(m["summary"].get(name, 0.0), 2), "in_progress": m["month"] == period["current"]}
        for m in period["months"]
    ]
    closed_values = [m["summary"].get(name, 0.0) for m in closed]
    limit = budgets.get(name)

    groups: dict[str, dict[str, Any]] = {}
    for m in period["months"]:
        for tx in m["transactions"]:
            if (tx.get("category") or cat.UNCATEGORIZED) != name:
                continue
            key = tx.get("merchant") or cat.merchant_key(tx.get("concept") or "")
            g = groups.setdefault(key, {"merchant": key, "count": 0, "total": 0.0})
            g["count"] += 1
            g["total"] -= float(tx["amount"])  # spending as a positive amount; refunds reduce it
    ordered = sorted(groups.values(), key=lambda g: (-g["total"], g["merchant"]))
    top, rest = ordered[:TOP_MERCHANTS], ordered[TOP_MERCHANTS:]
    widest = max((g["total"] for g in top), default=0.0)
    merchants = [
        {"merchant": g["merchant"], "count": g["count"], "total": round(g["total"], 2),
         "fill": round(max(g["total"], 0.0) / widest * 100, 1) if widest > 0 else 0.0}
        for g in top
    ]
    others = None
    if rest:
        others = {"places": len(rest), "count": sum(g["count"] for g in rest),
                  "total": round(sum(g["total"] for g in rest), 2)}

    return {
        "name": name,
        "values": values,
        "average": round(sum(closed_values) / len(closed_values), 2) if closed_values else None,
        "budget": limit,
        "closed": len(closed_values),
        "months_over": sum(1 for v in closed_values if v > limit + 0.005) if limit is not None else None,
        "total": round(sum(v["value"] for v in values), 2),
        "count": sum(g["count"] for g in groups.values()),
        "merchants": merchants,
        "others": others,
        "fixed": name in set(fixed),
    }
