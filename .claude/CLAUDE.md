# gastos-web — Project context for Claude

## What this is

Personal finance dashboard for Rodrigo & Rocío. Imports movements of the shared BBVA
account automatically (Enable Banking, PSD2, read only) or from XLSX exports, categorises
them with sign-aware keyword rules plus rules the users teach it, stores everything in
AWS S3, and serves a FastAPI dashboard on Render.com.

## Architecture

- **`main.py`** — single FastAPI file. Routes: `/` (dashboard), `/upload`, `/delete-month`, `/revisar` (+ `/revisar/regla`, `/revisar/movimiento`, `/revisar/regla/borrar`), `/banco` (+ `/banco/conectar`, `/banco/callback`, `/banco/cuenta`, `/banco/sincronizar`, `/banco/desconectar`), `/login`, `/logout`, `/api/sync` (cron, bearer token), `/api/data`, `/healthz`. `/corrections` redirects to `/revisar`. Also holds security: login rate limiting, same-origin check on POSTs, security headers, fail-fast secrets in production.
- **`services/categorizer.py`** — `classify()` decides a category from concept + amount sign (+ counterparty, details, MCC). Order: user rules (longest pattern) → income keywords (credits only) → built-in keywords (whole words, longest wins, `~` weak, `*` prefix) → MCC → fallback (credit = Ingresos, debit = Otros needing review). `merchant_key()` gives the short merchant name used for grouping and rule suggestions.
- **`services/ledger.py`** — pure functions over the ledger: stable ids for statement rows, `import_transactions()` with dedupe (same id, same movement from Excel and bank, bank ids that change), `apply_rules()`, monthly `summarize()`/`month_summaries()` (refunds reduce their category; only Ingresos is income), review groups.
- **`services/parser.py`** — BBVA XLSX parser → rows (no month). Auto-detects the header row; picks columns left to right exactly like v1 so ids stay stable. `_parse_amount()` handles Spanish number format (1.234,56).
- **`services/storage.py`** — S3 or local-folder backend (`STORAGE_BACKEND=local`) with ETag conditional writes (`ConflictError`).
- **`services/repo.py`** — load/update of `ledger.json`, `rules.json`, `settings.json` with retry on conflict; migrates v1 files on first read; archives uploaded statements.
- **`services/migration.py`** — v1 (`data.json` + `merchant_rules.json`) → v2. Keeps rules and manual overrides, recategorises the rest, stores a report in `ledger.meta.migration`.
- **`services/enable_banking.py`** — API client: RS256 JWT with the app key (`kid` = app id), `/auth`, `/sessions`, paginated `/accounts/{uid}/transactions`.
- **`services/bank_sync.py`** — connect (state check), choose account, sync (booked only, 6 h spacing for unattended calls, overlap window), consent status for the UI.
- **`services/insights.py`** — generates up to 6 dynamic insight cards from monthly data (MoM deltas, streaks, savings trajectory, best month).
- **`templates/`** — Jinja2 HTML extending `base.html` (nav, review badge, bank alerts). Chart.js loaded from CDN. No build step, no Node.
- **`tests/`** — pytest suite; runs offline with local storage and a fake bank (`tests/fakes.py`).
- **`.github/workflows/sync.yml`** — daily cron calling `POST /api/sync`.

## Data model

S3 `ledger.json` (every movement once; months are computed from dates):
```json
{
  "version": 2,
  "transactions": [
    {"id": "x:bbva-comun:3f2a…", "account": "bbva-comun", "date": "2026-09-20", "amount": -45.3,
     "concept": "MERCADONA VALENCIA", "counterparty": null, "details": "Pago con tarjeta",
     "balance": 954.7, "mcc": null, "merchant": "MERCADONA", "category": "Supermercado",
     "category_source": "keyword", "source": "xlsx", "imported_at": "…", "alt_ids": ["b:bbva-comun:R1"]}
  ],
  "meta": {"migration": {"…": "…"}}
}
```
Ids: `x:` Excel rows (hash of date, amount, balance), `b:` bank (`entry_reference`). `category_source`: `user` (set by hand, never overwritten), `rule`, `income`, `keyword`, `mcc`, `default`, `none` (= needs review).

S3 `rules.json`:
```json
{"version": 2, "rules": [{"pattern": "BIKI BAT", "category": "Restaurantes", "created_at": "…"}]}
```
Patterns are normalised (uppercase, no accents) and matched as substrings; user rules are applied before built-in keywords, and saving or deleting one re-runs `apply_rules()` on the whole ledger.

S3 `settings.json` → `bank`: session id, consent `valid_until`, accounts, chosen `account_uid`, last sync result/error.

v1 `data.json` and `merchant_rules.json` stay in the bucket as a backup after migration.

## Auth

Cookie-based session using `itsdangerous.URLSafeTimedSerializer`. The cookie is signed
with `SECRET_KEY` (env var). Single shared password stored in `APP_PASSWORD` (env var).
Cookie is `httponly`, `samesite=lax`, and `secure=True` only when `ENV=production`.

## Environment variables

All secrets come from environment variables. Never hardcode them. See `.env.example`.

Required: `SECRET_KEY`, `APP_PASSWORD` (the app refuses to start without them when `ENV=production`), `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET_NAME`.

Bank sync: `PUBLIC_URL`, `ENABLE_BANKING_APP_ID`, `ENABLE_BANKING_PRIVATE_KEY` (or `ENABLE_BANKING_PRIVATE_KEY_PATH`), `SYNC_TOKEN`.

Local development: `STORAGE_BACKEND=local`, `LOCAL_DATA_DIR`.

## Deployment

- **Hosting**: Render.com free tier (sleeps after 15 min of inactivity — expected behaviour).
- **Storage**: AWS S3 free tier. Bucket: private, no public access.
- Render reads `render.yaml` for build/start commands. Secrets set manually in Render dashboard.
- Every `git push origin main` triggers an automatic redeploy.
- Bank: Enable Banking restricted mode (free for own accounts). Consent lasts up to 180 days; the app warns 14 days before.
- Daily sync: GitHub Actions (`APP_URL` + `SYNC_TOKEN` secrets) → `POST /api/sync`. Opening the app also syncs if the last attempt is older than 20 h.

## Key decisions

- No database — S3 JSON is the data store. Simple, free, sufficient for 2 users and ~12 months of data.
- No frontend build step — plain HTML + Jinja2 + CDN Chart.js. Keeps deployment trivial.
- Categorisation is rule-based (no AI, by the users' choice): sign-aware, whole-word keywords, longest match wins, MCC as fallback, and rules learned from Revisar. Anything uncertain goes to Revisar instead of being guessed.
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
