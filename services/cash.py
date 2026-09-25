"""
Cash: expenses paid with notes and coins, written down by hand, and how they
fit with the cash machine.

Taking money out of a cash machine already counts as spending in "Efectivo".
A cash expense written down by hand counts in its own category on its own day,
and is taken from the newest withdrawal made on or before that day (then from
older ones, up to one month back). That part of the withdrawal stops counting
as Efectivo, so no money is counted twice: writing an expense down only moves
it from Efectivo to where it really went. An expense no withdrawal can cover
(a note someone gave you) counts as new spending.

Nothing here is stored: the split is worked out from the ledger every time, so
changing, deleting or uploading anything puts every euro back in its place.

Pure functions; the ledger module uses them for the monthly summaries.
"""

from __future__ import annotations

import calendar
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Any

from services import categorizer as cat

CASH_ACCOUNT = "efectivo"
MANUAL_SOURCE = "manual"
ID_PREFIX = "m"
CASH_CATEGORY = "Efectivo"
CASH_DETAILS = "Pago en efectivo"
DEFAULT_NAME = "Gasto en efectivo"
MAX_AMOUNT = 10_000.0
PLACE_MAX = 60
FIRST_DAY = date(2000, 1, 1)
SHOWN_CHOICES = 7       # categories shown first in the form; the rest go under "Más"
RECENT_SHOWN = 5

# What cash can be spent on: every spending category except the cash machine
# itself, income and account adjustments.
CHOICES: list[str] = [c for c in cat.CATEGORIES if c not in {CASH_CATEGORY, cat.INCOME, cat.ADJUSTMENTS}]
DEFAULT_CHOICES = [
    "Supermercado", "Cafés y Snacks", "Restaurantes", "Ocio/Cultura",
    "Transporte privado", "Belleza", "Salud",
]

Transaction = Mapping[str, Any]


def is_cash(tx: Transaction) -> bool:
    """Written down by hand, paid in cash."""
    return tx.get("account") == CASH_ACCOUNT


def is_withdrawal(tx: Transaction) -> bool:
    """Money taken out of the bank account as cash."""
    return (not is_cash(tx) and tx.get("category") == CASH_CATEGORY
            and float(tx["amount"]) < 0 and bool(tx.get("date")))


def _is_cash_expense(tx: Transaction) -> bool:
    return (is_cash(tx) and float(tx["amount"]) < 0 and bool(tx.get("date"))
            and tx.get("category") not in {CASH_CATEGORY, cat.INCOME})


def month_before(day: date) -> date:
    """The same day one month earlier (31 March → 28 or 29 February)."""
    year, month = (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def cover(transactions: Iterable[Transaction]) -> dict[str, float]:
    """
    How much of each withdrawal is already written down as cash expenses:
    {withdrawal id: euros}. Expenses are taken oldest first, each from the
    newest withdrawal on or before its day, going back at most one month.
    """
    txs = list(transactions)
    withdrawals = sorted((tx for tx in txs if is_withdrawal(tx)), key=lambda t: (t["date"], t["id"]), reverse=True)
    left = {tx["id"]: round(-float(tx["amount"]), 2) for tx in withdrawals}
    covered: dict[str, float] = {}
    for expense in sorted((tx for tx in txs if _is_cash_expense(tx)), key=lambda t: (t["date"], t["id"])):
        need = round(-float(expense["amount"]), 2)
        day = str(expense["date"])[:10]
        start = month_before(date.fromisoformat(day)).isoformat()
        for w in withdrawals:
            if need <= 0:
                break
            if w["date"] > day or left[w["id"]] <= 0:
                continue
            if w["date"] < start:
                break  # newest first: every other one is older still
            take = round(min(need, left[w["id"]]), 2)
            left[w["id"]] = round(left[w["id"]] - take, 2)
            covered[w["id"]] = round(covered.get(w["id"], 0.0) + take, 2)
            need = round(need - take, 2)
    return covered


def uncovered(tx: Transaction, covered: Mapping[str, float]) -> float:
    """The amount of a movement as it counts: a withdrawal only for what isn't written down yet."""
    amount = float(tx["amount"])
    if tx.get("id") in covered:
        return round(amount + covered[tx["id"]], 2)
    return amount


def in_hand(transactions: Iterable[Transaction], covered: Mapping[str, float], today: date) -> float:
    """Cash taken out in the last month and not written down yet: what a new expense can still come from."""
    start, end = month_before(today).isoformat(), today.isoformat()
    return round(sum(
        -uncovered(tx, covered) for tx in transactions
        if is_withdrawal(tx) and start <= tx["date"] <= end
    ), 2)


def choices(transactions: Iterable[Transaction], limit: int = SHOWN_CHOICES) -> tuple[list[str], list[str]]:
    """
    The categories to show first in the form (the ones you use most in cash,
    then the usual ones) and the rest, in their normal order.
    """
    order = {c: i for i, c in enumerate(CHOICES)}
    used = Counter(str(tx.get("category")) for tx in transactions if _is_cash_expense(tx))
    first = [c for c, _ in sorted(used.items(), key=lambda cn: (-cn[1], order.get(cn[0], 99))) if c in order][:limit]
    for c in DEFAULT_CHOICES:
        if len(first) >= limit:
            break
        if c not in first:
            first.append(c)
    return first, [c for c in CHOICES if c not in first]


def recent(transactions: Iterable[Transaction], limit: int = RECENT_SHOWN) -> list[Transaction]:
    """The last cash expenses written down, newest first."""
    mine = [tx for tx in transactions if is_cash(tx)]
    return sorted(mine, key=lambda t: (t.get("imported_at") or "", t.get("date") or "", t["id"]), reverse=True)[:limit]


# ── Form input ───────────────────────────────────────────────────────────────

def parse_day(choice: str | None, other: str | None, today: date) -> date:
    """'hoy', 'ayer' or 'otro' with a date → the day of the expense. ValueError if not valid."""
    if choice in (None, "", "hoy"):
        return today
    if choice == "ayer":
        return today - timedelta(days=1)
    if choice != "otro":
        raise ValueError(choice)
    day = date.fromisoformat((other or "").strip())
    if day > today or day < FIRST_DAY:
        raise ValueError(other)
    return day


def clean_place(text: str | None) -> str:
    """Where the money went, as typed: single spaces, at most PLACE_MAX characters."""
    return " ".join((text or "").split())[:PLACE_MAX].strip()
