import os
import subprocess
import sys
import time
from datetime import date, datetime
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import main
from services import repo
from tests.conftest import ROOT, bbva_row, make_xlsx


@pytest.fixture
def client():
    main._login_failures.clear()
    c = TestClient(main.app)
    assert c.post("/login", data={"password": "test-password"}, follow_redirects=False).status_code == 303
    return c


def upload(client, rows, name="extracto.xlsx"):
    return client.post("/upload", files=[("files", (name, make_xlsx(rows), "application/octet-stream"))],
                       follow_redirects=False)


SEPTEMBER = [
    bbva_row(datetime(2026, 9, 20), "MERCADONA VALENCIA", -45.3, 954.7),
    bbva_row(datetime(2026, 9, 19), "<script>alert(1)</script>", -12.4, 1000.0),
    bbva_row(datetime(2026, 9, 1), "Rodrigo", 1000, 1012.4, movement="Transferencia recibida"),
]


def test_pages_require_login():
    c = TestClient(main.app)
    for path in ("/", "/movimientos", "/analisis", "/analisis/categoria?nombre=Supermercado", "/ajustes",
                 "/upload", "/revisar", "/api/data", "/apuntar"):
        response = c.get(path, follow_redirects=False)
        assert (response.status_code, response.headers["location"]) == (303, "/login")


def test_login_is_rate_limited():
    main._login_failures.clear()
    c = TestClient(main.app)
    for _ in range(main.LOGIN_MAX_FAILURES):
        assert c.post("/login", data={"password": "wrong"}).status_code == 401
    blocked = c.post("/login", data={"password": "test-password"}, follow_redirects=False)
    assert blocked.status_code == 429
    # Someone else failing elsewhere doesn't lock you out
    other = c.post("/login", data={"password": "test-password"}, headers={"true-client-ip": "203.0.113.9"},
                   follow_redirects=False)
    assert other.status_code == 303
    main._login_failures.clear()


def test_posts_need_login_too():
    c = TestClient(main.app)
    for path in ("/ajustes/presupuestos", "/ajustes/objetivos", "/movimientos/categoria", "/movimientos/nota",
                 "/movimientos/automatica", "/movimientos/efectivo", "/movimientos/borrar", "/apuntar",
                 "/apuntar/deshacer"):
        response = c.post(path, data={"categoria": "Hogar", "importe": "10", "tx_id": "x", "category": "Hogar"},
                          follow_redirects=False)
        assert (response.status_code, response.headers["location"]) == (303, "/login")


def test_upload_import_and_pages(client):
    first = upload(client, SEPTEMBER)
    assert first.status_code == 303
    assert "added=3" in first.headers["location"] and "review=1" in first.headers["location"]
    again = upload(client, SEPTEMBER)
    assert "added=0" in again.headers["location"] and "dup=3" in again.headers["location"]

    page = client.get("/movimientos", params={"mes": "todos"})
    assert page.status_code == 200
    assert "<script>alert(1)</script>" not in page.text  # statement text is escaped everywhere
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text
    assert "Por revisar" in page.text
    assert client.get("/analisis").status_code == 200

    data = client.get("/api/data").json()
    assert data["months"]["2026-09"]["income"] == 1000.0


def test_upload_rejects_files_that_are_not_statements(client):
    bad = client.post("/upload", files=[("files", ("notas.txt", b"hola", "text/plain"))])
    assert bad.status_code == 422 and "solo se aceptan ficheros .xlsx" in bad.text
    broken = client.post("/upload", files=[("files", ("roto.xlsx", b"no", "application/octet-stream"))])
    assert broken.status_code == 422 and "roto.xlsx" in broken.text


