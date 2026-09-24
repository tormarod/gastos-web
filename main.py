from __future__ import annotations

import hmac
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Annotated, Any
from urllib.parse import urlencode, urlparse

from dotenv import load_dotenv
from fastapi import Cookie, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

load_dotenv()

from services import insights, parser, repo  # noqa: E402
from services import categorizer as cat  # noqa: E402
from services import ledger as lg  # noqa: E402

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


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Template helpers ─────────────────────────────────────────────────────────

MONTH_NAMES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _eur(value: Any, decimals: int = 2) -> str:
    """-1234.5 → '−1.234,50 €'"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    text = f"{abs(number):,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'−' if number < 0 else ''}{text} €"


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


templates.env.filters["eur"] = _eur
templates.env.filters["month_label"] = _month_label
templates.env.filters["short_date"] = _short_date
templates.env.globals["categories"] = cat.CATEGORIES


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


# ── Dashboard ────────────────────────────────────────────────────────────────

def _dashboard_context(ledger: lg.Ledger) -> dict[str, Any]:
    sorted_months = lg.month_summaries(ledger)

    all_categories: dict[str, float] = {}
    for m in sorted_months:
        for category, amount in m["summary"].items():
            all_categories[category] = round(all_categories.get(category, 0) + amount, 2)

    total_months = len(sorted_months)
    avg_categories = (
        {c: round(total / total_months, 2) for c, total in all_categories.items()}
        if total_months else {}
    )

    total_income = round(sum(m["income"] for m in sorted_months), 2)
    total_expenses = round(sum(m["total_expense"] for m in sorted_months), 2)
    total_balance = round(total_income - total_expenses, 2)

    monthly_series = [
        {"month": m["month"], "income": m["income"], "expense": m["total_expense"],
         "balance": m["balance"], "summary": m["summary"]}
        for m in sorted_months
    ]

    latest = sorted_months[-1] if sorted_months else None
    all_transactions = [
        {"date": tx.get("date"), "month": m["month"], "concept": tx.get("concept"),
         "category": tx.get("category"), "amount": tx["amount"]}
        for m in sorted_months
        for tx in m["transactions"]
    ]
    last_date = max((tx["date"] for tx in ledger["transactions"] if tx.get("date")), default=None)

    savings_accumulated = round(sum(m["balance"] for m in sorted_months if m["balance"] > 0), 2)
    savings_goal = 2400.0

    return {
        "sorted_months": sorted_months,
        "monthly_series": monthly_series,
        "avg_categories": avg_categories,
        "total_income": total_income,
        "total_expenses": total_expenses,
        "total_balance": total_balance,
        "avg_income": round(total_income / total_months, 2) if total_months else 0,
        "avg_expenses": round(total_expenses / total_months, 2) if total_months else 0,
        "avg_balance": round(total_balance / total_months, 2) if total_months else 0,
        "savings_accumulated": savings_accumulated,
        "savings_goal": savings_goal,
        "savings_pct": round(min(savings_accumulated / savings_goal * 100, 100), 1),
        "latest_transactions": latest["transactions"][:50] if latest else [],
        "latest_month": latest["month"] if latest else None,
        "year": (latest["month"][:4] if latest else str(_now().year)),
        "last_date": last_date,
        "all_transactions": all_transactions,
        "has_data": total_months > 0,
        "insight_cards": insights.generate(sorted_months),
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: SessionCookie = None):
    _require_auth(session)
    ledger = repo.load_ledger()
    return _render(request, "dashboard.html", _dashboard_context(ledger), ledger=ledger)


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


# ── API (JSON) ───────────────────────────────────────────────────────────────

@app.get("/api/data")
def api_data(session: SessionCookie = None):
    _require_auth(session)
    ledger = repo.load_ledger()
    return {"months": {m["month"]: m for m in lg.month_summaries(ledger)}}


@app.get("/healthz")
def healthz():
    return {"ok": True}
