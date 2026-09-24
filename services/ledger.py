"""
The ledger: every movement of the account, stored once.

S3 `ledger.json`:
{
  "version": 2,
  "transactions": [
    {"id": "x:bbva-comun:3f2a…", "account": "bbva-comun", "date": "2026-09-20",
     "amount": -45.3, "concept": "MERCADONA VALENCIA", "counterparty": null,
     "details": null, "balance": 1234.5, "mcc": null, "merchant": "MERCADONA",
     "category": "Supermercado", "category_source": "keyword",
     "source": "xlsx", "imported_at": "2026-09-24T07:00:00+00:00"}
  ],
  "meta": {}
}

Ids start with the source: "x:" for Excel imports; other sources (such as
a future bank feed) use their own prefix. The same movement arriving under
another id (from another source, or from a source that renumbers) is
detected and kept once; the second id is remembered in "alt_ids" so it is
skipped next time.

Months are not stored: they are computed from the movement dates.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from services import categorizer as cat

Transaction = dict[str, Any]
Ledger = dict[str, Any]

LEDGER_VERSION = 2
SHARED_ACCOUNT = "bbva-comun"
MATCH_WINDOW_DAYS = 3


def new_ledger() -> Ledger:
    return {"version": LEDGER_VERSION, "transactions": [], "meta": {}}


def iso_date(value: Any) -> str | None:
    """'2026-09-20', a date, a datetime or '2026-09-20T00:00:00' → '2026-09-20'."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-" and s[:4].isdigit():
        return s[:10]
    return None


def make_transaction(
    *,
    id: str,
    account: str,
    date: str | None,
    amount: float,
    concept: str,
    source: str,
    imported_at: str,
    balance: float | None = None,
    details: str | None = None,
    counterparty: str | None = None,
    mcc: str | None = None,
) -> Transaction:
    return {
        "id": id,
        "account": account,
        "date": iso_date(date),
        "amount": round(float(amount), 2),
        "concept": concept,
        "counterparty": counterparty or None,
        "details": details or None,
        "balance": round(float(balance), 2) if balance is not None else None,
        "mcc": mcc or None,
        "merchant": cat.merchant_key(concept, counterparty),
        "category": cat.UNCATEGORIZED,
        "category_source": cat.SOURCE_NONE,
        "source": source,
        "imported_at": imported_at,
    }


def _row_key(account: str, row: Mapping[str, Any]) -> str:
    amount = f"{float(row['amount']):.2f}"
    if row.get("balance") is not None:
        return f"{account}|{row.get('date')}|{amount}|{float(row['balance']):.2f}"
    return f"{account}|{row.get('date')}|{amount}|{cat.normalize(row.get('concept'))}"


def statement_row_id(account: str, row: Mapping[str, Any], occurrence: int) -> str:
    digest = hashlib.sha1(f"{_row_key(account, row)}|{occurrence}".encode()).hexdigest()[:20]
    return f"x:{account}:{digest}"


def rows_to_transactions(
    rows: Iterable[Mapping[str, Any]], account: str, imported_at: str
) -> list[Transaction]:
    """
    Turn parsed statement rows into ledger movements with stable ids, so that
    uploading the same (or an overlapping) statement again adds nothing.
    Identical rows within one file get an occurrence number.
    """
    seen: dict[str, int] = defaultdict(int)
    out: list[Transaction] = []
    for raw in rows:
        row = {**raw, "date": iso_date(raw.get("date"))}
        key = _row_key(account, row)
        occurrence = seen[key]
        seen[key] += 1
        out.append(make_transaction(
            id=statement_row_id(account, row, occurrence),
            account=account,
            date=row["date"],
            amount=row["amount"],
            concept=row["concept"],
            balance=row.get("balance"),
            details=row.get("details"),
            source="xlsx",
            imported_at=imported_at,
        ))

    # A row whose date can't be read belongs to the month of its neighbours,
    # so it still shows up in that month (and can be deleted with it).
    dated = [(i, tx["date"][:7]) for i, tx in enumerate(out) if tx["date"]]
    for i, tx in enumerate(out):
        if not tx["date"] and dated:
            before = [m for j, m in dated if j < i]
            tx["month_hint"] = before[-1] if before else dated[0][1]
    return out


# ── Categories ───────────────────────────────────────────────────────────────

def needs_review(tx: Mapping[str, Any]) -> bool:
    return tx.get("category_source") == cat.SOURCE_NONE


def categorize_transaction(tx: Transaction, rules: cat.RuleTuple) -> None:
    """Set the automatic category. Movements categorised by hand are left alone."""
    if tx.get("category_source") == cat.SOURCE_USER:
        return
    result = cat.classify(
        tx.get("concept") or "",
        float(tx["amount"]),
        rules,
        counterparty=tx.get("counterparty"),
        details=tx.get("details"),
        mcc=tx.get("mcc"),
        merchant=tx.get("merchant"),
    )
    tx["category"] = result.category
    tx["category_source"] = result.source


