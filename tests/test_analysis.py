from datetime import date

from services import analysis

TODAY = date(2026, 9, 24)
FIXED_CATEGORIES = ["Alquiler", "Suministros", "Telefonía", "Seguros"]

# The example of the design page: October 2025 to September 2026 (in progress).
MONTHS = ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03",
          "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
SUPPLIES = [110, 125, 160, 170, 165, 130, 112, 101, 98, 108, 107, 120]
INSURANCE = [63, 65, 66, 69, 66, 65, 65, 65, 65, 60, 67, 75]
VARIABLE = [1312, 1215, 1789, 1126, 1054, 1340, 1573, 1279, 1222, 1362, 1296, 752]
RESTAURANTS = [150, 140, 240, 120, 130, 170, 210, 150, 158, 195, 119, 164]


def month(key, summary, income=0.0, transactions=None):
    total = round(sum(summary.values()), 2)
    return {"month": key, "transactions": transactions or [], "summary": summary,
            "income": income, "total_expense": total, "balance": round(income - total, 2)}


def example_months():
    out = [
        # the 12 months before the default period, for the comparison
        month("2025-07", {"Alquiler": 850.0, "Supermercado": 1600.0}, income=2600.0),
        month("2025-08", {"Alquiler": 850.0, "Supermercado": 1650.0}, income=2600.0),
        month("2025-09", {"Alquiler": 850.0, "Supermercado": 1550.0}, income=2600.0),
    ]
    for i, key in enumerate(MONTHS):
        out.append(month(key, {
            "Alquiler": 850.0, "Suministros": float(SUPPLIES[i]), "Telefonía": 45.0,
            "Seguros": float(INSURANCE[i]), "Restaurantes": float(RESTAURANTS[i]),
            "Supermercado": float(VARIABLE[i] - RESTAURANTS[i]),
        }, income=2600.0))
    return out


def test_default_period_is_the_last_12_months_with_the_current_one_in_progress():
    period = analysis.select_period(example_months(), None, TODAY)
    assert period["key"] == "12m" and period["label"] == "Últimos 12 meses"
    assert [m["month"] for m in period["months"]] == MONTHS
    assert len(period["closed"]) == 11 and period["current"] == "2026-09"
    assert [m["month"] for m in period["previous"]] == ["2025-07", "2025-08", "2025-09"]
    assert [o["key"] for o in period["options"]] == ["12m", "2026", "2025", "todo"]


def test_year_and_everything_periods():
    months = example_months()
    year = analysis.select_period(months, "2025", TODAY)
    assert [m["month"] for m in year["months"]] == ["2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12"]
    assert year["current"] is None and len(year["closed"]) == 6 and year["previous"] == []
    everything = analysis.select_period(months, "todo", TODAY)
    assert len(everything["months"]) == 15 and everything["previous"] == []
    assert analysis.select_period(months, "1999", TODAY)["key"] == "12m"
    assert analysis.select_period(months, "<script>", TODAY)["key"] == "12m"


def test_overview_averages_closed_months_only():
    period = analysis.select_period(example_months(), "12m", TODAY)
    view = analysis.overview(period, FIXED_CATEGORIES, goal=200.0)
    assert view is not None
    assert (view["months"], view["spent"], view["fixed"], view["variable"]) == (11, 2410.45, 1086.09, 1324.36)
    assert (view["income"], view["saving"], view["goal_met"]) == (2600.0, 189.55, 7)
    assert view["changes"] == {"spent": -2, "income": 0, "saving": 39.55}  # previous: 2.450 € a month, 150 € saved


def test_no_comparison_without_enough_previous_months():
    months = example_months()[2:]  # only one month before the period
    view = analysis.overview(analysis.select_period(months, "12m", TODAY), FIXED_CATEGORIES, goal=200.0)
    assert view is not None and view["changes"] is None


def test_overview_needs_a_closed_month():
    only_current = [month("2026-09", {"Alquiler": 850.0}, income=2600.0)]
    period = analysis.select_period(only_current, "12m", TODAY)
    assert period["closed"] == [] and analysis.overview(period, FIXED_CATEGORIES, goal=200.0) is None


def test_monthly_split_into_fixed_and_variable():
    period = analysis.select_period(example_months(), "12m", TODAY)
    rows = analysis.monthly(period, FIXED_CATEGORIES)
    december = rows[2]
    assert (december["month"], december["fixed"], december["variable"], december["total"]) == ("2025-12", 1121.0, 1789.0, 2910.0)
    assert december["saving"] == -310.0 and not december["in_progress"]
    assert rows[-1]["in_progress"] and rows[-1]["total"] == 1842.0


