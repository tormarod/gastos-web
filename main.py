from __future__ import annotations

import os
from datetime import datetime
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Cookie, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

load_dotenv()

app = FastAPI(title="Gastos Web", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "cambiame")
COOKIE_NAME = "gastos_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

_signer = URLSafeTimedSerializer(SECRET_KEY)


def _make_session_token() -> str:
    return _signer.dumps("authenticated")


def _verify_session(token: str) -> bool:
    try:
        _signer.loads(token, max_age=COOKIE_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


class _RedirectToLogin(Exception):
    pass


def _require_auth(session: str | None) -> None:
    if not session or not _verify_session(session):
        raise _RedirectToLogin()


# ── Auth routes ─────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login(request: Request, password: Annotated[str, Form()]):
    if password != APP_PASSWORD:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Contraseña incorrecta."}, status_code=401
        )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        _make_session_token(),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("ENV") == "production",
    )
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


# ── Dashboard ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, gastos_session: Annotated[str | None, Cookie()] = None):
    _require_auth(gastos_session)

    from services import s3_store

    data = s3_store.load_data()
    months_data = data.get("months", {})

    # Sort months chronologically
    sorted_months = sorted(months_data.values(), key=lambda m: m["month"])

    # Build aggregated stats across all months
    all_categories: dict[str, float] = {}
    for m in sorted_months:
        for cat, amt in m.get("summary", {}).items():
            all_categories[cat] = round(all_categories.get(cat, 0) + amt, 2)

    total_months = len(sorted_months)
    avg_categories = {
        cat: round(total / total_months, 2)
        for cat, total in all_categories.items()
    } if total_months > 0 else {}

    total_income   = round(sum(m["income"] for m in sorted_months), 2)
    total_expenses = round(sum(m["total_expense"] for m in sorted_months), 2)
    total_balance  = round(total_income - total_expenses, 2)

    avg_income   = round(total_income / total_months, 2) if total_months else 0
    avg_expenses = round(total_expenses / total_months, 2) if total_months else 0
    avg_balance  = round(total_balance / total_months, 2) if total_months else 0

    # Monthly series for the chart
    monthly_series = [
        {
            "month": m["month"],
            "income": m["income"],
            "expense": m["total_expense"],
            "balance": m["balance"],
            "summary": m["summary"],
        }
        for m in sorted_months
    ]

    # Latest month transactions (for the transaction table)
    latest_transactions = sorted_months[-1]["transactions"] if sorted_months else []
    latest_month = sorted_months[-1]["month"] if sorted_months else None

    # Savings goal: how much has been saved (positive balance months)
    savings_accumulated = round(sum(m["balance"] for m in sorted_months if m["balance"] > 0), 2)
    savings_goal = 2400.0

    from services import insights
    insight_cards = insights.generate(sorted_months)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "sorted_months": sorted_months,
        "monthly_series": monthly_series,
        "avg_categories": avg_categories,
        "all_categories": all_categories,
        "total_income": total_income,
        "total_expenses": total_expenses,
        "total_balance": total_balance,
        "avg_income": avg_income,
        "avg_expenses": avg_expenses,
        "avg_balance": avg_balance,
        "savings_accumulated": savings_accumulated,
        "savings_goal": savings_goal,
        "savings_pct": round(min(savings_accumulated / savings_goal * 100, 100), 1),
        "latest_transactions": latest_transactions[:50],
        "latest_month": latest_month,
        "has_data": total_months > 0,
        "now": datetime.now().strftime("%d/%m/%Y"),
        "insight_cards": insight_cards,
    })


# ── Upload ───────────────────────────────────────────────────────────────────

@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request, gastos_session: Annotated[str | None, Cookie()] = None):
    _require_auth(gastos_session)
    from services import s3_store
    data = s3_store.load_data()
    existing_months = sorted(data.get("months", {}).keys(), reverse=True)
    return templates.TemplateResponse("upload.html", {
        "request": request,
        "existing_months": existing_months,
        "success": None,
        "error": None,
    })


@app.post("/upload")
async def upload_statement(
    request: Request,
    gastos_session: Annotated[str | None, Cookie()] = None,
    file: UploadFile = File(...),
    month: str = Form(...),
):
    _require_auth(gastos_session)

    if not (file.filename or "").endswith(".xlsx"):
        from services import s3_store
        data = s3_store.load_data()
        return templates.TemplateResponse("upload.html", {
            "request": request,
            "existing_months": sorted(data.get("months", {}).keys(), reverse=True),
            "success": None,
            "error": "Solo se aceptan archivos .xlsx (Excel).",
        }, status_code=400)

    content = await file.read()

    try:
        from services import parser, s3_store
        custom_rules = s3_store.load_custom_rules()
        month_data = parser.parse_bbva_xlsx(content, month, custom_rules)
        s3_store.upload_statement(month, content)
        s3_store.upsert_month(month_data)
    except Exception as exc:
        from services import s3_store
        data = s3_store.load_data()
        return templates.TemplateResponse("upload.html", {
            "request": request,
            "existing_months": sorted(data.get("months", {}).keys(), reverse=True),
            "success": None,
            "error": f"Error al procesar el fichero: {exc}",
        }, status_code=422)

    return RedirectResponse(f"/upload?success={month}", status_code=303)