def apply_rules(ledger: Ledger, rules: Iterable[Mapping[str, str]]) -> int:
    """Re-run categorisation on every movement. Returns how many changed category."""
    compiled = cat.compile_rules(rules)
    changed = 0
    for tx in ledger["transactions"]:
        before = tx.get("category")
        categorize_transaction(tx, compiled)
        if tx.get("category") != before:
            changed += 1
    return changed


def set_category(ledger: Ledger, tx_id: str, category: str) -> Transaction | None:
    """Categorise one movement by hand; later rule changes won't touch it."""
    for tx in ledger["transactions"]:
        if tx["id"] == tx_id:
            tx["category"] = category
            tx["category_source"] = cat.SOURCE_USER
            return tx
    return None


def upsert_rule(rules: list[dict[str, str]], pattern: str, category: str, now: str) -> list[dict[str, str]]:
    normalized = cat.normalize(pattern)
    kept = [r for r in rules if cat.normalize(r.get("pattern")) != normalized]
    kept.append({"pattern": normalized, "category": category, "created_at": now})
    return kept


def delete_rule(rules: list[dict[str, str]], pattern: str) -> list[dict[str, str]]:
    normalized = cat.normalize(pattern)
    return [r for r in rules if cat.normalize(r.get("pattern")) != normalized]


# ── Import ───────────────────────────────────────────────────────────────────

@dataclass
class ImportResult:
    added: int = 0
    duplicates: int = 0
    needs_review: int = 0
    first_date: str | None = None
    last_date: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _id_kind(tx_id: str) -> str:
    return tx_id.split(":", 1)[0]


def _to_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _cents(amount: Any) -> int:
    return round(float(amount) * 100)


def _balances(a: Mapping[str, Any], b: Mapping[str, Any]) -> str:
    if a.get("balance") is None or b.get("balance") is None:
        return "unknown"
    return "same" if abs(float(a["balance"]) - float(b["balance"])) < 0.005 else "different"


def _match_existing(
    existing: list[Transaction], fresh: list[Transaction], claimed: set[str]
) -> dict[int, Transaction]:
    """
    Pair new movements with stored ones that are the same movement under a
    different id. Returns {index in fresh: stored movement}.

    - From another source (e.g. Excel vs a bank feed): same account and amount, dates at
      most MATCH_WINDOW_DAYS apart. Matching balances and closer dates pair first.
    - From the same source (e.g. a bank feed that renumbered its ids):
      same date, amount and concept, and balances not contradicting.

    Stored movements already seen by id in this batch (`claimed`) are not
    available, so a second identical coffee on the same day is not lost.
    """
    by_key: dict[tuple[str, int], list[Transaction]] = defaultdict(list)
    for tx in existing:
        if tx.get("date") and tx["id"] not in claimed:
            by_key[(tx["account"], _cents(tx["amount"]))].append(tx)

    pairs: list[tuple[int, int, int, Transaction]] = []
    for i, tx in enumerate(fresh):
        tx_date = _to_date(tx.get("date"))
        if tx_date is None:
            continue
        kind = _id_kind(tx["id"])
        for ex in by_key.get((tx["account"], _cents(tx["amount"])), ()):
            ex_date = _to_date(ex.get("date"))
            if ex_date is None:
                continue
            gap = abs((ex_date - tx_date).days)
            balances = _balances(tx, ex)
            kinds = {_id_kind(ex["id"]), *(_id_kind(a) for a in ex.get("alt_ids", ()))}
            if kind in kinds:
                same = (
                    gap == 0
                    and balances != "different"
                    and cat.normalize(tx.get("concept")) == cat.normalize(ex.get("concept"))
                )
                if same:
                    pairs.append((0, 0, i, ex))
            elif gap <= MATCH_WINDOW_DAYS:
                pairs.append((0 if balances == "same" else 1, gap, i, ex))

    pairs.sort(key=lambda p: (p[0], p[1]))
    matched: dict[int, Transaction] = {}
    used: set[str] = set()
    for _, _, i, ex in pairs:
        if i in matched or ex["id"] in used:
            continue
        matched[i] = ex
        used.add(ex["id"])
    return matched


