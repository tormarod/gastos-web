import json

import httpx
import jwt
import pytest

from services import enable_banking as eb
from tests.fakes import rsa_pem


def client_with(handler):
    pem, public = rsa_pem()
    http = httpx.Client(base_url=eb.API_URL, transport=httpx.MockTransport(handler))
    return eb.EnableBankingClient("app-123", pem, http=http), public


def test_requests_are_signed_with_the_application_key():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"session_id": "s"})

    client, public = client_with(handler)
    client.get_session("s")
    token = seen["auth"].removeprefix("Bearer ")
    assert jwt.get_unverified_header(token)["kid"] == "app-123"
    claims = jwt.decode(token, public, algorithms=["RS256"], audience="api.enablebanking.com")
    assert claims["iss"] == "enablebanking.com"
    assert claims["exp"] - claims["iat"] == eb.TOKEN_TTL_SECONDS


def test_start_authorization_payload():
    seen = {}

    def handler(request):
        seen["path"], seen["body"] = request.url.path, json.loads(request.content)
        return httpx.Response(200, json={"url": "https://bank.example", "authorization_id": "a"})

    client, _ = client_with(handler)
    resp = client.start_authorization(
        aspsp_name="BBVA", country="ES", redirect_url="https://app/cb", state="st", valid_until="2027-01-01T00:00:00",
    )
    assert resp["url"] == "https://bank.example"
    assert seen["path"] == "/auth"
    assert seen["body"] == {
        "access": {"valid_until": "2027-01-01T00:00:00"},
        "aspsp": {"name": "BBVA", "country": "ES"},
        "state": "st", "redirect_url": "https://app/cb", "psu_type": "personal",
    }


def test_transactions_follow_continuation_keys():
    pages = {
        None: {"transactions": [{"entry_reference": "1"}], "continuation_key": "k2"},
        "k2": {"transactions": [{"entry_reference": "2"}], "continuation_key": None},
    }
    seen = []

    def handler(request):
        key = request.url.params.get("continuation_key")
        seen.append((request.url.path, request.url.params.get("date_from"), key, request.headers.get("psu-ip-address")))
        return httpx.Response(200, json=pages[key])

    client, _ = client_with(handler)
    txs = list(client.iter_transactions("acc", "2026-09-01", psu_headers={"Psu-Ip-Address": "1.2.3.4"}))
    assert [t["entry_reference"] for t in txs] == ["1", "2"]
    assert seen == [
        ("/accounts/acc/transactions", "2026-09-01", None, "1.2.3.4"),
        ("/accounts/acc/transactions", "2026-09-01", "k2", "1.2.3.4"),
    ]


@pytest.mark.parametrize("status, body, expected", [
    (401, {"message": "Unauthorized"}, eb.SessionExpiredError),
    (422, {"code": "EXPIRED_SESSION", "message": "Session expired"}, eb.SessionExpiredError),
    (500, {"message": "Boom"}, eb.EnableBankingError),
])
def test_errors(status, body, expected):
    client, _ = client_with(lambda request: httpx.Response(status, json=body))
    with pytest.raises(expected) as info:
        client.get_session("s")
    assert info.type is expected
    assert info.value.status == status


def test_configuration_from_environment(monkeypatch, tmp_path):
    pem, _ = rsa_pem()
    assert not eb.is_configured()
    monkeypatch.setenv("ENABLE_BANKING_APP_ID", "app-123")
    monkeypatch.setenv("ENABLE_BANKING_PRIVATE_KEY", pem.replace("\n", "\\n"))  # as pasted in a dashboard
    assert eb.is_configured()
    assert eb.EnableBankingClient.from_env()._key.startswith("-----BEGIN PRIVATE KEY-----\n")
    monkeypatch.delenv("ENABLE_BANKING_PRIVATE_KEY")
    key_file = tmp_path / "eb.pem"
    key_file.write_text(pem)
    monkeypatch.setenv("ENABLE_BANKING_PRIVATE_KEY_PATH", str(key_file))
    assert eb.EnableBankingClient.from_env()._key == pem