def test_review_rules_and_manual_categories(client):
    upload(client, SEPTEMBER)
    page = client.get("/revisar")
    assert "&lt;SCRIPT&gt;ALERT(1)&lt;/SCRIPT&gt;" in page.text

    saved = client.post("/revisar/regla", data={"pattern": "<script>alert", "category": "Ocio/Cultura"},
                        follow_redirects=False)
    assert saved.headers["location"] == "/revisar?ok=regla&n=1"
    assert "No hay nada pendiente" in client.get("/revisar").text
    assert [r["pattern"] for r in repo.load_rules()] == ["<SCRIPT>ALERT"]

    tx = next(t for t in repo.load_ledger()["transactions"] if t["concept"].startswith("MERCADONA"))
    moved = client.post("/revisar/movimiento", data={"tx_id": tx["id"], "category": "Hogar"}, follow_redirects=False)
    assert moved.headers["location"] == "/revisar?ok=movimiento"
    assert next(t for t in repo.load_ledger()["transactions"] if t["id"] == tx["id"])["category"] == "Hogar"

    assert "MERCADONA VALENCIA" in client.get("/revisar", params={"q": "mercadona"}).text
    assert client.post("/revisar/regla", data={"pattern": "X Y", "category": "Inventada"}).status_code == 400

    deleted = client.post("/revisar/regla/borrar", data={"pattern": "<script>alert"}, follow_redirects=False)
    assert deleted.headers["location"] == "/revisar?ok=borrada&n=1"
    assert client.get("/corrections", follow_redirects=False).headers["location"] == "/revisar"


def test_delete_month(client):
    upload(client, SEPTEMBER)
    response = client.post("/delete-month", data={"month": "2026-09"}, follow_redirects=False)
    assert response.headers["location"] == "/upload?deleted=2026-09&n=3"
    assert repo.load_ledger()["transactions"] == []


def test_posts_from_other_sites_are_rejected(client):
    response = client.post("/delete-month", data={"month": "2026-09"}, headers={"origin": "https://evil.example"})
    assert response.status_code == 403
    same_site = client.post("/delete-month", data={"month": "2026-09"}, headers={"origin": "http://testserver"},
                            follow_redirects=False)
    assert same_site.status_code == 303


