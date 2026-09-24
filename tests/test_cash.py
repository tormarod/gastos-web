from datetime import date

import pytest

from services import analysis, cash
from services import ledger as lg

NOW = "2026-09-24T08:42:00+00:00"


def ledger_with(*rows):
    """Statement rows (date, concept, amount, balance) imported and categorised."""
    ledger = lg.new_ledger()
    lg.import_transactions(ledger, lg.rows_to_transactions(
        [{"date": d, "concept": c, "amount": a, "balance": b} for d, c, a, b in rows], lg.SHARED_ACCOUNT, NOW,
    ))
    return ledger


def spend(ledger, day, amount, category="Supermercado", place="Frutería"):
    return lg.add_cash(ledger, lg.new_cash_expense(amount=amount, category=category, day=day, place=place, now=NOW))


def month(ledger, key):
    return next(m for m in lg.month_summaries(ledger) if m["month"] == key)


def withdrawal(ledger):
    return next(tx for tx in ledger["transactions"] if cash.is_withdrawal(tx))


def test_a_withdrawal_counts_as_efectivo_until_something_is_written_down():
    ledger = ledger_with(("2026-09-05", "RETIRADA EFECTIVO CAJERO", -100, 900))
    assert month(ledger, "2026-09")["summary"] == {"Efectivo": 100.0}


def test_a_cash_expense_moves_money_from_efectivo_to_its_category():
    ledger = ledger_with(("2026-09-05", "RETIRADA EFECTIVO CAJERO", -100, 900))
    spend(ledger, "2026-09-24", 12.5)
    spend(ledger, "2026-09-20", 6.8, "Cafés y Snacks", "Churrería")
    sep = month(ledger, "2026-09")
    assert sep["summary"] == {"Efectivo": 80.7, "Supermercado": 12.5, "Cafés y Snacks": 6.8}
    assert sep["total_expense"] == 100.0
    assert sep["covered"] == {withdrawal(ledger)["id"]: 19.3}


def test_an_expense_early_in_the_month_comes_from_last_months_withdrawal():
    # The example on the design page: 100 € on 29 August, 30 € at the greengrocer's on 2 September
    ledger = ledger_with(("2026-08-29", "RETIRADA EFECTIVO CAJERO", -100, 900))
    spend(ledger, "2026-09-02", 30)
    assert month(ledger, "2026-08")["summary"] == {"Efectivo": 70.0}
    assert month(ledger, "2026-09")["summary"] == {"Supermercado": 30.0}


def test_the_newest_withdrawal_before_the_expense_goes_first():
    ledger = ledger_with(
        ("2026-08-29", "RETIRADA EFECTIVO CAJERO", -100, 900),
        ("2026-09-15", "RETIRADA EFECTIVO CAJERO", -50, 850),
    )
    spend(ledger, "2026-09-02", 30)   # only August's was there on the 2nd
    spend(ledger, "2026-09-20", 60)   # September's 50, then 10 more from August
    assert month(ledger, "2026-08")["summary"] == {"Efectivo": 60.0}
    assert month(ledger, "2026-09")["summary"] == {"Supermercado": 90.0}


def test_withdrawals_more_than_a_month_old_are_not_used():
    ledger = ledger_with(("2026-08-01", "RETIRADA EFECTIVO CAJERO", -100, 900))
    spend(ledger, "2026-09-02", 30)
    assert month(ledger, "2026-08")["summary"] == {"Efectivo": 100.0}
    assert month(ledger, "2026-09")["summary"] == {"Supermercado": 30.0}


def test_cash_with_no_withdrawal_behind_it_counts_as_new_spending():
    ledger = ledger_with(("2026-09-05", "RETIRADA EFECTIVO CAJERO", -20, 900))
    spend(ledger, "2026-09-10", 50)
    sep = month(ledger, "2026-09")
    assert sep["summary"] == {"Supermercado": 50.0}
    assert sep["total_expense"] == 50.0


def test_a_withdrawal_moved_to_another_category_is_not_cash_any_more():
    ledger = ledger_with(("2026-09-05", "RETIRADA EFECTIVO CAJERO", -100, 900))
    lg.set_category(ledger, withdrawal(ledger)["id"], "Viajes")
    spend(ledger, "2026-09-10", 30)
    assert month(ledger, "2026-09")["summary"] == {"Viajes": 100.0, "Supermercado": 30.0}


def test_in_hand_is_what_the_last_month_of_withdrawals_has_left():
    ledger = ledger_with(
        ("2026-07-30", "RETIRADA EFECTIVO CAJERO", -40, 960),
        ("2026-09-05", "RETIRADA EFECTIVO CAJERO", -100, 900),
    )
    spend(ledger, "2026-09-24", 41.8)
    covered = cash.cover(ledger["transactions"])
    assert cash.in_hand(ledger["transactions"], covered, date(2026, 9, 24)) == 58.2


def test_choices_start_with_the_categories_used_most_in_cash():
    ledger = lg.new_ledger()
    first, rest = cash.choices(ledger["transactions"])
    assert first == cash.DEFAULT_CHOICES
    spend(ledger, "2026-09-01", 5, "Hogar")
    spend(ledger, "2026-09-02", 5, "Hogar")
    spend(ledger, "2026-09-03", 5, "Belleza")
    first, rest = cash.choices(ledger["transactions"])
    assert first[:2] == ["Hogar", "Belleza"] and len(first) == cash.SHOWN_CHOICES
    assert sorted(first + rest) == sorted(cash.CHOICES)
    assert not {"Efectivo", "Ingresos", "Ajustes de cuenta"} & set(first + rest)


