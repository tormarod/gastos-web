# 💰 Gastos Web — Rodrigo & Rocío

Personal finance dashboard for tracking shared expenses and savings goals.
Reads the shared BBVA account automatically every morning (or from Excel exports),
categorises every movement and shows it as an interactive dashboard.

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.11 + FastAPI |
| Templates | Jinja2 + Chart.js |
| Storage | AWS S3 (JSON documents + archived statements), conditional writes |
| Bank data | Enable Banking (PSD2, read only) + BBVA Excel as fallback |
| Daily sync | GitHub Actions cron → `POST /api/sync` |
| Auth | Cookie session with signed token (shared password) |
| Hosting | Render.com (free tier) |

No database. All financial data lives in a few JSON files in S3.

## Features

- **Automatic bank sync** — connects to the shared BBVA account through Enable Banking and imports new movements every morning; warns before the bank consent expires
- **Excel import** — drop one or several BBVA `.xlsx` exports; no month to pick, overlapping statements never duplicate movements
- **Categorisation** — sign-aware rules (a debit is never income, refunds reduce their category), whole-word keyword matching, the bank's merchant code when available, and rules you teach it
- **Revisar** — only what the app couldn't categorise, grouped by merchant; one rule fixes every past and future movement; search to correct anything
- **Dashboard** — KPIs, monthly trend charts, category breakdown with drill-down, latest movements
- **Dynamic insights** — spending spikes, streaks, year-end savings projection
- **Savings goal tracker** — progress bar towards the €2,400/year shared fund
- **Secure** — password-protected with login rate limiting, HTTPS-only cookies in production, same-origin form checks, least-privilege S3 access

## Local development

```bash
# 1. Clone and install
git clone https://github.com/tormarod/gastos-web.git
cd gastos-web
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements-dev.txt

# 2. Create your .env from the template
cp .env.example .env
# Fill in .env. To try it without AWS, set STORAGE_BACKEND=local

# 3. Run
uvicorn main:app --reload
# → http://localhost:8000

# 4. Tests
pytest
```

## Deployment

See [`DEPLOY.md`](DEPLOY.md) for the full step-by-step guide (S3, Render, Enable Banking, daily sync).

**Environment variables on Render:**

| Variable | Description |
|---|---|
| `APP_PASSWORD` | Shared login password (required in production) |
| `SECRET_KEY` | Random string for cookie signing (required in production) |
| `AWS_ACCESS_KEY_ID` | IAM user key (bucket access only) |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret |
| `AWS_REGION` | e.g. `eu-west-1` |
| `S3_BUCKET_NAME` | e.g. `gastos-rodrigo-rocio` |
| `PUBLIC_URL` | e.g. `https://gastos-web.onrender.com` (bank redirect) |
| `ENABLE_BANKING_APP_ID` | Enable Banking application id |
| `ENABLE_BANKING_PRIVATE_KEY` | Contents of the application's `.pem` key |
| `SYNC_TOKEN` | Shared with the GitHub Actions cron that calls `/api/sync` |

## Daily workflow

Nothing, most days: movements arrive by themselves. When the **Revisar** badge shows a number,
open it and assign a category (or save a rule). About every six months, renew the bank consent
from **Banco** when the app asks.

## Project structure

```
gastos-web/
├── main.py                  # FastAPI app — routes, auth, security, upload, review, bank, API
├── services/
│   ├── categorizer.py       # Rules: BBVA concept (+ sign, counterparty, MCC) → category
│   ├── ledger.py            # Movements: ids, import & dedupe, monthly summaries, review groups
│   ├── parser.py            # BBVA XLSX parser (header-row auto-detection)
│   ├── repo.py              # Load/save ledger, rules, settings; v1 → v2 migration on first read
│   ├── migration.py         # data.json + merchant_rules.json → ledger.json + rules.json
│   ├── storage.py           # S3 (or local folder) with ETag conditional writes
│   ├── enable_banking.py    # Enable Banking API client (JWT, auth, sessions, transactions)
│   ├── bank_sync.py         # Connect, sync and consent status for the shared account
│   └── insights.py          # Dynamic insight cards
├── templates/
│   ├── base.html            # Layout, navigation, bank alerts
│   ├── login.html
│   ├── dashboard.html
│   ├── review.html          # Revisar: uncategorised movements, search, rules
│   ├── upload.html          # Excel import and saved months
│   └── bank.html            # Bank connection
├── tests/                   # pytest suite (no network or AWS needed)
└── .github/workflows/sync.yml  # Daily sync
```

