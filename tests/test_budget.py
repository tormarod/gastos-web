from datetime import date

import pytest

from services import budget
from services import ledger as lg

TODAY = date(2026, 9, 24)

FIXED = {"Alquiler": 850.0, "Suministros": 120.0, "Seguros": 75.0, "Telefonía": 45.0}
VARIABLE = {
    "Supermercado": 450.0, "Restaurantes": 180.0, "Delivery": 60.0, "Cafés y Snacks": 40.0,
    "Transporte público": 50.0, "Gasolinera": 90.0, "Ocio/Cultura": 80.0, "Amazon/Online": 60.0,
    "Hogar": 60.0, "Salud": 40.0, "Ropa/Accesorios": 60.0, "Belleza": 40.0,
}
SEPTEMBER_SPENDING = {
    "Alquiler": 850.0, "Suministros": 120.0, "Seguros": 75.0, "Telefonía": 45.0,
    "Supermercado": 288.0, "Restaurantes": 164.0, "Delivery": 68.0, "Cafés y Snacks": 21.0,
    "Transporte público": 40.0, "Gasolinera": 46.0, "Ocio/Cultura": 35.0, "Amazon/Online": 18.0,
    "Salud": 12.0, "Otros": 42.0, "Compras": 18.0,
}


def month(key, summary, income=0.0, transactions=None):
    total = round(sum(summary.values()), 2)
    return {"month": key, "transactions": transactions or [], "summary": summary,
            "income": income, "total_expense": total, "balance": round(income - total, 2)}


def settings(budgets=None, **goals):
    return budget.settings_view({"budgets": budgets if budgets is not None else {**FIXED, **VARIABLE},
                                 "goals": goals})


MONTHS = [
    month("2025-12", {"Supermercado": 400.0}, income=700.0),     # other year: not in the 2026 fund
    month("2026-06", {"Supermercado": 430.0, "Alquiler": 850.0}, income=1780.0),  # +500
    month("2026-07", {"Supermercado": 460.0, "Alquiler": 850.0}, income=1950.0),  # +640
    month("2026-08", {"Supermercado": 433.0, "Alquiler": 850.0}, income=1783.0),  # +500
    month("2026-09", SEPTEMBER_SPENDING, income=2600.0),
]


def test_mid_month_overview_matches_the_design():
    view = budget.month_overview(MONTHS, "2026-09", settings(), TODAY, "2026-09-22")

    assert (view["spent"], view["budget_total"], view["budget_pct"]) == (1842.0, 2300.0, 80)
    assert view["fixed"]["spent"] == 1090.0 and view["fixed"]["pending"] == 0
    assert view["variable"] == {"spent": 752.0, "budget": 1210.0}
    assert (view["cutoff_day"], view["complete"], view["tick"]) == (22, False, 73.3)

    pace = view["pace"]
    assert pace["state"] == "below" and pace["diff"] == 135.33
    assert (pace["remaining"], pace["days_left"], pace["per_day"]) == (458.0, 8, 57.25)

    order = [(m["name"], m["state"]) for m in view["meters"]]
    assert order[:3] == [("Delivery", "over"), ("Restaurantes", "warn"), ("Transporte público", "ok")]
    assert [m["name"] for m in view["meters"]][-2:] == ["Amazon/Online", "Salud"]
    delivery = view["meters"][0]
    assert (delivery["fill"], delivery["over_by"]) == (100.0, 8.0)
    assert [m["name"] for m in view["untouched"]] == ["Hogar", "Ropa/Accesorios", "Belleza"]
    assert [r["state"] for r in view["fixed"]["rows"]] == ["paid"] * 4
    assert [(r["name"], r["spent"]) for r in view["unbudgeted"]] == [("Otros", 42.0), ("Compras", 18.0)]

    saving = view["saving"]
    assert (saving["projected"], saving["expense"], saving["amount"], saving["meets"]) == (True, 2368.0, 232.0, True)

    fund = view["fund"]
    assert (fund["year"], fund["saved"], fund["goal"], fund["months"]) == ("2026", 1640.0, 2400.0, 3)
    assert (fund["remaining"], fund["fill"]) == (760.0, 68.3)

    assert (view["prev_month"], view["next_month"], view["stale"]) == ("2026-08", None, False)


