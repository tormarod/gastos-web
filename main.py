from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from fastapi import (
    Cookie,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

load_dotenv()

from services import analysis, budget, insights, parser, repo
from services import categorizer as cat
from services import ledger as lg

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gastos")

IS_PRODUCTION = os.environ.get("ENV") == "production"


def _secret(name: str, dev_default: str) -> str:
    """Required in production; a clearly fake default keeps local development easy."""
    value = os.environ.get(name)
    if value:
        return value
    if IS_PRODUCTION:
        raise RuntimeError(f"Falta la variable de entorno {name}; la app no arranca sin ella.")
    log.warning("%s no está definida: usando un valor solo apto para desarrollo.", name)
    return dev_default


SECRET_KEY = _secret("SECRET_KEY", "dev-secret-change-me")
APP_PASSWORD = _secret("APP_PASSWORD", "cambiame")
COOKIE_NAME = "gastos_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60

app = FastAPI(title="Gastos Web", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

_signer = URLSafeTimedSerializer(SECRET_KEY)


try:
    _LOCAL_TZ = ZoneInfo("Europe/Madrid")
except ZoneInfoNotFoundError:  # no tz database on the system
    _LOCAL_TZ = timezone.utc


def _today() -> date:
    """Today in Spain, where the months you both live in start and end."""
    return datetime.now(_LOCAL_TZ).date()


# ── Template helpers ─────────────────────────────────────────────────────────

MONTH_NAMES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _number(value: Any, decimals: int = 0) -> str:
    """-1234.5 → '−1.235' (Spanish separators, no currency)"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    text = f"{abs(number):,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'−' if number < 0 and text.strip('0,.') else ''}{text}"


def _eur(value: Any, decimals: int = 2) -> str:
    """-1234.5 → '−1.234,50 €'"""
    text = _number(value, decimals)
    return text if text == "—" else f"{text}\u00a0€"  # no line break between number and €


def _input_amount(value: Any) -> str:
    """450.0 → '450', 12.5 → '12,5', None → '' (for form fields)"""
    if value is None:
        return ""
    number = float(value)
    if number == int(number):
        return str(int(number))
    return f"{number:.2f}".rstrip("0").replace(".", ",")


def _month_label(value: str | None) -> str:
    if not value or len(value) < 7:
        return value or "—"
    try:
        return f"{MONTH_NAMES[int(value[5:7]) - 1]} {value[:4]}"
    except (ValueError, IndexError):
        return value


def _short_date(value: str | None) -> str:
    """'2026-09-20' → '20/09/2026'"""
    if not value or len(value) < 10:
        return value or "—"
    return f"{value[8:10]}/{value[5:7]}/{value[:4]}"


def _day_month(value: str | None) -> str:
    """'2026-09-20' → '20 sep'"""
    if not value or len(value) < 10:
        return value or "—"
    try:
        return f"{int(value[8:10])} {MONTH_NAMES[int(value[5:7]) - 1][:3]}"
    except (ValueError, IndexError):
        return value


WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _day_label(value: str | None) -> str:
    """'2026-09-22' → 'Martes 22 sep'"""
    if not value:
        return "Sin fecha"
    try:
        day = date.fromisoformat(value[:10])
    except ValueError:
        return value
    return f"{WEEKDAYS[day.weekday()].capitalize()} {day.day} {MONTH_NAMES[day.month - 1][:3]}"


_LOWER_WORDS = {"DE", "DEL", "Y", "E", "EN"}
_SHORT_WORDS = {"LA", "EL", "LO", "AL", "UN", "MI", "SU", "TU"}  # two letters, but not initials


def _nice_name(tx: dict[str, Any]) -> str:
    """The merchant as people write it: 'BAR LA ESQUINA' → 'Bar La Esquina', 'BP' stays."""
    key = tx.get("merchant") or cat.merchant_key(tx.get("concept") or "")
    words = []
    for i, word in enumerate(key.split()):
        letters = word.replace("'", "").replace(".", "")
        if not letters.isalpha() or (len(word) <= 2 and word not in _LOWER_WORDS | _SHORT_WORDS):
            words.append(word)
        elif i and word in _LOWER_WORDS:
            words.append(word.lower())
        else:
            words.append(word.capitalize())
    return " ".join(words) or (tx.get("concept") or "—")


def _tx_anchor(tx_id: str) -> str:
    """A short, URL-safe id for a movement's row (ids can contain ':' and '#')."""
    return "m-" + hashlib.sha1(tx_id.encode()).hexdigest()[:10]


def _month_name(value: str | None) -> str:
    """'2026-09' → 'septiembre'"""
    try:
        return MONTH_NAMES[int((value or "")[5:7]) - 1]
    except (ValueError, IndexError):
        return value or "—"


def _asset_version() -> str:
    """Changes whenever a static file changes, so browsers fetch the new one."""
    digest = hashlib.sha1()
    for path in sorted(Path("static").glob("*.*")):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


templates.env.filters["eur"] = _eur
templates.env.filters["number"] = _number
templates.env.filters["input_amount"] = _input_amount
templates.env.filters["month_label"] = _month_label
templates.env.filters["month_name"] = _month_name
templates.env.filters["short_date"] = _short_date
templates.env.filters["day_month"] = _day_month
templates.env.filters["day_label"] = _day_label
templates.env.filters["nice_name"] = _nice_name
templates.env.filters["tx_anchor"] = _tx_anchor
templates.env.globals["categories"] = cat.CATEGORIES
templates.env.globals["asset_version"] = _asset_version()


def _render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    *,
    ledger: lg.Ledger | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    ledger = ledger if ledger is not None else repo.load_ledger()
    base = {"page": name, "review_count": lg.review_count(ledger)}
    return templates.TemplateResponse(request, name, {**base, **(context or {})}, status_code=status_code)


def _redirect(path: str, **params: Any) -> RedirectResponse:
    query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return RedirectResponse(f"{path}?{query}" if query else path, status_code=303)


# ── Security ─────────────────────────────────────────────────────────────────

class _RedirectToLogin(Exception):
    pass


def _make_session_token() -> str:
    return _signer.dumps("authenticated")


def _verify_session(token: str) -> bool:
    try:
        _signer.loads(token, max_age=COOKIE_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def _require_auth(session: str | None) -> None:
    if not session or not _verify_session(session):
        raise _RedirectToLogin()


def _client_ip(request: Request) -> str:
    # Render runs behind Cloudflare: True-Client-IP carries the visitor's
    # address, and Render sets the first X-Forwarded-For entry to it.
    for header in ("true-client-ip", "cf-connecting-ip"):
        if request.headers.get(header):
            return request.headers[header].strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


_login_failures: dict[str, deque[float]] = defaultdict(deque)


def _recent_failures(key: str, now: float) -> int:
    q = _login_failures[key]
    while q and now - q[0] > LOGIN_WINDOW_SECONDS:
        q.popleft()
    return len(q)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Forms must be posted from this site (defence in depth on top of SameSite cookies).
    if request.method == "POST":
        source = request.headers.get("origin") or request.headers.get("referer")
        if source is not None:
            host = urlparse(source).netloc
            if source == "null" or (host and host != request.headers.get("host")):
                return PlainTextResponse("Origen no permitido.", status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


@app.exception_handler(_RedirectToLogin)
async def redirect_to_login_handler(request: Request, exc: _RedirectToLogin):
    return RedirectResponse("/login", status_code=303)


SessionCookie = Annotated[str | None, Cookie(alias=COOKIE_NAME)]


# ── Auth routes ─────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login(request: Request, password: Annotated[str, Form()]):
    now = time.monotonic()
    ip = _client_ip(request)
    # Per address only: a global limit would let anyone lock you both out.
    if _recent_failures(ip, now) >= LOGIN_MAX_FAILURES:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Demasiados intentos fallidos. Espera 15 minutos y vuelve a probar."},
            status_code=429,
        )
    if not hmac.compare_digest(password.encode(), APP_PASSWORD.encode()):
        _login_failures[ip].append(now)
        return templates.TemplateResponse(
            request, "login.html", {"error": "Contraseña incorrecta."}, status_code=401
        )
    _login_failures.pop(ip, None)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        _make_session_token(),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=IS_PRODUCTION,
    )
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


# ── Home ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def home(request: Request, mes: str | None = None, session: SessionCookie = None):
    _require_auth(session)
    ledger = repo.load_ledger()
    settings = budget.settings_view(repo.load_settings())
    months = lg.month_summaries(ledger)
    today = _today()
    month = budget.pick_month(mes, months, today)
    view = budget.month_overview(months, month, settings, today, lg.last_date(ledger))
    return _render(request, "home.html", {"view": view, "has_data": bool(months)}, ledger=ledger)


# ── Analysis ─────────────────────────────────────────────────────────────────

def _chart_labels(months: list[str]) -> list[Any]:
    """Short month names; the first bar and every January also carry the year."""
    labels: list[Any] = []
    for i, month in enumerate(months):
        short = MONTH_NAMES[int(month[5:7]) - 1][:3]
        labels.append([short, month[:4]] if i == 0 or month[5:7] == "01" else short)
    return labels


def _previous_label(period: dict[str, Any]) -> str:
    if period["key"] == analysis.DEFAULT_PERIOD:
        return "los 12 meses anteriores"
    return str(int(period["key"]) - 1) if period["key"].isdigit() else ""


@app.get("/analisis", response_class=HTMLResponse)
def analysis_page(request: Request, periodo: str = "", session: SessionCookie = None):
    _require_auth(session)
    ledger = repo.load_ledger()
    settings = budget.settings_view(repo.load_settings())
    months = lg.month_summaries(ledger)
    today = _today()
    period = analysis.select_period(months, periodo, today)
    fixed, goals = settings["fixed_categories"], settings["goals"]
    rows = analysis.monthly(period, fixed)
    last = period["months"][-1]["month"] if period["months"] else budget.month_key(today)
    return _render(request, "analysis.html", {
        "has_data": bool(months),
        "period": period,
        "previous_label": _previous_label(period),
        "overview": analysis.overview(period, fixed, goals["monthly_saving"]),
        "monthly": rows,
        "categories": analysis.category_rows(period, settings["budgets"], fixed),
        "insight_cards": insights.generate(period["closed"])[:3],
        "fund": budget.fund_progress(months, goals, last, today),
        "goals": goals,
        "chart": {
            "labels": _chart_labels([r["month"] for r in rows]),
            "fixed": [r["fixed"] for r in rows],
            "variable": [r["variable"] for r in rows],
            "income": [r["income"] for r in rows],
            "saving": [None if r["in_progress"] else r["saving"] for r in rows],
            "inProgress": [r["in_progress"] for r in rows],
            "goal": goals["monthly_saving"],
        },
    }, ledger=ledger)


@app.get("/analisis/categoria", response_class=HTMLResponse)
def category_page(request: Request, nombre: str = "", periodo: str = "", session: SessionCookie = None):
    _require_auth(session)
    if nombre not in cat.CATEGORIES or nombre == cat.INCOME:
        raise HTTPException(status_code=404, detail="Categoría desconocida.")
    ledger = repo.load_ledger()
    settings = budget.settings_view(repo.load_settings())
    period = analysis.select_period(lg.month_summaries(ledger), periodo, _today())
    detail = analysis.category_detail(period, nombre, settings["budgets"], settings["fixed_categories"])
    return _render(request, "category.html", {
        "period": period,
        "detail": detail,
        "movements_url": "/movimientos?" + urlencode({"mes": "todos", "cat": nombre}),
        "chart": {
            "labels": _chart_labels([v["month"] for v in detail["values"]]),
            "values": [v["value"] for v in detail["values"]],
            "inProgress": [v["in_progress"] for v in detail["values"]],
            "budget": detail["budget"],
            "name": nombre,
        },
    }, ledger=ledger)


# ── Upload ───────────────────────────────────────────────────────────────────

def _int(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def _upload_context(ledger: lg.Ledger, request: Request) -> dict[str, Any]:
    q = request.query_params
    result = None
    if q.get("added") is not None:
        result = {
            "added": _int(q.get("added")),
            "duplicates": _int(q.get("dup")),
            "needs_review": _int(q.get("review")),
            "first_date": lg.iso_date(q.get("from")),
            "last_date": lg.iso_date(q.get("to")),
        }
    return {
        "months": lg.months_overview(ledger),
        "result": result,
        "deleted": q.get("deleted"),
        "deleted_count": q.get("n"),
        "errors": [],
    }


@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request, session: SessionCookie = None):
    _require_auth(session)
    ledger = repo.load_ledger()
    return _render(request, "upload.html", _upload_context(ledger, request), ledger=ledger)


@app.post("/upload")
def upload_statement(
    request: Request,
    files: Annotated[list[UploadFile], File()],
    session: SessionCookie = None,
):
    _require_auth(session)
    errors: list[str] = []
    incoming: list[lg.Transaction] = []
    imported_at = repo.now_iso()

    for upload in files:
        name = upload.filename or "fichero"
        if not name.lower().endswith(".xlsx"):
            errors.append(f"{name}: solo se aceptan ficheros .xlsx (Excel).")
            continue
        content = upload.file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            errors.append(f"{name}: el fichero pesa más de 10 MB.")
            continue
        try:
            rows = parser.parse_bbva_xlsx(content)
        except ValueError as exc:
            errors.append(f"{name}: {exc}")
            continue
        repo.archive_statement(content)
        incoming.extend(lg.rows_to_transactions(rows, lg.SHARED_ACCOUNT, imported_at))

    result = None
    if incoming:
        rules = repo.load_rules()
        result = repo.update_ledger(lambda ledger: lg.import_transactions(ledger, incoming, rules))

    if errors:
        ledger = repo.load_ledger()
        context = _upload_context(ledger, request)
        context["errors"] = errors
        context["result"] = result.as_dict() if result else None
        return _render(request, "upload.html", context, ledger=ledger,
                       status_code=422 if result is None else 200)

    assert result is not None
    return _redirect(
        "/upload", added=result.added, dup=result.duplicates, review=result.needs_review,
        **{"from": result.first_date, "to": result.last_date},
    )


@app.post("/delete-month")
def delete_month(month: Annotated[str, Form()], session: SessionCookie = None):
    _require_auth(session)
    removed = repo.update_ledger(lambda ledger: lg.delete_month(ledger, month))
    return _redirect("/upload", deleted=month, n=removed)


# ── Review ───────────────────────────────────────────────────────────────────

def _valid_category(category: str) -> str:
    if category not in cat.CATEGORIES:
        raise HTTPException(status_code=400, detail="Categoría desconocida.")
    return category


@app.get("/revisar", response_class=HTMLResponse)
def review_page(request: Request, q: str = "", session: SessionCookie = None):
    _require_auth(session)
    ledger = repo.load_ledger()
    params = request.query_params
    return _render(request, "review.html", {
        "q": q.strip(),
        "groups": lg.review_groups(ledger, q),
        "rules": sorted(repo.load_rules(), key=lambda r: r["pattern"]),
        "migration": (ledger.get("meta") or {}).get("migration"),
        "ok": params.get("ok"),
        "changed": _int(params.get("n")),
    }, ledger=ledger)


@app.get("/corrections")
def corrections_moved(session: SessionCookie = None):
    _require_auth(session)
    return RedirectResponse("/revisar", status_code=301)


@app.post("/revisar/regla")
def save_rule(
    pattern: Annotated[str, Form()],
    category: Annotated[str, Form()],
    q: Annotated[str, Form()] = "",
    session: SessionCookie = None,
):
    _require_auth(session)
    _valid_category(category)
    if len(cat.normalize(pattern)) < 2:
        return _redirect("/revisar", q=q, ok="corto")
    now = repo.now_iso()
    rules = repo.update_rules(lambda current: lg.upsert_rule(current, pattern, category, now))
    changed = repo.update_ledger(lambda ledger: lg.apply_rules(ledger, rules))
    return _redirect("/revisar", q=q, ok="regla", n=changed)


@app.post("/revisar/movimiento")
def save_transaction_category(
    tx_id: Annotated[str, Form()],
    category: Annotated[str, Form()],
    q: Annotated[str, Form()] = "",
    session: SessionCookie = None,
):
    _require_auth(session)
    _valid_category(category)
    found = repo.update_ledger(lambda ledger: lg.set_category(ledger, tx_id, category) is not None)
    return _redirect("/revisar", q=q, ok="movimiento" if found else "no_encontrado")


@app.post("/revisar/regla/borrar")
def delete_rule(pattern: Annotated[str, Form()], session: SessionCookie = None):
    _require_auth(session)
    rules = repo.update_rules(lambda current: lg.delete_rule(current, pattern))
    changed = repo.update_ledger(lambda ledger: lg.apply_rules(ledger, rules))
    return _redirect("/revisar", ok="borrada", n=changed)


# ── Movements ────────────────────────────────────────────────────────────────

MOVEMENTS_STEP = 200
MOVEMENTS_MAX = 5000
QUERY_MAX = 80


def _clean_filters(q: str | None, mes: str | None, category: str | None) -> dict[str, str]:
    """Only known-good filter values travel back into links and redirects."""
    month = mes or ""
    category = category or ""
    return {
        "q": (q or "").strip()[:QUERY_MAX],
        "mes": month if month == "todos" or budget.is_month(month) else "",
        "cat": category if category in cat.CATEGORIES else "",
    }


def _back_to_movements(filters: dict[str, str], **params: str) -> RedirectResponse:
    query = urlencode({k: v for k, v in {**filters, **params}.items() if v})
    anchor = params.get("ver")
    return RedirectResponse(f"/movimientos?{query}" + (f"#{anchor}" if anchor else ""), status_code=303)


@app.get("/movimientos", response_class=HTMLResponse)
def movements_page(
    request: Request,
    q: str = "",
    mes: str = "",
    category: Annotated[str, Query(alias="cat")] = "",
    n: str = "",
    session: SessionCookie = None,
):
    _require_auth(session)
    ledger = repo.load_ledger()
    filters = _clean_filters(q, mes, category)
    newest = lg.last_date(ledger)
    default_month = newest[:7] if newest else budget.month_key(_today())
    if filters["mes"] == "todos" or (not filters["mes"] and filters["q"]):
        month = None  # a new search looks in every month
    else:
        month = filters["mes"] or default_month
    found = lg.search(ledger, query=filters["q"], month=month, category=filters["cat"] or None)
    limit = min(max(_int(n), MOVEMENTS_STEP), MOVEMENTS_MAX)
    groups: list[dict[str, Any]] = []
    for tx in found[:limit]:
        day = tx.get("date")
        if not groups or groups[-1]["date"] != day:
            groups.append({"date": day, "transactions": []})
        groups[-1]["transactions"].append(tx)
    totals = lg.summarize(found)
    params = request.query_params
    return _render(request, "movements.html", {
        "has_data": newest is not None or bool(ledger["transactions"]),
        "filters": filters,
        "month": month,
        "months": [m["month"] for m in reversed(lg.month_summaries(ledger))],
        "groups": groups,
        "count": len(found),
        "expense": totals["total_expense"],
        "income": totals["income"],
        "more": max(len(found) - limit, 0),
        "step": MOVEMENTS_STEP,
        "more_url": "/movimientos?" + urlencode({k: v for k, v in {**filters, "n": limit + MOVEMENTS_STEP}.items() if v}),
        "ok": params.get("ok"),
        "ver": params.get("ver"),
    }, ledger=ledger)


@app.post("/movimientos/categoria")
def movement_category(
    tx_id: Annotated[str, Form()],
    category: Annotated[str, Form()],
    q: Annotated[str, Form()] = "",
    mes: Annotated[str, Form()] = "",
    category_filter: Annotated[str, Form(alias="cat")] = "",
    session: SessionCookie = None,
):
    _require_auth(session)
    _valid_category(category)
    filters = _clean_filters(q, mes, category_filter)
    found = repo.update_ledger(lambda ledger: lg.set_category(ledger, tx_id, category) is not None)
    if not found:
        return _back_to_movements(filters, ok="no_encontrado")
    return _back_to_movements(filters, ok="categoria", ver=_tx_anchor(tx_id))


@app.post("/movimientos/nota")
def movement_note(
    tx_id: Annotated[str, Form()],
    note: Annotated[str, Form()] = "",
    q: Annotated[str, Form()] = "",
    mes: Annotated[str, Form()] = "",
    category_filter: Annotated[str, Form(alias="cat")] = "",
    session: SessionCookie = None,
):
    _require_auth(session)
    filters = _clean_filters(q, mes, category_filter)
    found = repo.update_ledger(lambda ledger: lg.set_note(ledger, tx_id, note) is not None)
    if not found:
        return _back_to_movements(filters, ok="no_encontrado")
    return _back_to_movements(filters, ok="nota" if note.strip() else "nota_borrada", ver=_tx_anchor(tx_id))


@app.post("/movimientos/automatica")
def movement_automatic(
    tx_id: Annotated[str, Form()],
    q: Annotated[str, Form()] = "",
    mes: Annotated[str, Form()] = "",
    category_filter: Annotated[str, Form(alias="cat")] = "",
    session: SessionCookie = None,
):
    _require_auth(session)
    filters = _clean_filters(q, mes, category_filter)
    rules = repo.load_rules()
    found = repo.update_ledger(lambda ledger: lg.reset_category(ledger, tx_id, rules) is not None)
    if not found:
        return _back_to_movements(filters, ok="no_encontrado")
    return _back_to_movements(filters, ok="automatica", ver=_tx_anchor(tx_id))


# ── Settings ─────────────────────────────────────────────────────────────────

def _budget_category(name: str) -> str:
    if name not in budget.BUDGETABLE:
        raise HTTPException(status_code=400, detail="Categoría desconocida.")
    return name


@app.get("/ajustes", response_class=HTMLResponse)
def settings_page(request: Request, sugerir: str | None = None, session: SessionCookie = None):
    _require_auth(session)
    ledger = repo.load_ledger()
    settings = budget.settings_view(repo.load_settings())
    today = _today()
    suggestion = budget.suggest_budgets(lg.month_summaries(ledger), budget.month_key(today))
    suggested = bool(sugerir) and bool(suggestion["suggested"])
    values = suggestion["suggested"] if suggested else settings["budgets"]
    fixed = set(settings["fixed_categories"])
    averages = suggestion["averages"]
    order = {c: i for i, c in enumerate(budget.BUDGETABLE)}
    rows = sorted(
        ({"name": c, "value": values.get(c), "fixed": c in fixed, "average": averages.get(c)}
         for c in budget.BUDGETABLE),
        key=lambda r: (not r["fixed"], -(r["average"] or 0), order[r["name"]]),
    )
    fixed_total = round(sum(v for c, v in values.items() if c in fixed), 2)
    total = round(sum(values.values()), 2)
    params = request.query_params
    return _render(request, "settings.html", {
        "rows": rows,
        "total": total,
        "fixed_total": fixed_total,
        "variable_total": round(total - fixed_total, 2),
        "goals": settings["goals"],
        "suggestion_months": suggestion["months"],
        "suggested": suggested,
        "ok": params.get("ok"),
        "error": params.get("error"),
        "error_category": params.get("cat"),
    }, ledger=ledger)


@app.post("/ajustes/presupuestos")
def save_budgets(
    categoria: Annotated[list[str], Form()],
    importe: Annotated[list[str], Form()],
    fijo: Annotated[list[str] | None, Form()] = None,
    session: SessionCookie = None,
):
    _require_auth(session)
    if len(categoria) != len(importe):
        raise HTTPException(status_code=400, detail="Formulario incompleto.")
    budgets: dict[str, float] = {}
    for name, text in zip(categoria, importe):
        _budget_category(name)
        try:
            amount = budget.parse_amount(text, budget.MAX_BUDGET)
        except ValueError:
            return _redirect("/ajustes", error="importe", cat=name)
        if amount:
            budgets[name] = amount
    fixed = [_budget_category(name) for name in (fijo or [])]
    now = repo.now_iso()
    repo.update_settings(lambda doc: budget.set_budgets(doc, budgets, fixed, now))
    return _redirect("/ajustes", ok="presupuesto")


@app.post("/ajustes/objetivos")
def save_goals(
    monthly_saving: Annotated[str, Form()] = "",
    annual_fund: Annotated[str, Form()] = "",
    fund_name: Annotated[str, Form()] = "",
    fund_note: Annotated[str, Form()] = "",
    session: SessionCookie = None,
):
    _require_auth(session)
    try:
        saving = budget.parse_amount(monthly_saving, budget.MAX_GOAL)
        fund = budget.parse_amount(annual_fund, budget.MAX_GOAL)
    except ValueError:
        return _redirect("/ajustes", error="objetivos")
    goals = {"monthly_saving": saving or 0.0, "annual_fund": fund or 0.0,
             "fund_name": fund_name, "fund_note": fund_note}
    now = repo.now_iso()
    repo.update_settings(lambda doc: budget.set_goals(doc, goals, now))
    return _redirect("/ajustes", ok="objetivos")


# ── API (JSON) ───────────────────────────────────────────────────────────────

@app.get("/api/data")
def api_data(session: SessionCookie = None):
    _require_auth(session)
    ledger = repo.load_ledger()
    return {"months": {m["month"]: m for m in lg.month_summaries(ledger)}}


@app.get("/healthz")
def healthz():
    return {"ok": True}