## Data in S3

| Key | Content |
|---|---|
| `ledger.json` | Every movement once: date, amount, concept, category and where it came from |
| `rules.json` | Your rules: text pattern → category |
| `settings.json` | Bank connection state |
| `statements/` | Uploaded Excel files |
| `data.json`, `merchant_rules.json` | Version 1 files, kept as a backup after the migration |

## Categorisation rules

Each movement gets the first answer from:

1. **Your rules** (from Revisar) — the longest matching pattern wins.
2. **Income keywords**, credits only — NÓMINA, TRANSFERENCIA, BIZUM…
3. **Built-in keywords** in [`services/categorizer.py`](services/categorizer.py) — whole words, the longest keyword wins (ZARA HOME beats ZARA).
4. **Merchant category code** sent by the bank, when there is one.
5. Otherwise credits are income and debits go to **Revisar**.

| Category | Keywords matched (examples) |
|---|---|
| Alquiler | ALQUILER, ARRENDAMIENTO, COMUNIDAD DE PROPIETARIOS |
| Suministros | OCTOPUS, NATURGY, IBERDROLA, ENDESA, CANAL DE ISABEL… |
| Telefonía | DIGI, MOVISTAR, VODAFONE, ORANGE… |
| Supermercado | DIA, MERCADONA, ALCAMPO, LIDL, CARREFOUR… |
| Delivery | GLOVO, JUST EAT, UBER *EATS, BOLT FOOD, TELEPIZZA… |
| Restaurantes | RESTAURANTE, CAFETERIA, BAR, MCDONALDS, BURGER KING… |
| Cafés y Snacks | STARBUCKS, CAFE, PANADERIA, PASTELERIA, HELADERIA… |
| Amazon/Online | AMAZON, ALIEXPRESS, SHEIN, ZALANDO, PAYPAL… |
| Ocio/Cultura | NETFLIX, SPOTIFY, MOVISTAR PLUS, STEAM, CINESA… |
| Coche | PARKING, PEAJE, AUTOPISTA, ITV, TALLER… |
| Transporte público | METRO, EMT, RENFE, CERCANIAS, ABONO TRANSPORTE… |
| Transporte privado | CABIFY, UBER, BOLT, FREE NOW, TAXI… |
| Salud | FARMACIA, CLINICA, DENTISTA, SANITAS, QUIRONSALUD… |
| Ropa/Accesorios | ZARA, H&M, MANGO, PRIMARK, DECATHLON… |
| Belleza | SEPHORA, DOUGLAS, DRUNI, PELUQUERIA, BARBERIA… |
| Viajes | BOOKING, AIRBNB, HOTEL, VUELING, RYANAIR… |
| Compras | EL CORTE INGLES, FNAC, MEDIA MARKT, PAPELERIA… |
| Hogar | IKEA, LEROY MERLIN, ZARA HOME, FERRETERIA… |
| Seguros | MAPFRE, ALLIANZ, MUTUA MADRILEÑA, SEGUROS… |
| Gasolinera | REPSOL, BP, CEPSA, SHELL, GALP… |
| Efectivo | CAJERO, REINTEGRO, RETIRADA DE EFECTIVO… |
| Comisiones | COMISION, MANTENIMIENTO CUENTA, CUOTA TARJETA… |
| Ajustes de cuenta | AJUSTE, LIQUIDACION, REEMBOLSO, CUADRE… |
| Ingresos | NÓMINA, TRANSFERENCIA RECIBIDA, BIZUM RECIBIDO… (credits only) |

To add or adjust built-in keywords, edit [`services/categorizer.py`](services/categorizer.py) and run `pytest tests/test_categorizer.py`.