def test_a_finished_month_shows_the_result_without_pace_or_warnings():
    august = month("2026-08", {"Supermercado": 440.0, "Alquiler": 850.0}, income=1800.0)
    view = budget.month_overview([august], "2026-08", settings(), TODAY, "2026-09-22")
    assert view["complete"] and view["tick"] is None and view["pace"] is None
    supermarket = next(m for m in view["meters"] if m["name"] == "Supermercado")
    assert supermarket["pct"] == 97.8 and supermarket["state"] == "ok"  # 98 % at the end is fine
    assert view["saving"] == {"projected": False, "amount": 510.0, "income_pending": False,
                              "goal": 200.0, "meets": True}
    assert view["next_month"] == "2026-09" and view["prev_month"] is None


def test_pace_above_and_on_track():
    budgets = {"Supermercado": 300.0}
    fast = [month("2026-09", {"Supermercado": 250.0}, income=1000.0)]
    view = budget.month_overview(fast, "2026-09", settings(budgets), TODAY, "2026-09-15")
    assert view["pace"]["state"] == "above" and view["pace"]["diff"] == 100.0
    assert view["meters"][0]["state"] == "ok"  # 83 %: still under the warning line

    on_pace = [month("2026-09", {"Supermercado": 155.0}, income=1000.0)]
    view = budget.month_overview(on_pace, "2026-09", settings(budgets), TODAY, "2026-09-15")
    assert view["pace"]["state"] == "on"


def test_fixed_bills_can_be_pending_or_over_budget():
    spending = {"Alquiler": 850.0, "Suministros": 131.4}
    view = budget.month_overview([month("2026-09", spending)], "2026-09", settings(FIXED), TODAY, "2026-09-10")
    states = {r["name"]: r["state"] for r in view["fixed"]["rows"]}
    assert states == {"Alquiler": "paid", "Suministros": "over", "Seguros": "pending", "Telefonía": "pending"}
    assert view["fixed"]["pending"] == 120.0
    assert view["pace"] is None  # no variable budget, nothing to pace

    closed = budget.month_overview([month("2026-08", spending)], "2026-08", settings(FIXED), TODAY, "2026-09-10")
    states = {r["name"]: r["state"] for r in closed["fixed"]["rows"]}
    assert states == {"Alquiler": "paid", "Suministros": "over", "Seguros": "missing", "Telefonía": "missing"}
    assert closed["fixed"]["pending"] == 0
    cheaper = budget.month_overview([month("2026-08", {"Suministros": 98.0})], "2026-08", settings(FIXED), TODAY,
                                    "2026-09-10")
    assert cheaper["fixed"]["rows"][1]["state"] == "paid"  # came in under budget: nothing pending


def test_without_budgets_the_page_lists_spending_and_the_recent_average():
    view = budget.month_overview(MONTHS, "2026-09", settings({}), TODAY, "2026-09-09")
    assert not view["has_budgets"] and view["pace"] is None and view["saving"] is None
    assert view["spending"][0] == {"name": "Alquiler", "spent": 850.0, "fill": 100.0}
    assert view["recent_months"] == ["2026-06", "2026-07", "2026-08"]
    assert view["recent_average"] == round((1280 + 1310 + 1283) / 3, 2)
    assert view["stale"] and view["days_since"] == 15


def test_no_movements_yet_this_month():
    view = budget.month_overview(MONTHS[:-1], "2026-09", settings(), TODAY, "2026-08-31")
    assert view["cutoff_day"] == 0 and view["spent"] == 0
    assert view["pace"]["state"] == "on" and view["data_until"] is None
    assert view["saving"]["income_pending"]


def test_months_needing_review_are_counted():
    txs = lg.rows_to_transactions(
        [{"date": "2026-09-03", "concept": "XYZ RARO", "amount": -9.0, "balance": 1.0}],
        lg.SHARED_ACCOUNT, "2026-09-24T07:00:00+00:00",
    )
    ledger = lg.new_ledger()
    lg.import_transactions(ledger, txs)
    view = budget.month_overview(lg.month_summaries(ledger), "2026-09", settings(), TODAY, "2026-09-03")
    assert view["review"] == 1 and view["unbudgeted"] == [{"name": "Otros", "spent": 9.0, "review": 1}]


