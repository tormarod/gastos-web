from services import categorizer as cat
from services import ledger as lg

NOW = "2026-09-24T07:00:00+00:00"


def xlsx(rows):
    return lg.rows_to_transactions(rows, lg.SHARED_ACCOUNT, NOW)


def row(date, concept, amount, balance=None):
    return {"date": date, "concept": concept, "amount": amount, "balance": balance}


def bank_tx(ref, date, concept, amount, balance=None):
    return lg.make_transaction(
        id=f"b:{lg.SHARED_ACCOUNT}:{ref}", account=lg.SHARED_ACCOUNT, date=date, amount=amount,
        concept=concept, source="bank", imported_at=NOW, balance=balance,
    )


def test_reimporting_the_same_statement_adds_nothing():
    ledger = lg.new_ledger()
    rows = [row("2026-09-20", "MERCADONA", -45.3, 954.7), row("2026-09-19", "BAR PEPE", -3.5, 1000.0)]
    first = lg.import_transactions(ledger, xlsx(rows))
    second = lg.import_transactions(ledger, xlsx(rows))
    assert (first.added, second.added, second.duplicates) == (2, 0, 2)
    assert (first.first_date, first.last_date) == ("2026-09-19", "2026-09-20")


def test_overlapping_statements_only_add_new_movements():
    ledger = lg.new_ledger()
    lg.import_transactions(ledger, xlsx([row("2026-08-30", "A", -1, 10), row("2026-09-01", "B", -2, 8)]))
    result = lg.import_transactions(ledger, xlsx([row("2026-09-01", "B", -2, 8), row("2026-09-02", "C", -3, 5)]))
    assert (result.added, result.duplicates) == (1, 1)
    assert [t["concept"] for t in ledger["transactions"]] == ["A", "B", "C"]


def test_identical_rows_in_one_statement_are_both_kept():
    ledger = lg.new_ledger()
    two_coffees = [row("2026-09-20", "CAFE", -1.5), row("2026-09-20", "CAFE", -1.5)]
    assert lg.import_transactions(ledger, xlsx(two_coffees)).added == 2
    assert lg.import_transactions(ledger, xlsx(two_coffees)).added == 0


def test_same_movement_from_excel_and_bank_is_kept_once():
    ledger = lg.new_ledger()
    lg.import_transactions(ledger, xlsx([row("2026-09-20", "MERCADONA VALENCIA", -45.3, 954.7)]))
    result = lg.import_transactions(ledger, [bank_tx("R1", "2026-09-21", "Mercadona", -45.3)])
    assert (result.added, result.duplicates) == (0, 1)
    assert ledger["transactions"][0]["alt_ids"] == ["b:bbva-comun:R1"]
    # Next sync the bank id is already known
    assert lg.import_transactions(ledger, [bank_tx("R1", "2026-09-21", "Mercadona", -45.3)]).duplicates == 1


def test_similar_purchases_on_consecutive_days_are_paired_with_the_right_day():
    ledger = lg.new_ledger()
    lg.import_transactions(ledger, xlsx([row("2026-09-01", "CAFE", -2.5, 100), row("2026-09-02", "CAFE", -2.5, 97.5)]))
    result = lg.import_transactions(ledger, [
        bank_tx("B2", "2026-09-02", "CAFE", -2.5), bank_tx("B1", "2026-09-01", "CAFE", -2.5),
    ])
    assert result.added == 0
    by_date = {t["date"]: t["alt_ids"] for t in ledger["transactions"]}
    assert by_date == {"2026-09-01": ["b:bbva-comun:B1"], "2026-09-02": ["b:bbva-comun:B2"]}


def test_a_second_identical_purchase_from_the_bank_is_not_lost():
    ledger = lg.new_ledger()
    lg.import_transactions(ledger, [bank_tx("A", "2026-09-20", "CAFE", -1.5)])
    result = lg.import_transactions(ledger, [
        bank_tx("A", "2026-09-20", "CAFE", -1.5), bank_tx("B", "2026-09-20", "CAFE", -1.5),
    ])
    assert (result.added, result.duplicates) == (1, 1)


def test_bank_ids_that_change_after_reconnecting_do_not_duplicate():
    ledger = lg.new_ledger()
    lg.import_transactions(ledger, [bank_tx("A", "2026-09-20", "CAFE", -1.5), bank_tx("B", "2026-09-20", "CAFE", -1.5)])
    result = lg.import_transactions(ledger, [bank_tx("C", "2026-09-20", "CAFE", -1.5), bank_tx("D", "2026-09-20", "CAFE", -1.5)])
    assert (result.added, result.duplicates) == (0, 2)


