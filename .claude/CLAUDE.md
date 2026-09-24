# gastos-web — Project context for Claude

## What this is

Personal finance dashboard for Rodrigo & Rocío. Imports BBVA XLSX exports of the shared
account, categorises movements with sign-aware keyword rules plus rules the users teach it,
stores everything in AWS S3, and serves a FastAPI dashboard on Render.com. A direct bank
connection (Enable Banking) is planned for a later version.

## Architecture

- **`main.py`** — single FastAPI file. Routes: `/` (Inicio, `?mes=YYYY-MM` for other months), `/movimientos` (`?q=` text or amount, `?mes=YYYY-MM|todos`, `?cat=`; + `POST /movimientos/categoria`, `/movimientos/nota`, `/movimientos/automatica`, which redirect back to the same list with `?ver=` opening the row), `/analisis` (`?periodo=12m|YYYY|todo`), `/analisis/categoria?nombre=…`, `/ajustes` (+ `/ajustes/presupuestos`, `/ajustes/objetivos`; `?sugerir=1` pre-fills budgets with averages), `/upload`, `/delete-month`, `/revisar` (+ `/revisar/regla`, `/revisar/movimiento`, `/revisar/regla/borrar`), `/login`, `/logout`, `/api/data`, `/healthz`. `/corrections` redirects to `/revisar`. Also holds security: login rate limiting, same-origin check on POSTs, security headers, fail-fast secrets in production. "Today" is Europe/Madrid (`_today()`).
- **`services/categorizer.py`** — `classify()` decides a category from concept + amount sign (+ details; counterparty and MCC when a movement carries them). Order: user rules (longest pattern) → income keywords (credits only) → built-in keywords (whole words, longest wins, `~` weak, `*` prefix) → MCC → fallback (credit = Ingresos, debit = Otros needing review). `merchant_key()` gives the short merchant name used for grouping and rule suggestions.
- **`services/ledger.py`** — pure functions over the ledger: stable ids for statement rows, `import_transactions()` with dedupe (same id; also the same movement from another source or under a changed id, ready for a future bank feed), `apply_rules()`, monthly `summarize()`/`month_summaries()` (refunds reduce their category; only Ingresos is income), review groups, `search()` (text in concept/merchant/details/note ignoring case and accents, or an exact amount via `query_amount()`), `set_note()`, `reset_category()` (undo a manual category).
- **`services/budget.py`** — pure functions for budgets and goals: `settings_view()` (defaults, drops junk), `month_overview()` (everything Inicio shows: pace against the last movement, meter states ok/warn/over, fixed bills, projected saving, fund), `suggest_budgets()` (last 3 complete months, rounded up to 10 €), `fund_progress()` (positive balances of the closed months of the year), `parse_amount()` for Spanish-format form input.
- **`services/analysis.py`** — pure functions for Análisis: `select_period()` (12 months, a year or everything; closed months, the month in progress and the previous period), `overview()` (averages of closed months, goal met, changes against the previous period when it has 3+ months), `monthly()` (fixed/variable/income/saving per month), `category_rows()` (average, sparkline bars, budget; hides categories only spent in the month in progress), `category_detail()` (months, months over budget, top 4 merchants + the rest).
- **`services/parser.py`** — BBVA XLSX parser → rows (no month). Auto-detects the header row; picks columns left to right exactly like v1 so ids stay stable. `_parse_amount()` handles Spanish number format (1.234,56).
- **`services/storage.py`** — S3 or local-folder backend (`STORAGE_BACKEND=local`) with ETag conditional writes (`ConflictError`).
- **`services/repo.py`** — load/update of `ledger.json`, `rules.json` and `settings.json` with retry on conflict; migrates v1 files on first read; archives uploaded statements.
- **`services/migration.py`** — v1 (`data.json` + `merchant_rules.json`) → v2. Keeps rules and manual overrides, recategorises the rest, stores a report in `ledger.meta.migration`.
- **`services/insights.py`** — generates up to 6 dynamic insight cards from monthly data (MoM deltas, streaks, savings trajectory, best month). It is given closed months only; Análisis shows the first 3.
- **`templates/`** — Jinja2 HTML extending `base.html` (tabs Inicio · Movimientos · Revisar · Análisis · Subir, at the top on desktop and at the bottom on phones; Ajustes is the gear next to Salir; review badge): `home.html` (Inicio; categories link to their movements), `movements.html`, `analysis.html`, `category.html`, `settings.html` (Ajustes), `review.html`, `upload.html`, `login.html`. Chart.js loaded from CDN. No build step, no Node.
- **`static/app.css`** — shared styles and colour tokens, light by default and dark with the OS setting. Meter state colours and the chart pair were checked for colour-blind separation; keep new colours as tokens there. Charts read the tokens at runtime (and rebuild when the theme changes): `--c-fixed` (grey, fixed spending), `--c-var` (accent, variable spending and single-series bars), `--c-spark` (sparkline bars). The month in progress is drawn at 40 % opacity.
- **`tests/`** — pytest suite; runs offline with local storage.

