# 💰 Gastos Web — Rodrigo & Rocío

Personal finance dashboard for tracking shared expenses and savings goals.
Parses BBVA bank statement exports and displays them as an interactive dashboard.

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.11 + FastAPI |
| Templates | Jinja2 + Chart.js |
| Storage | AWS S3 (statements + data as JSON) |
| Auth | Cookie session with signed token (shared password) |
| Hosting | Render.com (free tier) |

No database. All financial data lives in a single `data.json` file in S3.

## Features

- **Dashboard** — KPIs, monthly trend charts, category breakdown, transaction list
- **Upload** — drag-and-drop BBVA `.xlsx` export → auto-parsed and categorised → dashboard updates instantly
- **Savings goal tracker** — progress bar towards the €2,400/year shared fund
- **Secure** — password-protected, HTTPS-only in production, no data exposed publicly

## Local development

```bash
# 1. Clone and install
git clone https://github.com/tormarod/gastos-web.git
cd gastos-web
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt

# 2. Create your .env from the template
cp .env.example .env
# Fill in .env with your AWS credentials and chosen password

# 3. Run
uvicorn main:app --reload
# → http://localhost:8000
```

## Deployment

See [`DEPLOY.md`](DEPLOY.md) for the full step-by-step guide (AWS S3 setup + Render.com deploy).

**Environment variables required on Render:**

| Variable | Description |
|---|---|
| `APP_PASSWORD` | Shared login password |
| `SECRET_KEY` | Random 64-char hex string for cookie signing |
| `AWS_ACCESS_KEY_ID` | IAM user key (S3 access only) |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret |
| `AWS_REGION` | e.g. `eu-west-1` |
| `S3_BUCKET_NAME` | e.g. `gastos-rodrigo-rocio` |

## Monthly workflow

1. Log in to BBVA online → download the month's statement as **Excel (.xlsx)**
2. Open the app → **Subir Extracto**
3. Select the correct month, drop the file in
4. Dashboard updates immediately

## Project structure

```
gastos-web/
├── main.py                  # FastAPI app — routes, auth, upload handling
├── services/
│   ├── categorizer.py       # Keyword rules: BBVA concept → spending category
│   ├── parser.py            # BBVA XLSX parser (header-row auto-detection)
│   └── s3_store.py          # AWS S3 read/write (data.json + raw statements)
└── templates/
    ├── login.html
    ├── dashboard.html        # Dynamic dashboard with Chart.js
    └── upload.html
```

## Categorisation rules

Transactions are automatically tagged using keyword matching against the BBVA concept field:

| Category | Keywords matched |
|---|---|
| Alquiler | ALQUILER, ARRENDAMIENTO |
| Supermercado | DIA, MERCADONA, ALCAMPO, LIDL, CARREFOUR… |
| Delivery | GLOVO, JUSTEAT, UBER EATS, DOMINOS… |
| Restaurantes | RESTAURANTE, CAFETERIA, BAR, BIKI BAT… |
| Amazon/Online | AMAZON, ALIEXPRESS, SHEIN… |
| Suministros | OCTOPUS, NATURGY, IBERDROLA… |
| Telefonía | DIGI, MOVISTAR, VODAFONE… |
| Transporte | METRO, RENFE, EMT, CABIFY, UBER… |

To add or adjust rules, edit [`services/categorizer.py`](services/categorizer.py).
