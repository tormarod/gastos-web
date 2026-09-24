from services import insights


def month(key, spending, income):
    total = sum(spending.values())
    return {"month": key, "summary": spending, "income": income, "total_expense": total, "balance": income - total}


def test_the_latest_closed_month_can_be_the_best_one():
    months = [
        month("2026-06", {"Supermercado": 500.0}, 800.0),
        month("2026-07", {"Supermercado": 450.0}, 800.0),
        month("2026-08", {"Supermercado": 300.0}, 800.0),
    ]
    titles = [card["title"] for card in insights.generate(months)]
    assert "Agosto fue vuestro mejor mes" in titles


def test_no_months_no_insights():
    assert insights.generate([]) == []
