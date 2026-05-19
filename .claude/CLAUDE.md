# gastos-web — Project context for Claude

## What this is

Personal finance dashboard for Rodrigo & Rocío. Parses BBVA bank statement XLSX exports,
categorises transactions with keyword rules, stores everything in AWS S3, and serves a
FastAPI dashboard on Render.com.

## Architecture

- **`main.py`** — single FastAPI file. Routes: `/` (dashboard), `/upload`, `/login`, `/logout`, `/api/data`, `/corrections` (GET/POST), `/corrections/delete`.
- **`services/categorizer.py`** — keyword rules mapping BBVA concept strings → spending categories. Also exposes `suggest_pattern()` to strip BBVA prefixes for the correction UI.
- **`services/insights.py`** — generates up to 6 dynamic insight cards from monthly data (MoM deltas, streaks, savings trajectory, best month).
- **`services/parser.py`** — BBVA XLSX parser. Auto-detects the header row so it handles format variations. `_parse_amount()` handles Spanish number format (1.234,56).
- **`services/s3_store.py`** — all persistence. `data.json` holds all months; `merchant_rules.json` holds user-defined corrections; raw XLSX files go to `statements/<month>.xlsx`. Also exposes `load_custom_rules`, `save_custom_rules`, `recategorize_all`.
- **`templates/`** — Jinja2 HTML. Chart.js loaded from CDN. No build step, no Node.

## Data model

S3 `data.json`:
```json
{
  "months": {
    "2026-01": {
      "month": "2026-01",
      "transactions": [{ "date": "...", "concept": "...", "amount": -12.50, "balance": 1234.0, "category": "Supermercado" }],
      "summary": { "Supermercado": 295.0, "Delivery": 117.0 },
      "income": 2288.0,
      "total_expense": 2422.0,
      "balance": -134.0
    }
  }
}
```

S3 `merchant_rules.json`:
```json
{
  "rules": [
    { "pattern": "BIKI BAT", "category": "Restaurantes" },
    { "pattern": "BOOKING.COM", "category": "Viajes" }
  ]
}
```
Custom rules are applied before built-in keyword rules. `recategorize_all()` rewrites every stored transaction's category when rules change.

## Auth

Cookie-based session using `itsdangerous.URLSafeTimedSerializer`. The cookie is signed
with `SECRET_KEY` (env var). Single shared password stored in `APP_PASSWORD` (env var).
Cookie is `httponly`, `samesite=lax`, and `secure=True` only when `ENV=production`.

## Environment variables

All secrets come from environment variables. Never hardcode them. See `.env.example`.

Required: `SECRET_KEY`, `APP_PASSWORD`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET_NAME`.

## Deployment

- **Hosting**: Render.com free tier (sleeps after 15 min of inactivity — expected behaviour).
- **Storage**: AWS S3 free tier. Bucket: private, no public access.
- Render reads `render.yaml` for build/start commands. Secrets set manually in Render dashboard.
- Every `git push origin main` triggers an automatic redeploy.

## Key decisions

- No database — S3 JSON is the data store. Simple, free, sufficient for 2 users and ~12 months of data.
- No frontend build step — plain HTML + Jinja2 + CDN Chart.js. Keeps deployment trivial.
- Categorisation is keyword-based and intentionally simple. BBVA concept strings are consistent enough for this to work well.

## Spending categories

Alquiler · Suministros · Telefonía · Supermercado · Delivery · Restaurantes · Cafés y Snacks ·
Amazon/Online · Ocio/Cultura · Transporte · Salud · Ropa/Accesorios · Belleza ·
Viajes · Compras · Hogar · Seguros · Gasolinera · Efectivo · Comisiones · Ingresos · Ajustes de cuenta · Otros

## What to avoid

- Do not add a database unless data volume makes S3 JSON genuinely unworkable (it won't for this project).
- Do not add user accounts or complex auth — this is a two-person private tool.
- Do not add a frontend framework — plain Jinja2 + Chart.js is the right fit.
- Do not commit `.env`. It is in `.gitignore`. Use `.env.example` for documentation.
- Do not push unless asked to.
- Do not edit files without asking first. Always ask before making the changes. First design, then ask to edit.