def test_pick_month_stays_between_the_first_month_and_today():
    assert budget.pick_month(None, MONTHS, TODAY) == "2026-09"
    assert budget.pick_month("2026-13", MONTHS, TODAY) == "2026-09"
    assert budget.pick_month("2027-01", MONTHS, TODAY) == "2026-09"
    assert budget.pick_month("2020-01", MONTHS, TODAY) == "2025-12"
    assert budget.pick_month("2026-07", MONTHS, TODAY) == "2026-07"
    assert budget.pick_month("2026-07", [], TODAY) == "2026-09"
    assert budget.shift_month("2026-01", -1) == "2025-12"


def test_suggested_budgets_use_the_last_three_complete_months():
    months = [
        month("2026-05", {"Supermercado": 900.0}),  # outside the window
        month("2026-06", {"Supermercado": 431.0, "Cafés y Snacks": 12.0, "Belleza": 6.0}),
        month("2026-07", {"Supermercado": 440.0, "Otros": 80.0, "Ajustes de cuenta": 50.0}),
        month("2026-08", {"Supermercado": 452.0}),
        month("2026-09", {"Supermercado": 50.0}),   # the current month doesn't count
    ]
    result = budget.suggest_budgets(months, "2026-09")
    assert result["months"] == ["2026-06", "2026-07", "2026-08"]
    assert result["averages"]["Supermercado"] == 441.0
    assert result["suggested"] == {"Supermercado": 450.0}  # cafés 4 €/month and belleza 2 €: left empty
    assert "Otros" not in result["averages"] and "Ajustes de cuenta" not in result["averages"]
    assert budget.suggest_budgets(months, "2026-06")["suggested"] == {"Supermercado": 900.0}
    assert budget.suggest_budgets([], "2026-09")["months"] == []


def test_settings_view_fills_defaults_and_drops_junk():
    empty = budget.settings_view(None)
    assert empty["budgets"] == {} and empty["fixed_categories"] == budget.DEFAULT_FIXED
    assert empty["goals"] == budget.DEFAULT_GOALS

    view = budget.settings_view({
        "budgets": {"Supermercado": 450, "Inventada": 10, "Otros": 30, "Hogar": 0, "Salud": "x"},
        "fixed_categories": [],
        "goals": {"monthly_saving": 300, "annual_fund": -5, "fund_name": "  ", "fund_note": "Japón"},
    })
    assert view["budgets"] == {"Supermercado": 450.0}
    assert view["fixed_categories"] == []
    assert view["goals"] == {"monthly_saving": 300.0, "annual_fund": 2400.0,
                             "fund_name": "Fondo común", "fund_note": "Japón"}


@pytest.mark.parametrize("text, expected", [
    ("450", 450.0), (" 450 € ", 450.0), ("450,5", 450.5), ("450.5", 450.5),
    ("2.400", 2400.0), ("1.234,56", 1234.56), ("", None), ("  ", None), ("0", 0.0),
])
def test_parse_amount(text, expected):
    assert budget.parse_amount(text, budget.MAX_BUDGET) == expected


@pytest.mark.parametrize("text", ["-1", "abc", "nan", "inf", "100001", "1e9"])
def test_parse_amount_rejects_bad_values(text):
    with pytest.raises(ValueError):
        budget.parse_amount(text, budget.MAX_BUDGET)


def test_saving_settings_keeps_known_categories_in_order():
    doc = {}
    budget.set_budgets(doc, {"Supermercado": 450.0, "Alquiler": 850.0, "Hogar": 0.0},
                       ["Seguros", "Alquiler"], "now")
    assert doc["budgets"] == {"Alquiler": 850.0, "Supermercado": 450.0}
    assert doc["fixed_categories"] == ["Alquiler", "Seguros"]
    budget.set_goals(doc, {"monthly_saving": 250.0, "annual_fund": 3000.0,
                           "fund_name": "x" * 60, "fund_note": " Viajes "}, "now")
    assert doc["goals"]["fund_name"] == "x" * budget.MAX_NAME and doc["goals"]["fund_note"] == "Viajes"
    assert budget.settings_view(doc)["budgets"] == {"Alquiler": 850.0, "Supermercado": 450.0}
