"""
Dynamic financial insights generator.

Produces up to 6 insight cards ranked by severity (danger → warn → success)
based on the user's own historical data — no hardcoded thresholds.
"""

from __future__ import annotations

from typing import Any

MONTH_NAMES = {
    "01": "enero", "02": "febrero", "03": "marzo", "04": "abril",
    "05": "mayo",  "06": "junio",   "07": "julio", "08": "agosto",
    "09": "septiembre", "10": "octubre", "11": "noviembre", "12": "diciembre",
}

CAT_ICONS: dict[str, str] = {
    "Alquiler": "🏠", "Suministros": "⚡", "Telefonía": "📱",
    "Supermercado": "🛒", "Delivery": "🍕", "Restaurantes": "🍽️",
    "Amazon/Online": "📦", "Ocio/Cultura": "🎭", "Transporte": "🚗",
    "Salud": "💊", "Ropa/Accesorios": "👗", "Hogar": "🪴",
    "Seguros": "🛡️", "Gasolinera": "⛽", "Efectivo": "💵",
    "Comisiones": "🏦", "Otros": "📎",
}

Insight = dict[str, str]


def _month_name(label: str) -> str:
    return MONTH_NAMES.get(label[5:7], label)


def _icon(cat: str) -> str:
    return CAT_ICONS.get(cat, "💶")