## Data model

S3 `ledger.json` (every movement once; months are computed from dates):
```json
{
  "version": 2,
  "transactions": [
    {"id": "x:bbva-comun:3f2a…", "account": "bbva-comun", "date": "2026-09-20", "amount": -45.3,
     "concept": "MERCADONA VALENCIA", "counterparty": null, "details": "Pago con tarjeta",
     "balance": 954.7, "mcc": null, "merchant": "MERCADONA", "category": "Supermercado",
     "category_source": "keyword", "source": "xlsx", "imported_at": "…",
     "note": "optional, up to 140 characters"}
  ],
  "meta": {"migration": {"…": "…"}}
}
```
Ids: `x:` Excel rows (hash of date, amount, balance); other sources would use their own prefix, and duplicates found under another id are kept once with the extra id in `alt_ids`. `category_source`: `user` (set by hand, never overwritten), `rule`, `income`, `keyword`, `mcc`, `default`, `none` (= needs review).

S3 `settings.json` (optional; defaults apply until it is saved from Ajustes):
```json
{"version": 1, "budgets": {"Supermercado": 450, "Alquiler": 850},
 "fixed_categories": ["Alquiler", "Suministros", "Telefonía", "Seguros"],
 "goals": {"monthly_saving": 200, "annual_fund": 2400, "fund_name": "Fondo común", "fund_note": "Viajes · Hogar · Ocio"},
 "updated_at": "…"}
```
Budgets are euros per month for every month; Ingresos, Otros and Ajustes de cuenta never get one.

S3 `rules.json`:
```json
{"version": 2, "rules": [{"pattern": "BIKI BAT", "category": "Restaurantes", "created_at": "…"}]}
```
Patterns are normalised (uppercase, no accents) and matched as substrings; user rules are applied before built-in keywords, and saving or deleting one re-runs `apply_rules()` on the whole ledger.

v1 `data.json` and `merchant_rules.json` stay in the bucket as a backup after migration.

## Auth

Cookie-based session using `itsdangerous.URLSafeTimedSerializer`. The cookie is signed
with `SECRET_KEY` (env var). Single shared password stored in `APP_PASSWORD` (env var).
Cookie is `httponly`, `samesite=lax`, and `secure=True` only when `ENV=production`.

## Environment variables

All secrets come from environment variables. Never hardcode them. See `.env.example`.

Required: `SECRET_KEY`, `APP_PASSWORD` (the app refuses to start without them when `ENV=production`), `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET_NAME`.

Local development: `STORAGE_BACKEND=local`, `LOCAL_DATA_DIR`.

## Deployment

- **Hosting**: Render.com free tier (sleeps after 15 min of inactivity — expected behaviour).
- **Storage**: AWS S3 free tier. Bucket: private, no public access.
- Render reads `render.yaml` for build/start commands. Secrets set manually in Render dashboard.
- Every `git push origin main` triggers an automatic redeploy.

## Key decisions

- No database — S3 JSON is the data store. Simple, free, sufficient for 2 users and ~12 months of data.
- No frontend build step — plain HTML + Jinja2 + CDN Chart.js. Keeps deployment trivial.
- Categorisation is rule-based (no AI, by the users' choice): sign-aware, whole-word keywords, longest match wins, and rules learned from Revisar. Anything uncertain goes to Revisar instead of being guessed.
- S3 JSON with conditional writes instead of locks: every update re-reads and retries on conflict (`repo.update_*`).

## Spending categories

Alquiler · Suministros · Telefonía · Supermercado · Delivery · Restaurantes · Cafés y Snacks ·
Amazon/Online · Ocio/Cultura · Coche · Transporte público · Transporte privado · Salud · Ropa/Accesorios · Belleza ·
Viajes · Compras · Hogar · Seguros · Gasolinera · Efectivo · Comisiones · Ingresos · Ajustes de cuenta · Otros

## What to avoid

- Do not add a database unless data volume makes S3 JSON genuinely unworkable (it won't for this project).
- Do not add user accounts or complex auth — this is a two-person private tool.
- Do not add a frontend framework — plain Jinja2 + Chart.js is the right fit.
- Do not commit `.env`. It is in `.gitignore`. Use `.env.example` for documentation.
- Do not push unless asked to.
- Do not edit files without asking first. Always ask before making the changes. First design, then ask to edit.
