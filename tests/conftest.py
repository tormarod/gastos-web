"""Shared test setup: local storage in a temp folder, fake secrets, helpers."""

from __future__ import annotations

import io
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # templates/ and static/ are resolved relative to the working directory

os.environ.pop("ENV", None)
os.environ["APP_PASSWORD"] = "test-password"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["SYNC_TOKEN"] = "test-sync-token"
os.environ["STORAGE_BACKEND"] = "local"
for name in ("ENABLE_BANKING_APP_ID", "ENABLE_BANKING_PRIVATE_KEY", "ENABLE_BANKING_PRIVATE_KEY_PATH", "PUBLIC_URL"):
    os.environ.pop(name, None)


@pytest.fixture(autouse=True)
def storage_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(data))
    return data


BBVA_HEADER = ["F.Valor", "Fecha", "Concepto", "Movimiento", "Importe", "Divisa", "Disponible", "Divisa", "Observaciones"]


def make_xlsx(rows: Iterable[Iterable[Any]], header: list[str] | None = None, preamble: bool = True) -> bytes:
    """A workbook shaped like a BBVA export: a few metadata rows, the header, the movements."""
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    if preamble:
        ws.append(["Últimos movimientos"])
        ws.append(["Cuenta", "ES12 0182 **** **** 1234"])
        ws.append([])
    ws.append(header or BBVA_HEADER)
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def bbva_row(day: datetime, concept: str, amount: float, balance: float, movement: str = "Pago con tarjeta") -> list[Any]:
    return [day, day, concept, movement, amount, "EUR", balance, "EUR", ""]
