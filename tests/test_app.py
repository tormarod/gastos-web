import os
import subprocess
import sys
from datetime import datetime

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
    for path in ("/", "/upload", "/revisar", "/api/data"):
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


def test_upload_import_and_dashboard(client):
    first = upload(client, SEPTEMBER)
    assert first.status_code == 303
    assert "added=3" in first.headers["location"] and "review=1" in first.headers["location"]
    again = upload(client, SEPTEMBER)
    assert "added=0" in again.headers["location"] and "dup=3" in again.headers["location"]

    page = client.get("/")
    assert page.status_code == 200
    assert "Plan Financiero 2026" in page.text
    assert "<script>alert(1)</script>" not in page.text  # statement text is escaped everywhere
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text
    assert "<b>1</b> movimiento que la app no ha sabido categorizar" in page.text

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