def test_month_before_keeps_the_end_of_short_months():
    assert cash.month_before(date(2026, 3, 31)) == date(2026, 2, 28)
    assert cash.month_before(date(2026, 1, 15)) == date(2025, 12, 15)


def test_parse_day():
    today = date(2026, 9, 24)
    assert cash.parse_day("hoy", "", today) == today
    assert cash.parse_day("ayer", "", today) == date(2026, 9, 23)
    assert cash.parse_day("otro", "2026-09-01", today) == date(2026, 9, 1)
    for choice, other in (("otro", "2026-09-25"), ("otro", ""), ("otro", "1999-12-31"), ("mañana", "")):
        with pytest.raises(ValueError):
            cash.parse_day(choice, other, today)


def test_clean_place():
    assert cash.clean_place("  Frutería   Paco ") == "Frutería Paco"
    assert len(cash.clean_place("x" * 100)) == cash.PLACE_MAX


# ── In the ledger ────────────────────────────────────────────────────────────

def test_a_cash_expense_is_a_movement_of_its_own_account():
    ledger = lg.new_ledger()
    tx = spend(ledger, "2026-09-24", 12.5)
    assert tx["id"].startswith("m:efectivo:")
    assert (tx["account"], tx["source"], tx["amount"], tx["category_source"]) == ("efectivo", "manual", -12.5, "user")
    assert (tx["concept"], tx["merchant"], tx["balance"]) == ("Frutería", "Frutería", None)
    same = spend(ledger, "2026-09-24", 12.5)
    assert same["id"] != tx["id"]  # two equal coffees are two expenses
    nameless = spend(ledger, "2026-09-24", 3, place="")
    assert nameless["merchant"] == cash.DEFAULT_NAME


def test_cash_can_be_changed_or_deleted_but_bank_movements_cannot():
    ledger = ledger_with(("2026-09-05", "MERCADONA", -45.3, 954.7))
    bank = ledger["transactions"][0]
    tx = spend(ledger, "2026-09-24", 12.5)
    changed = lg.update_cash(ledger, tx["id"], amount=15, day="2026-09-23", place="Mercado", category="Hogar")
    assert changed is not None
    assert (changed["amount"], changed["date"], changed["merchant"], changed["category"]) == (-15.0, "2026-09-23", "Mercado", "Hogar")
    assert lg.update_cash(ledger, bank["id"], amount=1, day="2026-09-01", place="x", category="Hogar") is None
    assert lg.delete_cash(ledger, bank["id"]) is None
    assert lg.delete_cash(ledger, tx["id"]) is not None
    assert [t["id"] for t in ledger["transactions"]] == [bank["id"]]


def test_rules_and_reset_leave_cash_alone():
    ledger = lg.new_ledger()
    tx = spend(ledger, "2026-09-24", 12.5, "Hogar", "Mercadona")
    lg.apply_rules(ledger, [])
    lg.reset_category(ledger, tx["id"], [])
    assert (tx["category"], tx["category_source"]) == ("Hogar", "user")


def test_deleting_a_month_keeps_what_was_written_down_by_hand():
    ledger = ledger_with(("2026-09-05", "MERCADONA", -45.3, 954.7))
    tx = spend(ledger, "2026-09-24", 12.5)
    assert lg.delete_month(ledger, "2026-09") == 1
    assert ledger["transactions"] == [tx]
    assert lg.months_overview(ledger) == []


def test_reimporting_a_statement_never_takes_cash_for_a_bank_movement():
    rows = [{"date": "2026-09-24", "concept": "MERCADONA", "amount": -12.5, "balance": 900}]
    ledger = lg.new_ledger()
    spend(ledger, "2026-09-24", 12.5)
    result = lg.import_transactions(ledger, lg.rows_to_transactions(rows, lg.SHARED_ACCOUNT, NOW))
    assert (result.added, result.duplicates) == (1, 0)


def test_how_up_to_date_the_bank_is_ignores_cash():
    ledger = ledger_with(("2026-09-05", "MERCADONA", -45.3, 954.7))
    spend(ledger, "2026-09-24", 12.5)
    assert lg.last_date(ledger) == "2026-09-24"
    assert lg.bank_last_date(ledger) == "2026-09-05"


def test_movement_totals_count_withdrawals_only_for_what_is_left():
    ledger = ledger_with(("2026-09-05", "RETIRADA EFECTIVO CAJERO", -100, 900))
    spend(ledger, "2026-09-24", 12.5)
    covered = cash.cover(ledger["transactions"])
    assert lg.summarize(ledger["transactions"], covered)["total_expense"] == 100.0
    assert lg.summarize(ledger["transactions"])["total_expense"] == 112.5


def test_analysis_counts_each_withdrawal_only_for_what_is_left():
    ledger = ledger_with(("2026-08-05", "RETIRADA EFECTIVO CAJERO", -100, 900))
    spend(ledger, "2026-08-10", 30)
    period = analysis.select_period(lg.month_summaries(ledger), "todo", date(2026, 9, 24))
    detail = analysis.category_detail(period, "Efectivo", {}, [])
    assert detail["total"] == 70.0
    assert [m["total"] for m in detail["merchants"]] == [70.0]
