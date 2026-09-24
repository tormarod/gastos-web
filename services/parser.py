"""
BBVA XLSX bank statement parser.

BBVA export format (typical):
- First rows: metadata (account, owner, date range, balance)
- Header row: "F. VALOR", "FECHA", "CONCEPTO", "MOVIMIENTO", "IMPORTE", "DISPONIBLE", ...
- Following rows: one movement each

The header row is found dynamically so small layout changes don't break it.
The parser only reads rows; months, categories and duplicates are handled by
services.ledger when the rows are imported.
"""

from __future__ import annotations

import io
import re
import unicodedata
from datetime import date, datetime
from typing import Any

import openpyxl

Row = dict[str, Any]

# Header fragments, matched against uppercase headers without accents. Columns
# are picked left to right exactly as the first version did, so a statement
# parsed before and after the migration yields the same dates (and ids).
_DATE_HEADERS = ["F. VALOR", "FECHA VALOR", "FECHA"]
_CONCEPT_HEADERS = ["CONCEPTO", "DESCRIPCION"]
_AMOUNT_HEADERS = ["IMPORTE"]
_BALANCE_HEADERS = ["DISPONIBLE", "SALDO"]
_DETAIL_HEADERS = ["MOVIMIENTO", "OBSERVACIONES"]
_HEADER_ROW_HINTS = ["CONCEPTO", "F. VALOR", "IMPORTE", "DESCRIPCION"]


def parse_bbva_xlsx(file_bytes: bytes) -> list[Row]:
    """
    Parse a BBVA XLSX export into rows, in file order:

        {"date": "2026-09-20" | None, "concept": str, "amount": -45.3,
         "balance": 1234.5 | None, "details": str | None}
    """
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:  # openpyxl raises several unrelated types for bad files
        raise ValueError("No se ha podido abrir el fichero. ¿Es un Excel (.xlsx) válido?") from exc
    ws = wb.active
    if ws is None:
        raise ValueError("El fichero Excel no contiene hojas.")

    rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
    header_idx = _find_header_row(rows)
    if header_idx is None:
        raise ValueError("No se encontró la cabecera del extracto BBVA. ¿Es un fichero BBVA correcto?")

    headers = [_norm_header(c) for c in rows[header_idx]]

    def col(fragments: list[str]) -> int | None:
        for i, h in enumerate(headers):
            if any(f in h for f in fragments):
                return i
        return None

    date_col = col(_DATE_HEADERS)
    if date_col is None:  # "F.VALOR" without the space
        date_col = next((i for i, h in enumerate(headers) if re.sub(r"[^A-Z]", "", h) == "FVALOR"), None)
    concept_col = col(_CONCEPT_HEADERS)
    amount_col = col(_AMOUNT_HEADERS)
    balance_col = col(_BALANCE_HEADERS)
    detail_cols = [i for name in _DETAIL_HEADERS if (i := col([name])) is not None]

    if concept_col is None or amount_col is None:
        raise ValueError("No se encontraron las columnas CONCEPTO e IMPORTE en el extracto.")

    parsed: list[Row] = []
    for row in rows[header_idx + 1:]:
        if all(c is None for c in row):
            continue

        amount = _parse_amount(_cell(row, amount_col))
        if amount is None:
            continue

        concept = str(_cell(row, concept_col) or "").strip()
        if not concept:
            continue

        raw_date = _cell(row, date_col)
        tx_date = _parse_date(raw_date) if raw_date is not None else None

        raw_balance = _cell(row, balance_col)
        balance = _parse_amount(raw_balance) if raw_balance is not None else None

        details = " · ".join(
            str(v).strip() for i in detail_cols if (v := _cell(row, i)) not in (None, "")
        ) or None

        parsed.append({
            "date": tx_date.isoformat() if tx_date else None,
            "concept": concept,
            "amount": round(amount, 2),
            "balance": round(balance, 2) if balance is not None else None,
            "details": details,
        })

    if not parsed:
        raise ValueError("El extracto no contiene movimientos.")
    return parsed


def _find_header_row(rows: list[tuple[Any, ...]]) -> int | None:
    """Prefer the first row naming two or more known columns; fall back to one."""
    fallback = None
    for i, row in enumerate(rows):
        names = [_norm_header(c) for c in row if c is not None]
        hits = sum(1 for n in names if any(k in n for k in _HEADER_ROW_HINTS))
        if hits >= 2:
            return i
        if hits == 1 and fallback is None:
            fallback = i
    return fallback


def _norm_header(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).upper().strip()


def _cell(row: tuple[Any, ...], idx: int | None) -> Any:
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _parse_amount(val: Any) -> float | None:
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace("\xa0", "").replace(" ", "").replace("€", "").replace("EUR", "")
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
    if isinstance(val, datetime):
        return val.date()
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
