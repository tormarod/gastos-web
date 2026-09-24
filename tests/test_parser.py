from datetime import date, datetime

import pytest

from services.parser import _parse_amount, _parse_date, parse_bbva_xlsx
from tests.conftest import bbva_row, make_xlsx


def test_parses_a_bbva_export():
    content = make_xlsx([
        bbva_row(datetime(2026, 9, 20), "MERCADONA VALENCIA", -45.3, 954.7),
        bbva_row(datetime(2026, 9, 1), "Rodrigo", 1000, 1000.0, movement="Transferencia recibida"),
    ])
    rows = parse_bbva_xlsx(content)
    assert rows == [
        {"date": "2026-09-20", "concept": "MERCADONA VALENCIA", "amount": -45.3, "balance": 954.7,
         "details": "Pago con tarjeta"},
        {"date": "2026-09-01", "concept": "Rodrigo", "amount": 1000.0, "balance": 1000.0,
         "details": "Transferencia recibida"},
    ]


def test_older_header_names_and_text_cells():
    content = make_xlsx(
        [["15/05/2026", "BAR PEPE", "-1.234,56", "10.000,00"]],
        header=["F. VALOR", "CONCEPTO", "IMPORTE", "DISPONIBLE"],
    )
    assert parse_bbva_xlsx(content) == [
        {"date": "2026-05-15", "concept": "BAR PEPE", "amount": -1234.56, "balance": 10000.0, "details": None},
    ]


def test_skips_blank_and_incomplete_rows():
    content = make_xlsx([
        bbva_row(datetime(2026, 9, 20), "OK", -1, 1),
        [None] * 9,
        [datetime(2026, 9, 19), None, "SIN IMPORTE", None, None, None, None, None, None],
        [datetime(2026, 9, 18), None, "", None, -5, None, None, None, None],
    ])
    assert [r["concept"] for r in parse_bbva_xlsx(content)] == ["OK"]


def test_a_title_mentioning_importe_is_not_the_header():
    content = make_xlsx(
        [["Importe total del periodo"], ["F. VALOR", "CONCEPTO", "IMPORTE"], ["01/09/2026", "BAR", "-3,50"]],
        header=["Extracto"], preamble=False,
    )
    assert parse_bbva_xlsx(content)[0]["amount"] == -3.5


def test_rejects_files_that_are_not_bbva_statements():
    with pytest.raises(ValueError, match="cabecera"):
        parse_bbva_xlsx(make_xlsx([["a", "b"]], header=["Nombre", "Apellidos"], preamble=False))
    with pytest.raises(ValueError, match="Excel"):
        parse_bbva_xlsx(b"esto no es un excel")
    with pytest.raises(ValueError, match="no contiene movimientos"):
        parse_bbva_xlsx(make_xlsx([]))


@pytest.mark.parametrize("raw, expected", [
    (-12.5, -12.5), ("1.234,56", 1234.56), ("-12,5", -12.5), ("3,00 €", 3.0),
    ("1234.5", 1234.5), ("abc", None), (True, None),
])
def test_parse_amount(raw, expected):
    assert _parse_amount(raw) == expected


def test_parse_date():
    assert _parse_date(datetime(2026, 9, 20, 13, 5)) == date(2026, 9, 20)
    assert _parse_date("20-09-2026") == date(2026, 9, 20)
    assert _parse_date("2026-09-20") == date(2026, 9, 20)
    assert _parse_date("ayer") is None


def test_date_column_is_chosen_like_the_first_version():
    # "F.Valor" (no space) never matched in v1, so the booking date ("Fecha") was used.
    # Keeping that choice means statements migrated from v1 produce the same ids.
    content = make_xlsx([[datetime(2026, 9, 21), datetime(2026, 9, 20), "BAR", "Pago", -3, "EUR", 10, "EUR", ""]])
    assert parse_bbva_xlsx(content)[0]["date"] == "2026-09-20"
    only_value_date = make_xlsx([[datetime(2026, 9, 21), "BAR", -3]], header=["F.Valor", "Concepto", "Importe"])
    assert parse_bbva_xlsx(only_value_date)[0]["date"] == "2026-09-21"


def test_metadata_rows_are_not_taken_for_the_header():
    content = make_xlsx(
        [["Fecha de consulta", "24/09/2026", "Saldo", "1.000,00"],
         ["F. VALOR", "CONCEPTO", "IMPORTE", "SALDO"],
         ["01/09/2026", "BAR", "-3,50", "996,50"]],
        header=["Extracto"], preamble=False,
    )
    assert parse_bbva_xlsx(content)[0]["balance"] == 996.5