@app.post("/delete-month")
async def delete_month(
    request: Request,
    gastos_session: Annotated[str | None, Cookie()] = None,
    month: str = Form(...),
):
    _require_auth(gastos_session)
    from services import s3_store
    s3_store.delete_month(month)
    return RedirectResponse("/upload", status_code=303)


# ── Corrections ──────────────────────────────────────────────────────────────

ALL_CATEGORIES = [
    "Alquiler", "Suministros", "Telefonía", "Supermercado",
    "Delivery", "Restaurantes", "Cafés y Snacks", "Amazon/Online", "Ocio/Cultura",
    "Coche", "Transporte público", "Transporte privado", "Salud", "Ropa/Accesorios", "Belleza",
    "Viajes", "Compras", "Hogar", "Seguros", "Gasolinera",
    "Efectivo", "Comisiones", "Ingresos", "Ajustes de cuenta",
]


@app.get("/corrections", response_class=HTMLResponse)
async def corrections_page(request: Request, gastos_session: Annotated[str | None, Cookie()] = None):
    _require_auth(gastos_session)

    from services import s3_store
    from services.categorizer import suggest_pattern

    data = s3_store.load_data()
    custom_rules = s3_store.load_custom_rules()

    # Collect unique "Otros" concepts across all months, sorted by impact
    otros: dict[str, dict] = {}
    for month_data in sorted(data["months"].values(), key=lambda m: m["month"]):
        for tx in month_data["transactions"]:
            if tx["category"] == "Otros" and tx["amount"] < 0:
                key = tx["concept"]
                if key not in otros:
                    otros[key] = {
                        "concept": key,
                        "pattern": suggest_pattern(key),
                        "count": 0,
                        "total": 0.0,
                        "transactions": [],
                    }
                otros[key]["count"] += 1
                otros[key]["total"] = round(otros[key]["total"] + abs(tx["amount"]), 2)
                otros[key]["transactions"].append({
                    "date": tx.get("date") or "—",
                    "month": month_data["month"],
                    "amount": abs(tx["amount"]),
                })

    otros_list = sorted(otros.values(), key=lambda x: -x["total"])
    otros_total = round(sum(x["total"] for x in otros_list), 2)

    tx_overrides = s3_store.load_transaction_overrides()
    override_index = {
        (o["date"], o["concept"], o["amount"]): o["category"]
        for o in tx_overrides
    }
    for item in otros_list:
        for tx in item["transactions"]:
            tx["override_category"] = override_index.get(
                (tx["date"], item["concept"], tx["amount"])
            )

    return templates.TemplateResponse("corrections.html", {
        "request": request,
        "otros_list": otros_list,
        "otros_total": otros_total,
        "custom_rules": custom_rules,
        "categories": ALL_CATEGORIES,
        "saved": request.query_params.get("saved"),
    })


@app.post("/corrections")
async def save_correction(
    request: Request,
    gastos_session: Annotated[str | None, Cookie()] = None,
    pattern: str = Form(...),
    category: str = Form(...),
):
    _require_auth(gastos_session)

    from services import s3_store

    custom_rules = s3_store.load_custom_rules()
    # Remove any existing rule for this pattern and add the new one
    custom_rules = [r for r in custom_rules if r["pattern"].upper() != pattern.upper()]
    custom_rules.append({"pattern": pattern.upper(), "category": category})
    s3_store.save_custom_rules(custom_rules)
    s3_store.recategorize_all(custom_rules)

    return RedirectResponse("/corrections?saved=1", status_code=303)


@app.post("/corrections/override")
async def save_tx_override(
    request: Request,
    gastos_session: Annotated[str | None, Cookie()] = None,
    date: str = Form(...),
    concept: str = Form(...),
    amount: float = Form(...),
    category: str = Form(...),
):
    _require_auth(gastos_session)

    from services import s3_store

    s3_store.save_transaction_override(date, concept, amount, category)
    custom_rules = s3_store.load_custom_rules()
    s3_store.recategorize_all(custom_rules)

    return RedirectResponse("/corrections?saved=1", status_code=303)


@app.post("/corrections/delete")
async def delete_correction(
    request: Request,
    gastos_session: Annotated[str | None, Cookie()] = None,
    pattern: str = Form(...),
):
    _require_auth(gastos_session)

    from services import s3_store

    custom_rules = s3_store.load_custom_rules()
    custom_rules = [r for r in custom_rules if r["pattern"].upper() != pattern.upper()]
    s3_store.save_custom_rules(custom_rules)
    s3_store.recategorize_all(custom_rules)

    return RedirectResponse("/corrections", status_code=303)


# ── API (JSON) ───────────────────────────────────────────────────────────────

@app.get("/api/data")
async def api_data(gastos_session: Annotated[str | None, Cookie()] = None):
    _require_auth(gastos_session)
    from services import s3_store
    return s3_store.load_data()


# ── Redirect to login when session is missing or invalid ────────────────────

@app.exception_handler(_RedirectToLogin)
async def redirect_to_login_handler(request: Request, exc: _RedirectToLogin):
    return RedirectResponse("/login", status_code=303)