def test_categories_are_assigned_on_import():
    ledger = lg.new_ledger()
    result = lg.import_transactions(
        ledger, xlsx([row("2026-09-20", "BIKI BAT", -30), row("2026-09-21", "COSA RARA", -5)]),
        rules=[{"pattern": "BIKI BAT", "category": "Restaurantes"}],
    )
    assert result.needs_review == 1
    assert [(t["category"], t["category_source"]) for t in ledger["transactions"]] == [
        ("Restaurantes", cat.SOURCE_RULE), (cat.UNCATEGORIZED, cat.SOURCE_NONE),
    ]


def test_summaries_by_month_with_refunds_and_income():
    ledger = lg.new_ledger()
    lg.import_transactions(ledger, xlsx([
        row("2026-08-31", "MERCADONA", -100, 10),
        row("2026-09-01", "NOMINA", 2000, 2010),
        row("2026-09-02", "AMAZON EU", -60, 1950),
        row("2026-09-03", "DEVOLUCION AMAZON", 20, 1970),
        row("2026-09-04", "REINTEGRO CAJERO", -50, 1920),
    ]))
    august, september = lg.month_summaries(ledger)
    assert august["month"] == "2026-08" and august["summary"] == {"Supermercado": 100.0}
    assert september["summary"] == {"Amazon/Online": 40.0, "Efectivo": 50.0}
    assert (september["income"], september["total_expense"], september["balance"]) == (2000.0, 90.0, 1910.0)
    assert [t["date"] for t in september["transactions"]][0] == "2026-09-04"  # newest first


def test_apply_rules_leaves_manual_categories_alone():
    ledger = lg.new_ledger()
    lg.import_transactions(ledger, xlsx([row("2026-09-20", "TIENDA X", -10), row("2026-09-21", "TIENDA X", -11)]))
    manual_id = ledger["transactions"][0]["id"]
    lg.set_category(ledger, manual_id, "Hogar")
    changed = lg.apply_rules(ledger, [{"pattern": "TIENDA X", "category": "Ropa/Accesorios"}])
    assert changed == 1
    assert [t["category"] for t in ledger["transactions"]] == ["Hogar", "Ropa/Accesorios"]
    assert lg.set_category(ledger, "nope", "Hogar") is None


def test_review_groups_and_search():
    ledger = lg.new_ledger()
    lg.import_transactions(ledger, xlsx([
        row("2026-09-20", "COMPRA EN COSA RARA 20/09", -10),
        row("2026-09-21", "COMPRA EN COSA RARA 21/09", -5),
        row("2026-09-22", "ZARA MADRID", -30),
    ]))
    groups = lg.review_groups(ledger)
    assert [(g["merchant"], g["count"], g["total"], g["category"]) for g in groups] == [("COSA RARA", 2, 15.0, "Otros")]
    assert lg.review_count(ledger) == 2
    found = lg.review_groups(ledger, "zara")
    assert [(g["merchant"], g["category"]) for g in found] == [("ZARA", "Ropa/Accesorios")]


def test_delete_month_and_overview():
    ledger = lg.new_ledger()
    lg.import_transactions(ledger, xlsx([row("2026-08-31", "A", -1), row("2026-09-01", "COSA", -2)]))
    assert [m["month"] for m in lg.months_overview(ledger)] == ["2026-09", "2026-08"]
    assert lg.delete_month(ledger, "2026-08") == 1
    assert [m["month"] for m in lg.months_overview(ledger)] == ["2026-09"]


def test_rules_are_normalised_and_replaced():
    rules = lg.upsert_rule([], "  biki  bat ", "Ocio/Cultura", NOW)
    rules = lg.upsert_rule(rules, "BIKI BAT", "Restaurantes", NOW)
    assert [(r["pattern"], r["category"]) for r in rules] == [("BIKI BAT", "Restaurantes")]
    assert lg.delete_rule(rules, "biki bat") == []


def test_iso_date():
    assert lg.iso_date("2026-05-15T00:00:00") == "2026-05-15"
    assert lg.iso_date(None) is None
    assert lg.iso_date("—") is None


def test_rows_without_a_date_stay_in_their_month():
    ledger = lg.new_ledger()
    lg.import_transactions(ledger, xlsx([
        row("2026-09-20", "A", -1, 10), row(None, "SIN FECHA", -2, 8), row("2026-09-18", "B", -3, 5),
    ]))
    (september,) = lg.month_summaries(ledger)
    assert september["month"] == "2026-09" and len(september["transactions"]) == 3
    assert lg.delete_month(ledger, "2026-09") == 3