def import_transactions(
    ledger: Ledger,
    incoming: Iterable[Transaction],
    rules: Iterable[Mapping[str, str]] | None = None,
) -> ImportResult:
    """Add new movements, skipping any already stored. Mutates the ledger."""
    compiled = cat.compile_rules(rules)
    txs: list[Transaction] = ledger["transactions"]
    owner_of: dict[str, str] = {}
    for tx in txs:
        owner_of[tx["id"]] = tx["id"]
        for alt in tx.get("alt_ids", ()):
            owner_of[alt] = tx["id"]
    result = ImportResult()

    fresh: list[Transaction] = []
    claimed: set[str] = set()
    seen_in_batch: set[str] = set()
    for tx in incoming:
        if tx["id"] in owner_of:
            claimed.add(owner_of[tx["id"]])
            result.duplicates += 1
            continue
        if tx["id"] in seen_in_batch:
            result.duplicates += 1
            continue
        seen_in_batch.add(tx["id"])
        fresh.append(tx)

    matched = _match_existing(txs, fresh, claimed)
    for i, tx in enumerate(fresh):
        existing = matched.get(i)
        if existing is not None:
            existing.setdefault("alt_ids", []).append(tx["id"])
            # The other source may know more (bank MCC, counterparty); keep the category decisions.
            for key in ("mcc", "counterparty"):
                if not existing.get(key) and tx.get(key):
                    existing[key] = tx[key]
            if needs_review(existing):
                categorize_transaction(existing, compiled)
            result.duplicates += 1
            continue

        categorize_transaction(tx, compiled)
        txs.append(tx)
        result.added += 1
        if needs_review(tx):
            result.needs_review += 1
        if tx.get("date"):
            if result.first_date is None or tx["date"] < result.first_date:
                result.first_date = tx["date"]
            if result.last_date is None or tx["date"] > result.last_date:
                result.last_date = tx["date"]

    txs.sort(key=lambda t: (t.get("date") or "", t["id"]))
    return result


# ── Reading ──────────────────────────────────────────────────────────────────

def month_of(tx: Mapping[str, Any]) -> str | None:
    if tx.get("date"):
        return str(tx["date"])[:7]
    return tx.get("month_hint")


def summarize(transactions: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """
    Spending per category (refunds reduce their category), income and balance.
    Only "Ingresos" counts as income; everything else is spending.
    """
    spending: dict[str, float] = defaultdict(float)
    income = 0.0
    for tx in transactions:
        amount = float(tx["amount"])
        category = tx.get("category") or cat.UNCATEGORIZED
        if category == cat.INCOME:
            income += amount
        else:
            spending[category] -= amount
    summary = {k: round(v, 2) for k, v in spending.items() if abs(v) >= 0.005}
    total_expense = round(sum(summary.values()), 2)
    income = round(income, 2)
    return {
        "summary": summary,
        "income": income,
        "total_expense": total_expense,
        "balance": round(income - total_expense, 2),
    }


def _newest_first(txs: Iterable[Transaction]) -> list[Transaction]:
    return sorted(txs, key=lambda t: (t.get("date") or "", t["id"]), reverse=True)


def month_summaries(ledger: Ledger) -> list[dict[str, Any]]:
    """One entry per month, oldest first, shaped like the old data.json months."""
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for tx in ledger["transactions"]:
        month = month_of(tx)
        if month:
            groups[month].append(tx)
    return [
        {"month": month, "transactions": _newest_first(groups[month]), **summarize(groups[month])}
        for month in sorted(groups)
    ]


def months_overview(ledger: Ledger) -> list[dict[str, Any]]:
    """Newest month first, with counts, for the import page."""
    out = []
    for m in reversed(month_summaries(ledger)):
        out.append({
            "month": m["month"],
            "count": len(m["transactions"]),
            "income": m["income"],
            "total_expense": m["total_expense"],
            "needs_review": sum(1 for tx in m["transactions"] if needs_review(tx)),
        })
    return out


def review_count(ledger: Ledger) -> int:
    return sum(1 for tx in ledger["transactions"] if needs_review(tx))


def review_groups(ledger: Ledger, query: str | None = None, limit: int = 150) -> list[dict[str, Any]]:
    """
    Movements grouped by merchant, biggest first. Without a query, only those
    needing review; with one, every movement whose text contains it.
    """
    q = cat.normalize(query)
    if q:
        candidates = [
            tx for tx in ledger["transactions"]
            if q in cat.normalize(" ".join(
                str(tx.get(k) or "") for k in ("concept", "counterparty", "merchant", "details")
            ))
        ]
    else:
        candidates = [tx for tx in ledger["transactions"] if needs_review(tx)]

    groups: dict[str, dict[str, Any]] = {}
    for tx in candidates:
        key = tx.get("merchant") or cat.merchant_key(tx.get("concept") or "")
        g = groups.setdefault(key, {
            "merchant": key, "pattern": key, "count": 0, "total": 0.0,
            "transactions": [], "categories": Counter(),
        })
        g["count"] += 1
        g["total"] += abs(float(tx["amount"]))
        g["transactions"].append(tx)
        g["categories"][tx.get("category") or cat.UNCATEGORIZED] += 1

    out = []
    for g in groups.values():
        g["transactions"] = _newest_first(g["transactions"])
        g["total"] = round(g["total"], 2)
        g["category"] = g.pop("categories").most_common(1)[0][0]
        g["example"] = g["transactions"][0].get("concept") or g["merchant"]
        out.append(g)
    out.sort(key=lambda g: (-g["total"], g["merchant"]))
    return out[:limit]


def delete_month(ledger: Ledger, month: str) -> int:
    before = len(ledger["transactions"])
    ledger["transactions"] = [tx for tx in ledger["transactions"] if month_of(tx) != month]
    return before - len(ledger["transactions"])