def test_security_headers(client):
    response = client.get("/")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_production_refuses_to_start_without_secrets():
    env = {k: v for k, v in os.environ.items() if k not in ("APP_PASSWORD", "SECRET_KEY")}
    env["ENV"] = "production"
    result = subprocess.run([sys.executable, "-c", "import main"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "SECRET_KEY" in result.stderr


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr(main, "_today", lambda: date(2026, 9, 24))
    return date(2026, 9, 24)


AUGUST = [
    bbva_row(datetime(2026, 8, 30), "MERCADONA VALENCIA", -431.0, 2000.0),
    bbva_row(datetime(2026, 8, 3), "ALQUILER PISO", -850.0, 2431.0, movement="Transferencia realizada"),
    bbva_row(datetime(2026, 8, 1), "Rodrigo", 1500, 3281.0, movement="Transferencia recibida"),
]


def test_home_without_budget_invites_to_create_one(client, today):
    upload(client, SEPTEMBER + AUGUST)
    page = client.get("/")
    assert page.status_code == 200
    assert "Septiembre 2026" in page.text and "Poneos un presupuesto" in page.text
    assert "Datos hasta el 20 sep" in page.text
    assert "<script>alert" not in page.text
    assert 'href="/?mes=2026-08"' in page.text

    august = client.get("/", params={"mes": "2026-08"})
    assert "Agosto 2026" in august.text and "Mes completo" in august.text
    assert "Septiembre 2026" in client.get("/", params={"mes": "2031-01"}).text
    assert "Septiembre 2026" in client.get("/", params={"mes": "basura"}).text


def test_home_warns_when_data_is_old(client, today):
    upload(client, AUGUST)
    page = client.get("/")
    assert "Los últimos datos son del <b>30 ago</b>, hace 25 días." in page.text


def test_budget_suggestion_save_and_home(client, today):
    upload(client, SEPTEMBER + AUGUST)
    suggested = client.get("/ajustes", params={"sugerir": "1"})
    assert "Propuesta con la media de agosto" in suggested.text
    assert 'value="440"' in suggested.text  # 431 € of supermarket in August, rounded up

    form = {"categoria": ["Supermercado", "Alquiler", "Hogar"], "importe": ["440", "850", ""], "fijo": ["Alquiler"]}
    saved = client.post("/ajustes/presupuestos", data=form, follow_redirects=False)
    assert saved.headers["location"] == "/ajustes?ok=presupuesto"
    stored = repo.load_settings()
    assert stored["budgets"] == {"Alquiler": 850.0, "Supermercado": 440.0}
    assert stored["fixed_categories"] == ["Alquiler"]

    home = client.get("/")
    assert "de presupuesto" in home.text and "Por categoría" in home.text
    assert "Supermercado" in home.text and "Ritmo de los variables" in home.text
    assert "Poneos un presupuesto" not in home.text


def test_budget_form_validation(client):
    bad_amount = client.post("/ajustes/presupuestos", data={"categoria": "Hogar", "importe": "-5"},
                             follow_redirects=False)
    assert bad_amount.headers["location"] == "/ajustes?error=importe&cat=Hogar"
    unknown = client.post("/ajustes/presupuestos", data={"categoria": "Inventada", "importe": "5"})
    assert unknown.status_code == 400
    not_budgetable = client.post("/ajustes/presupuestos", data={"categoria": "Otros", "importe": "5"})
    assert not_budgetable.status_code == 400
    bad_fixed = client.post("/ajustes/presupuestos",
                            data={"categoria": "Hogar", "importe": "5", "fijo": "Ingresos"})
    assert bad_fixed.status_code == 400
    mismatched = client.post("/ajustes/presupuestos", data={"categoria": ["Hogar", "Salud"], "importe": "5"})
    assert mismatched.status_code == 400
    assert repo.load_settings() == {"version": 1}  # nothing was saved
    assert "no es válido" in client.get("/ajustes", params={"error": "importe", "cat": "Hogar"}).text


def test_goals_are_editable_and_used_by_the_analysis(client, today):
    saved = client.post("/ajustes/objetivos", data={
        "monthly_saving": "250", "annual_fund": "3.000", "fund_name": "Viaje a Japón", "fund_note": "Primavera",
    }, follow_redirects=False)
    assert saved.headers["location"] == "/ajustes?ok=objetivos"
    goals = repo.load_settings()["goals"]
    assert goals == {"monthly_saving": 250.0, "annual_fund": 3000.0, "fund_name": "Viaje a Japón", "fund_note": "Primavera"}

    bad = client.post("/ajustes/objetivos", data={"monthly_saving": "mucho", "annual_fund": "1"},
                      follow_redirects=False)
    assert bad.headers["location"] == "/ajustes?error=objetivos"

    upload(client, SEPTEMBER + AUGUST)
    analysis = client.get("/analisis").text
    assert "Viaje a Japón 2026" in analysis and "3.000\u00a0€" in analysis and "250\u00a0€" in analysis
    settings_page = client.get("/ajustes").text
    assert 'value="3000"' in settings_page and 'value="Viaje a Japón"' in settings_page


def test_every_page_renders(client, today):
    upload(client, SEPTEMBER + AUGUST)
    for path in ("/", "/movimientos", "/movimientos?q=mercadona", "/analisis", "/analisis?periodo=todo",
                 "/analisis/categoria?nombre=Supermercado", "/ajustes", "/upload", "/revisar", "/login",
                 "/apuntar"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "/static/app.css?v=" in response.text
        if path != "/login":
            assert '<span>Ajustes</span>' in response.text  # the gear always says what it is
            assert '<span>Salir</span>' in response.text
    assert client.get("/static/app.css").status_code == 200


def test_movements_default_month_search_and_filters(client, today):
    upload(client, SEPTEMBER + AUGUST)
    page = client.get("/movimientos").text
    assert "3 movimientos" in page and "Domingo 20 sep" in page and "Domingo 30 ago" not in page
    assert '<option value="2026-09" selected>' in page

    search = client.get("/movimientos", params={"q": "mercadona"}).text
    assert "2 movimientos" in search and "Domingo 30 ago" in search
    assert '<option value="todos" selected>' in search  # a new search looks in every month
    assert "<b>1 movimiento</b>" in client.get("/movimientos", params={"q": "mercadona", "mes": "2026-08"}).text
    assert "<b>1 movimiento</b>" in client.get("/movimientos", params={"q": "431"}).text  # an amount
    rent = client.get("/movimientos", params={"cat": "Alquiler", "mes": "todos"}).text
    assert "<b>1 movimiento</b>" in rent and "Alquiler Piso" in rent
    junk = client.get("/movimientos", params={"mes": "basura", "cat": "Inventada", "n": "x"}).text
    assert "3 movimientos" in junk and "Quitar filtros" not in junk


def test_movement_category_note_and_back_to_automatic(client, today):
    upload(client, SEPTEMBER)
    tx = next(t for t in repo.load_ledger()["transactions"] if t["concept"] == "MERCADONA VALENCIA")
    anchor = main._tx_anchor(tx["id"])

    moved = client.post("/movimientos/categoria", data={"tx_id": tx["id"], "category": "Hogar", "mes": "2026-09",
                                                         "cat": "Inventada", "q": ""}, follow_redirects=False)
    assert moved.headers["location"] == f"/movimientos?mes=2026-09&ok=categoria&ver={anchor}#{anchor}"
    stored = next(t for t in repo.load_ledger()["transactions"] if t["id"] == tx["id"])
    assert (stored["category"], stored["category_source"]) == ("Hogar", "user")
    page = client.get(moved.headers["location"].split("#")[0]).text
    assert f'id="{anchor}" open' in page and "puesta a mano" in page and "Volver a la categoría automática" in page

    back = client.post("/movimientos/automatica", data={"tx_id": tx["id"]}, follow_redirects=False)
    assert "ok=automatica" in back.headers["location"]
    stored = next(t for t in repo.load_ledger()["transactions"] if t["id"] == tx["id"])
    assert (stored["category"], stored["category_source"]) == ("Supermercado", "keyword")

    noted = client.post("/movimientos/nota", data={"tx_id": tx["id"], "note": "Compra de la semana", "q": "valencia"},
                        follow_redirects=False)
    assert noted.headers["location"].startswith("/movimientos?q=valencia&ok=nota&ver=")
    assert "Compra de la semana" in client.get("/movimientos", params={"q": "semana"}).text
    cleared = client.post("/movimientos/nota", data={"tx_id": tx["id"], "note": "  "}, follow_redirects=False)
    assert "ok=nota_borrada" in cleared.headers["location"]
    assert "note" not in next(t for t in repo.load_ledger()["transactions"] if t["id"] == tx["id"])

    missing = client.post("/movimientos/nota", data={"tx_id": "no-existe", "note": "x"}, follow_redirects=False)
    assert missing.headers["location"] == "/movimientos?ok=no_encontrado"
    bad = client.post("/movimientos/categoria", data={"tx_id": tx["id"], "category": "Inventada"})
    assert bad.status_code == 400


def test_analysis_periods_and_category_detail(client, today):
    upload(client, SEPTEMBER + AUGUST)
    page = client.get("/analisis").text
    assert "Agosto 2026," in page and "1 mes cerrado." in page and "Septiembre va a medias" in page
    assert 'href="/analisis?periodo=12m" aria-current="page"' in page
    assert 'href="/analisis/categoria?nombre=Supermercado&amp;periodo=12m"' in page
    assert "1.281 €" in page  # August: 431 € of groceries + 850 € of rent
    assert 'href="/analisis?periodo=12m" aria-current="page"' in client.get("/analisis", params={"periodo": "nope"}).text
    assert 'href="/analisis?periodo=2026" aria-current="page"' in client.get("/analisis", params={"periodo": "2026"}).text

    detail = client.get("/analisis/categoria", params={"nombre": "Supermercado"}).text
    assert "Mercadona" in detail and "2 veces" in detail
    assert 'href="/movimientos?mes=todos&amp;cat=Supermercado"' in detail
    assert client.get("/analisis/categoria", params={"nombre": "Ingresos"}).status_code == 404
    assert client.get("/analisis/categoria", params={"nombre": "<script>"}).status_code == 404


def test_home_categories_open_their_movements(client, today):
    upload(client, SEPTEMBER + AUGUST)
    client.post("/ajustes/presupuestos", data={"categoria": ["Supermercado", "Alquiler"], "importe": ["440", "850"],
                                               "fijo": ["Alquiler"]})
    home = client.get("/").text
    assert 'href="/movimientos?mes=2026-09&amp;cat=Supermercado"' in home
    assert 'href="/movimientos?mes=2026-09&amp;cat=Alquiler"' in home


def test_movements_without_data_invite_to_upload(client):
    page = client.get("/movimientos").text
    assert "Todavía no hay movimientos." in page and 'href="/upload"' in page


# ── Cash written down by hand ────────────────────────────────────────────────

WITHDRAWAL = [bbva_row(datetime(2026, 9, 5), "RETIRADA EFECTIVO CAJERO", -100, 900.0)]


def cash_rows():
    return [t for t in repo.load_ledger()["transactions"] if t["account"] == "efectivo"]


def test_add_cash_expense_and_undo(client, today):
    upload(client, WITHDRAWAL)
    page = client.get("/apuntar").text
    assert "Gasto en efectivo" in page and 'id="keypad"' in page and "Supermercado" in page
    assert "Del cajero, sin apuntar (último mes): <b class=\"num\">100,00\u00a0€</b>" in page

    saved = client.post("/apuntar", data={"importe": "12,50", "categoria": "Supermercado", "donde": " Frutería ",
                                          "dia": "hoy"}, follow_redirects=False)
    assert saved.status_code == 303
    [tx] = cash_rows()
    assert saved.headers["location"] == "/apuntar?" + urlencode({"ok": tx["id"]})
    assert (tx["amount"], tx["date"], tx["merchant"], tx["category"]) == (-12.5, "2026-09-24", "Frutería", "Supermercado")
    page = client.get(saved.headers["location"]).text
    assert "Apuntados <b class=\"num\">12,50\u00a0€</b> en Supermercado, hoy." in page
    assert "87,50\u00a0€" in page and "Últimos apuntados" in page

    # It moves money from Efectivo to Supermercado: the month total doesn't change
    sep = client.get("/api/data").json()["months"]["2026-09"]
    assert sep["summary"] == {"Efectivo": 87.5, "Supermercado": 12.5} and sep["total_expense"] == 100.0

    undone = client.post("/apuntar/deshacer", data={"tx_id": tx["id"]}, follow_redirects=False)
    assert undone.headers["location"] == "/apuntar?ok=deshecho" and cash_rows() == []
    again = client.post("/apuntar/deshacer", data={"tx_id": tx["id"]}, follow_redirects=False)
    assert again.headers["location"] == "/apuntar?ok=no_encontrado"


def test_add_cash_validation(client, today):
    cases = [
        ({"importe": "0", "categoria": "Supermercado"}, "Escribid un importe mayor que 0"),
        ({"importe": "12.000,00", "categoria": "Supermercado"}, "Escribid un importe mayor que 0"),
        ({"importe": "doce", "categoria": "Supermercado"}, "Escribid un importe mayor que 0"),
        ({"importe": "12", "categoria": "Efectivo"}, "Elegid una categoría"),
        ({"importe": "12", "categoria": "Ingresos"}, "Elegid una categoría"),
        ({"importe": "12", "categoria": "Hogar", "dia": "otro", "fecha": "2026-09-25"}, "no puede ser en el futuro"),
    ]
    for data, message in cases:
        response = client.post("/apuntar", data={"donde": "Mercado", **data})
        assert response.status_code == 422, data
        assert message in response.text and "No se ha apuntado nada." in response.text
        assert 'value="Mercado"' in response.text  # what you typed is still there
    assert cash_rows() == []
    ok = client.post("/apuntar", data={"importe": "1.234,5", "categoria": "Hogar", "dia": "otro", "fecha": "2026-08-31"},
                     follow_redirects=False)
    assert ok.status_code == 303
    [tx] = cash_rows()
    assert (tx["amount"], tx["date"], tx["merchant"]) == (-1234.5, "2026-08-31", "Gasto en efectivo")
    yesterday = client.post("/apuntar", data={"importe": "3", "categoria": "Otros", "dia": "ayer"}, follow_redirects=False)
    assert yesterday.status_code == 303
    assert sorted(t["date"] for t in cash_rows()) == ["2026-08-31", "2026-09-23"]


def test_cash_in_movements_can_be_changed_or_deleted(client, today):
    upload(client, SEPTEMBER + WITHDRAWAL)
    client.post("/apuntar", data={"importe": "12,50", "categoria": "Supermercado", "donde": "Frutería"})
    [tx] = cash_rows()
    anchor = main._tx_anchor(tx["id"])
    page = client.get("/movimientos").text
    assert "Frutería" in page and "efectivo</span>" in page and "87,50\u00a0€ sin apuntar" in page
    assert "Borrar este gasto" in page
    # the month total counts the withdrawal only for what isn't written down
    assert "salen <b class=\"num\">157,70\u00a0€</b>" in page

    changed = client.post("/movimientos/efectivo", data={
        "tx_id": tx["id"], "importe": "15", "fecha": "2026-09-23", "donde": "Mercado", "category": "Hogar",
        "mes": "2026-09"}, follow_redirects=False)
    assert changed.headers["location"] == f"/movimientos?mes=2026-09&ok=efectivo&ver={anchor}#{anchor}"
    [tx] = cash_rows()
    assert (tx["amount"], tx["date"], tx["merchant"], tx["category"]) == (-15.0, "2026-09-23", "Mercado", "Hogar")

    for bad in ({"importe": "0"}, {"fecha": "2026-10-01"}, {"category": "Efectivo"}):
        data = {"tx_id": tx["id"], "importe": "15", "fecha": "2026-09-23", "category": "Hogar", **bad}
        assert "ok=efectivo_mal" in client.post("/movimientos/efectivo", data=data, follow_redirects=False).headers["location"]
    assert client.post("/movimientos/categoria", data={"tx_id": tx["id"], "category": "Ingresos"}).status_code == 400

    # Movements from the bank can't be changed or deleted this way
    bank = next(t for t in repo.load_ledger()["transactions"] if t["concept"] == "MERCADONA VALENCIA")
    data = {"tx_id": bank["id"], "importe": "1", "fecha": "2026-09-01", "category": "Hogar"}
    assert "ok=no_encontrado" in client.post("/movimientos/efectivo", data=data, follow_redirects=False).headers["location"]
    assert "ok=no_encontrado" in client.post("/movimientos/borrar", data={"tx_id": bank["id"]},
                                             follow_redirects=False).headers["location"]

    deleted = client.post("/movimientos/borrar", data={"tx_id": tx["id"], "mes": "2026-09"}, follow_redirects=False)
    assert deleted.headers["location"] == "/movimientos?mes=2026-09&ok=borrado"
    assert cash_rows() == [] and len(repo.load_ledger()["transactions"]) == 4


def test_cash_does_not_make_the_bank_data_look_up_to_date(client, today):
    upload(client, SEPTEMBER)
    client.post("/apuntar", data={"importe": "3", "categoria": "Cafés y Snacks"})
    page = client.get("/").text
    assert "Datos hasta el 20 sep" in page


def test_deleting_a_month_keeps_cash(client, today):
    upload(client, SEPTEMBER)
    client.post("/apuntar", data={"importe": "3", "categoria": "Cafés y Snacks"})
    page = client.get("/upload").text
    assert "3 movimientos y 1 apuntado a mano" in page and "Lo apuntado a mano se queda." in page
    client.post("/delete-month", data={"month": "2026-09"})
    assert len(cash_rows()) == 1 and len(repo.load_ledger()["transactions"]) == 1


# ── Installing on the phone ──────────────────────────────────────────────────

def test_install_files_need_no_password_and_carry_no_data():
    c = TestClient(main.app)
    manifest = c.get("/manifest.webmanifest")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    data = manifest.json()
    assert (data["short_name"], data["start_url"], data["display"]) == ("Gastos", "/", "standalone")
    assert {i["sizes"] for i in data["icons"]} >= {"192x192", "512x512"}
    assert any(i.get("purpose") == "maskable" for i in data["icons"])
    assert data["shortcuts"][0]["url"] == "/apuntar"
    for icon in data["icons"] + data["shortcuts"][0]["icons"]:
        assert c.get(icon["src"]).status_code == 200, icon["src"]

    worker = c.get("/sw.js")
    assert worker.status_code == 200
    assert worker.headers["content-type"].startswith("application/javascript")
    assert worker.headers["cache-control"] == "no-cache"
    assert f"gastos-{main._asset_version()}" in worker.text and "'/espera'" in worker.text
    assert f"SLOW_MS = {main.WAKE_UP_WAIT_MS};" in worker.text

    waiting = c.get("/espera")
    assert waiting.status_code == 200
    assert "Despertando el servidor" in waiting.text and "Sin conexión" in waiting.text
    assert "<svg" in waiting.text and "fonts.googleapis" not in waiting.text  # works offline on its own


def test_pages_link_the_manifest_and_the_service_worker(client, today):
    for path in ("/", "/login", "/apuntar"):
        page = client.get(path).text
        assert '<link rel="manifest" href="/manifest.webmanifest">' in page, path
        assert "serviceWorker.register('/sw.js')" in page and 'rel="apple-touch-icon"' in page
    assert 'id="instalar" hidden' in client.get("/ajustes").text


def test_a_session_in_use_is_renewed_once_a_day(monkeypatch):
    c = TestClient(main.app)
    fresh = main._make_session_token()
    c.cookies.set(main.COOKIE_NAME, fresh)
    assert "set-cookie" not in c.get("/").headers  # signed today: nothing to renew

    two_days_ago = time.time() - 2 * 86400
    monkeypatch.setattr("itsdangerous.timed.time.time", lambda: two_days_ago)
    old = main._make_session_token()
    monkeypatch.undo()
    c.cookies.set(main.COOKIE_NAME, old)
    renewed = c.get("/", follow_redirects=False)
    assert renewed.status_code == 200
    assert f"{main.COOKIE_NAME}=" in renewed.headers["set-cookie"] and "Max-Age=2592000" in renewed.headers["set-cookie"]
    new_token = renewed.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    age = main._session_age(new_token)
    assert new_token != old and age is not None and age < 60

    # Expired sessions are not brought back
    a_month_ago = time.time() - 31 * 86400
    monkeypatch.setattr("itsdangerous.timed.time.time", lambda: a_month_ago)
    expired = main._make_session_token()
    monkeypatch.undo()
    c2 = TestClient(main.app)
    c2.cookies.set(main.COOKIE_NAME, expired)
    response = c2.get("/", follow_redirects=False)
    assert response.headers["location"] == "/login" and "set-cookie" not in response.headers
