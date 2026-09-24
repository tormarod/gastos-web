"""
One-off migration from the v1 month-based files to the v2 ledger.

v1: data.json {"months": {"2026-01": {"transactions": [...], ...}}}
    merchant_rules.json {"rules": [...], "transaction_overrides": [...]}
v2: ledger.json (one list of movements) + rules.json

Nothing is deleted: the v1 files stay in the bucket as a backup.
Every movement is categorised again with the new rules, except those that
were assigned by hand (transaction_overrides), which keep their category.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from services import categorizer as cat
from services import ledger as lg


def migrate_rules(legacy_rules_file: Mapping[str, Any] | None, now: str) -> list[dict[str, str]]:
    rules: list[dict[str, str]] = []
    for r in (legacy_rules_file or {}).get("rules", []):
        if r.get("pattern") and r.get("category"):
            rules = lg.upsert_rule(rules, r["pattern"], r["category"], now)
    return rules


def migrate_ledger(
    legacy_data: Mapping[str, Any],
    legacy_rules_file: Mapping[str, Any] | None,
    rules: list[dict[str, str]],
    now: str,
    account: str = lg.SHARED_ACCOUNT,
) -> tuple[lg.Ledger, dict[str, Any]]:
    """Build the v2 ledger. Returns (ledger, report)."""
    overrides = {
        (str(o.get("date")), o.get("concept"), round(abs(float(o.get("amount", 0))), 2)): o.get("category")
        for o in (legacy_rules_file or {}).get("transaction_overrides", [])
    }
    compiled = cat.compile_rules(rules)
    ledger = lg.new_ledger()
    changes: Counter[str] = Counter()
    overrides_applied = 0

    months = legacy_data.get("months", {})
    for label in sorted(months):
        month = months[label]
        rows = [
            {
                "date": lg.iso_date(t.get("date")),
                "concept": t.get("concept") or "",
                "amount": t.get("amount", 0),
                "balance": t.get("balance"),
            }
            for t in month.get("transactions", [])
        ]
        txs = lg.rows_to_transactions(rows, account, now)
        for tx, old in zip(txs, month.get("transactions", [])):
            if not tx["date"]:
                tx["month_hint"] = label
            old_category = old.get("category") or cat.UNCATEGORIZED
            # v1 overrides were saved with date "—" when the movement had no date
            key_date = str(old.get("date") or "—")
            manual = overrides.get((key_date, old.get("concept"), round(abs(float(old.get("amount", 0))), 2)))
            if manual:
                tx["category"] = manual
                tx["category_source"] = cat.SOURCE_USER
                overrides_applied += 1
            else:
                lg.categorize_transaction(tx, compiled)
            if tx["category"] != old_category:
                changes[f"{old_category} → {tx['category']}"] += 1
        existing = {t["id"] for t in ledger["transactions"]}
        ledger["transactions"].extend(t for t in txs if t["id"] not in existing)

    ledger["transactions"].sort(key=lambda t: (t.get("date") or t.get("month_hint") or "", t["id"]))
    report = {
        "migrated_at": now,
        "months": len(months),
        "transactions": len(ledger["transactions"]),
        "rules": len(rules),
        "overrides_applied": overrides_applied,
        "category_changes": dict(changes.most_common()),
    }
    ledger["meta"]["migration"] = report
    return ledger, report