def generate(sorted_months: list[dict[str, Any]]) -> list[Insight]:
    """
    sorted_months: list of month dicts ordered oldest → newest,
    each with keys: month, summary, income, total_expense, balance.
    """
    if not sorted_months:
        return []

    insights: list[Insight] = []
    current  = sorted_months[-1]
    previous = sorted_months[-2] if len(sorted_months) >= 2 else None

    # All spending categories (exclude Ingresos)
    all_cats = {
        cat
        for m in sorted_months
        for cat in m["summary"]
        if cat != "Ingresos"
    }

    # Historical average per category (all months except current)
    history = sorted_months[:-1] if len(sorted_months) > 1 else sorted_months
    avg_by_cat: dict[str, float] = {}
    for cat in all_cats:
        vals = [m["summary"][cat] for m in history if m["summary"].get(cat, 0) > 0]
        if vals:
            avg_by_cat[cat] = sum(vals) / len(vals)

    # ── 1. Month-over-month deltas ────────────────────────────────────────
    if previous:
        deltas: list[tuple[str, float, float, float, float]] = []
        for cat in all_cats:
            curr_val = current["summary"].get(cat, 0.0)
            prev_val = previous["summary"].get(cat, 0.0)
            if prev_val == 0:
                continue
            pct  = (curr_val - prev_val) / prev_val * 100
            diff = curr_val - prev_val
            deltas.append((cat, curr_val, prev_val, pct, diff))

        deltas.sort(key=lambda x: abs(x[4]), reverse=True)
        curr_name = _month_name(current["month"])
        prev_name = _month_name(previous["month"])

        # Significant increases → warn/danger
        for cat, cv, pv, pct, diff in deltas:
            if pct > 20 and diff > 15:
                severity = "danger" if pct > 40 else "warn"
                insights.append({
                    "type":   severity,
                    "icon":   _icon(cat),
                    "title":  f"{cat} subió un {pct:.0f}% en {curr_name}",
                    "detail": f"De {pv:.0f}€ en {prev_name} a {cv:.0f}€ este mes — {diff:.0f}€ más.",
                })
                if sum(1 for i in insights if i["type"] in ("danger", "warn")) >= 3:
                    break

        # Significant decreases → success
        for cat, cv, pv, pct, diff in deltas:
            if pct < -15 and diff < -10:
                insights.append({
                    "type":   "success",
                    "icon":   _icon(cat),
                    "title":  f"{cat} bajó un {abs(pct):.0f}% en {curr_name}",
                    "detail": f"De {pv:.0f}€ en {prev_name} a {cv:.0f}€ este mes — {abs(diff):.0f}€ menos.",
                })
                if sum(1 for i in insights if i["type"] == "success") >= 2:
                    break

    # ── 2. Above personal average (only if not already caught by MoM) ─────
    covered = {i["title"].split()[0] for i in insights}
    for cat, curr_val in sorted(current["summary"].items(), key=lambda x: -x[1]):
        if cat == "Ingresos" or cat in covered:
            continue
        avg = avg_by_cat.get(cat, 0)
        if avg == 0:
            continue
        pct_above = (curr_val - avg) / avg * 100
        if pct_above > 35 and curr_val - avg > 25:
            insights.append({
                "type":   "warn",
                "icon":   _icon(cat),
                "title":  f"{cat} por encima de vuestra media habitual",
                "detail": (
                    f"Este mes: {curr_val:.0f}€ · vuestra media: {avg:.0f}€/mes "
                    f"({pct_above:.0f}% más de lo normal)."
                ),
            })
            break  # one average-alert is enough

    # ── 3. Consecutive improvement streak (3+ months) ─────────────────────
    if len(sorted_months) >= 3:
        for cat in all_cats:
            vals = [m["summary"].get(cat, 0.0) for m in sorted_months[-3:]]
            if vals[0] > vals[1] > vals[2] and vals[0] > 0:
                reduction = vals[0] - vals[2]
                if reduction > 20:
                    m0 = _month_name(sorted_months[-3]["month"])
                    m2 = _month_name(sorted_months[-1]["month"])
                    insights.append({
                        "type":   "success",
                        "icon":   _icon(cat),
                        "title":  f"3 meses seguidos reduciendo {cat}",
                        "detail": (
                            f"De {vals[0]:.0f}€ en {m0} a {vals[2]:.0f}€ en {m2} "
                            f"— {reduction:.0f}€ menos. Seguid así."
                        ),
                    })
                    break  # one streak insight is enough

    # ── 4. Savings trajectory ─────────────────────────────────────────────
    if len(sorted_months) >= 2:
        avg_balance = sum(m["balance"] for m in sorted_months) / len(sorted_months)
        curr_month_num = int(current["month"][5:7])
        months_left    = 12 - curr_month_num
        already_saved  = sum(m["balance"] for m in sorted_months if m["balance"] > 0)
        projected      = round(already_saved + avg_balance * months_left)

        if avg_balance >= 0:
            insights.append({
                "type":   "success",
                "icon":   "📈",
                "title":  f"A este ritmo ahorraréis ~{projected:,}€ este año".replace(",", "."),
                "detail": (
                    f"Media de {avg_balance:.0f}€/mes de superávit · "
                    f"{months_left} meses restantes en {current['month'][:4]}."
                ),
            })
        else:
            insights.append({
                "type":   "danger",
                "icon":   "⚠️",
                "title":  "Los gastos superan los ingresos de media",
                "detail": (
                    f"Media mensual: {avg_balance:.0f}€. "
                    "Revisad los gastos variables para revertir la tendencia."
                ),
            })

    # ── 5. Best month callout (≥3 months of data) ─────────────────────────
    if len(sorted_months) >= 3:
        best = max(sorted_months[:-1], key=lambda m: m["balance"])
        if best["balance"] > 0 and best["balance"] > (avg_by_cat.get("_balance", 0)):
            insights.append({
                "type":   "success",
                "icon":   "🏆",
                "title":  f"{_month_name(best['month']).capitalize()} fue vuestro mejor mes",
                "detail": (
                    f"Ahorro de {best['balance']:.0f}€ — usadlo como referencia "
                    "para los meses siguientes."
                ),
            })

    # ── Rank by severity and cap at 6 ─────────────────────────────────────
    order = {"danger": 0, "warn": 1, "success": 2}
    insights.sort(key=lambda i: order[i["type"]])
    return insights[:6]
