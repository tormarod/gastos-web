"""
BBVA XLSX bank statement parser.

BBVA export format (typical):
- Rows 1–7: metadata header (account, owner, date range, balance)
- Row 8: column headers — "F. VALOR", "CONCEPTO", "IMPORTE", "DISPONIBLE"
- Row 9+: transactions

This parser finds the header row dynamically so it's resilient to format changes.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import openpyxl

from services.categorizer import categorize


Transaction = dict[str, Any]


def parse_bbva_xlsx(
    file_bytes: bytes,
    month_label: str,
    custom_rules: list[dict] | None = None,
) -> dict[str, Any]:
    """
    Parse a BBVA XLSX export and return a structured month summary.

    Returns:
        {
            "month": "2026-05",
            "transactions": [...],
            "summary": { category: total, ... },
            "income": float,
            "total_expense": float,
            "balance": float,
        }
    """
    import io
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active
    if ws is None:
        raise ValueError("El fichero Excel no contiene hojas.")

    rows = list(ws.iter_rows(values_only=True))

    # Find the header row by looking for "CONCEPTO" or "F. VALOR"
    header_row_idx = None
    for i, row in enumerate(rows):
        row_text = " ".join(str(c) for c in row if c is not None).upper()
        if "CONCEPTO" in row_text or "F. VALOR" in row_text or "IMPORTE" in row_text:
            header_row_idx = i
            break

    if header_row_idx is None:
        raise ValueError("No se encontró la cabecera del extracto BBVA. ¿Es un fichero BBVA correcto?")

    headers = [str(c).strip().upper() if c is not None else "" for c in rows[header_row_idx]]

    def col(name_fragments: list[str]) -> int | None:
        for i, h in enumerate(headers):
            if any(f in h for f in name_fragments):
                return i
        return None

    date_col   = col(["F. VALOR", "FECHA VALOR", "FECHA"])
    concept_col = col(["CONCEPTO", "DESCRIPCIÓN", "DESCRIPCION"])
    amount_col  = col(["IMPORTE"])
    balance_col = col(["DISPONIBLE", "SALDO"])

    if concept_col is None or amount_col is None:
        raise ValueError("No se encontraron las columnas CONCEPTO e IMPORTE en el extracto.")

    transactions: list[Transaction] = []

    for row in rows[header_row_idx + 1:]:
        # Skip completely empty rows
        if all(c is None for c in row):
            continue

        raw_amount = row[amount_col] if amount_col is not None else None
        if raw_amount is None:
            continue

        # Parse amount — may be a float already or a string like "1.234,56"
        amount = _parse_amount(raw_amount)
        if amount is None:
            continue

        concept = str(row[concept_col] or "").strip()
        if not concept:
            continue

        tx_date: date | None = None
        if date_col is not None and row[date_col] is not None:
            tx_date = _parse_date(row[date_col])

        balance: float | None = None
        if balance_col is not None and row[balance_col] is not None:
            balance = _parse_amount(row[balance_col])

        category = categorize(concept, custom_rules)

        transactions.append({
            "date": tx_date.isoformat() if tx_date else None,
            "concept": concept,
            "amount": round(amount, 2),
            "balance": round(balance, 2) if balance is not None else None,
            "category": category,
        })

    income   = round(sum(t["amount"] for t in transactions if t["amount"] > 0), 2)
    expenses = round(sum(t["amount"] for t in transactions if t["amount"] < 0), 2)

    summary: dict[str, float] = {}
    for t in transactions:
        if t["amount"] < 0:
            cat = t["category"]
            summary[cat] = round(summary.get(cat, 0) + abs(t["amount"]), 2)

    # Sort transactions by date descending
    transactions.sort(key=lambda t: t["date"] or "", reverse=True)

    return {
        "month": month_label,
        "transactions": transactions,
        "summary": summary,
        "income": income,
        "total_expense": abs(expenses),
        "balance": round(income + expenses, 2),
    }


def _parse_amount(val: Any) -> float | None:
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace("\xa0", "").replace(" ", "")
    # Handle Spanish number format: 1.234,56 → 1234.56
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date(val: Any) -> date | None:
    if isinstance(val, date):
        return val
    s = str(val).strip()
    # Try dd/mm/yyyy or dd-mm-yyyy
    m = re.match(r"(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})", s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    # Try yyyy-mm-dd
    m2 = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m2:
        try:
            return date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
        except ValueError:
            pass
    return None
