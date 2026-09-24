import json
from datetime import datetime

from services import ledger as lg
from services import parser, repo, storage
from tests.conftest import bbva_row, make_xlsx

LEGACY = {"months": {
    "2026-08": {"month": "2026-08", "summary": {}, "income": 0, "total_expense": 0, "balance": 0, "transactions": [
        {"date": "2026-08-30T00:00:00", "concept": "REINTEGRO CAJERO BBVA", "amount": -50.0, "balance": 950.0, "category": "Ingresos"},
        {"date": "2026-08-29T00:00:00", "concept": "MERCADONA VALENCIA", "amount": -45.3, "balance": 1000.0, "category": "Supermercado"},
        {"date": "2026-08-28T00:00:00", "concept": "BIKI BAT", "amount": -30.0, "balance": 1045.3, "category": "Restaurantes"},
        {"date": "2026-08-27T00:00:00", "concept": "BIZUM ENVIADO A PEPE", "amount": -20.0, "balance": 1075.3, "category": "Otros"},
        {"date": None, "concept": "SIN FECHA", "amount": -5.0, "balance": None, "category": "Otros"},
    ]},
}}
LEGACY_RULES = {
    "rules": [{"pattern": "BIKI BAT", "category": "Restaurantes"}],
    "transaction_overrides": [
        {"date": "2026-08-27T00:00:00", "concept": "BIZUM ENVIADO A PEPE", "amount": 20.0, "category": "Ocio/Cultura"},
        {"date": "—", "concept": "SIN FECHA", "amount": 5.0, "category": "Compras"},
    ],
}


def write_legacy(storage_dir):
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "data.json").write_text(json.dumps(LEGACY))
    (storage_dir / "merchant_rules.json").write_text(json.dumps(LEGACY_RULES))


def test_v1_files_are_migrated_on_first_read(storage_dir):
    write_legacy(storage_dir)
    ledger = repo.load_ledger()

    by_concept = {t["concept"]: t for t in ledger["transactions"]}
    assert by_concept["REINTEGRO CAJERO BBVA"]["category"] == "Efectivo"   # fixed by the new rules
    assert by_concept["REINTEGRO CAJERO BBVA"]["date"] == "2026-08-30"
    assert by_concept["BIKI BAT"]["category_source"] == "rule"
    assert (by_concept["BIZUM ENVIADO A PEPE"]["category"], by_concept["BIZUM ENVIADO A PEPE"]["category_source"]) == ("Ocio/Cultura", "user")
    assert by_concept["SIN FECHA"]["category"] == "Compras"
    assert lg.month_of(by_concept["SIN FECHA"]) == "2026-08"

    report = ledger["meta"]["migration"]
    assert report["transactions"] == 5 and report["overrides_applied"] == 2
    assert report["category_changes"]["Ingresos → Efectivo"] == 1

    # Saved once, v1 files untouched, rules carried over
    assert (storage_dir / "ledger.json").exists()
    assert json.loads((storage_dir / "data.json").read_text()) == LEGACY
    assert [r["pattern"] for r in repo.load_rules()] == ["BIKI BAT"]
    assert storage.read_json("rules.json")[0] is not None


def test_uploading_an_already_migrated_statement_adds_nothing(storage_dir):
    write_legacy(storage_dir)
    repo.load_ledger()
    content = make_xlsx([
        bbva_row(datetime(2026, 8, 30), "REINTEGRO CAJERO BBVA", -50.0, 950.0),
        bbva_row(datetime(2026, 8, 29), "MERCADONA VALENCIA", -45.3, 1000.0),
        bbva_row(datetime(2026, 8, 31), "NUEVO", -1.0, 949.0),
    ])
    incoming = lg.rows_to_transactions(parser.parse_bbva_xlsx(content), lg.SHARED_ACCOUNT, repo.now_iso())
    result = repo.update_ledger(lambda ledger: lg.import_transactions(ledger, incoming, repo.load_rules()))
    assert (result.added, result.duplicates) == (1, 2)


def test_without_v1_data_the_ledger_starts_empty(storage_dir):
    assert repo.load_ledger()["transactions"] == []
    assert not (storage_dir / "ledger.json").exists()
    assert repo.load_rules() == []