def test_category_rows_with_average_trend_and_budget():
    period = analysis.select_period(example_months(), "12m", TODAY)
    budgets = {"Restaurantes": 180.0, "Suministros": 120.0, "Alquiler": 850.0}
    rows = analysis.category_rows(period, budgets, FIXED_CATEGORIES)
    assert [r["name"] for r in rows][:2] == ["Supermercado", "Alquiler"]
    restaurants = next(r for r in rows if r["name"] == "Restaurantes")
    assert (restaurants["average"], restaurants["budget"], restaurants["over"]) == (162.0, 180.0, False)
    assert restaurants["series"] == [float(v) for v in RESTAURANTS[:11]]
    assert restaurants["total"] == 1946.0  # the whole period, September included
    supplies = next(r for r in rows if r["name"] == "Suministros")
    assert supplies["average"] == 126.0 and supplies["over"] and supplies["fixed"]
    spark = restaurants["spark"]
    assert len(spark) == 11 and spark[-1]["kind"] == "last" and spark[2]["h"] == 18  # December is the tallest


def test_sparkline_handles_zero_and_long_series():
    bars = analysis.sparkline([0.0, 780.0, 0.0])
    assert [b["kind"] for b in bars] == ["zero", "bar", "zero"]
    many = analysis.sparkline([10.0] * 40)
    assert len(many) == 40 and all(b["w"] >= 1 for b in many) and many[-1]["x"] < analysis.SPARK_WIDTH
    assert analysis.sparkline([]) == []


def tx(amount, concept, merchant, category="Restaurantes"):
    return {"amount": amount, "concept": concept, "merchant": merchant, "category": category}


def test_category_detail_months_over_budget_and_merchants():
    august_txs = [tx(-40.0, "BAR LA ESQUINA", "BAR LA ESQUINA"), tx(-30.0, "BAR LA ESQUINA", "BAR LA ESQUINA"),
                  tx(-25.0, "PIZZERIA NAPOLI", "PIZZERIA NAPOLI"), tx(-12.0, "SUSHI KYOTO", "SUSHI KYOTO"),
                  tx(-9.0, "ASADOR EL ROBLE", "ASADOR EL ROBLE"), tx(-3.0, "KIOSKO", "KIOSKO"),
                  tx(5.0, "BAR LA ESQUINA DEVOLUCION", "BAR LA ESQUINA"),
                  tx(-60.0, "MERCADONA", "MERCADONA", category="Supermercado")]
    months = [
        month("2026-07", {"Restaurantes": 195.0}),
        month("2026-08", {"Restaurantes": 114.0, "Supermercado": 60.0}, transactions=august_txs),
        month("2026-09", {"Restaurantes": 164.0}),
    ]
    period = analysis.select_period(months, "12m", TODAY)
    detail = analysis.category_detail(period, "Restaurantes", {"Restaurantes": 180.0}, FIXED_CATEGORIES)
    assert [v["value"] for v in detail["values"]] == [195.0, 114.0, 164.0]
    assert detail["values"][-1]["in_progress"]
    assert (detail["average"], detail["closed"], detail["months_over"]) == (154.5, 2, 1)
    assert detail["total"] == 473.0 and detail["count"] == 7
    assert [(m["merchant"], m["count"], m["total"]) for m in detail["merchants"]] == [
        ("BAR LA ESQUINA", 3, 65.0), ("PIZZERIA NAPOLI", 1, 25.0), ("SUSHI KYOTO", 1, 12.0), ("ASADOR EL ROBLE", 1, 9.0),
    ]
    assert detail["merchants"][0]["fill"] == 100.0
    assert detail["others"] == {"places": 1, "count": 1, "total": 3.0}


def test_category_detail_without_budget():
    months = [month("2026-08", {"Viajes": 780.0})]
    detail = analysis.category_detail(analysis.select_period(months, "12m", TODAY), "Viajes", {}, FIXED_CATEGORIES)
    assert detail["budget"] is None and detail["months_over"] is None and detail["merchants"] == []


def test_rows_skip_categories_only_spent_in_the_month_in_progress_and_round_before_warning():
    months = [
        month("2026-07", {"Supermercado": 450.2}),
        month("2026-08", {"Supermercado": 450.4}),
        month("2026-09", {"Supermercado": 100.0, "Viajes": 300.0}),
    ]
    rows = analysis.category_rows(analysis.select_period(months, "12m", TODAY), {"Supermercado": 450.0}, [])
    assert [r["name"] for r in rows] == ["Supermercado"]
    assert rows[0]["average"] == 450.3 and not rows[0]["over"]  # shows as 450 €, the budget
