from datetime import datetime, timedelta, timezone

import pytest

from services import bank_sync, repo
from services import enable_banking as eb
from services import ledger as lg
from tests.fakes import FakeBank, configure, eb_transaction

NOW = datetime(2026, 9, 24, 7, 0, tzinfo=timezone.utc)


@pytest.fixture
def bank(monkeypatch):
    fake = FakeBank()
    configure(monkeypatch, fake)
    return fake


def connect(bank, now=NOW):
    bank_sync.start("https://app.example/banco/callback", now)
    state = repo.load_settings()["bank"]["pending"]["state"]
    return bank_sync.complete("code-1", state, now)


def test_map_transaction():
    debit = bank_sync.map_transaction(eb_transaction(
        "R1", "2026-09-20", -45.3, "COMPRA EN MERCADONA", creditor={"name": "MERCADONA SA"},
        merchant_category_code="5411", balance_after_transaction={"balance_amount": {"amount": "954.70"}},
        bank_transaction_code={"description": "Pago con tarjeta"},
    ), "now")
    assert debit is not None
    assert (debit["id"], debit["amount"], debit["date"], debit["balance"]) == ("b:bbva-comun:R1", -45.3, "2026-09-20", 954.7)
    assert (debit["counterparty"], debit["mcc"], debit["merchant"], debit["details"]) == (
        "MERCADONA SA", "5411", "MERCADONA SA", "Pago con tarjeta",
    )

    credit = bank_sync.map_transaction(eb_transaction(None, "2026-09-01", 1000, "", debtor={"name": "RODRIGO"}), "now")
    assert credit is not None
    assert credit["amount"] == 1000 and credit["concept"] == "RODRIGO" and credit["id"].startswith("b:bbva-comun:h")

    assert bank_sync.map_transaction(eb_transaction("P", "2026-09-22", -3, "PENDIENTE", status="PDNG"), "now") is None
    assert bank_sync.map_transaction({"transaction_amount": {"amount": "x"}}, "now") is None


def test_connect_then_sync(bank):
    url = bank_sync.start("https://app.example/banco/callback", NOW)
    assert url.startswith("https://bank.example/")
    auth = bank.calls[0][1]
    assert (auth["aspsp_name"], auth["country"], auth["redirect_url"]) == ("BBVA", "ES", "https://app.example/banco/callback")
    assert auth["valid_until"].startswith((NOW + timedelta(days=180)).date().isoformat())

    with pytest.raises(ValueError):
        bank_sync.complete("code-1", "wrong-state", NOW)

    state = repo.load_settings()["bank"]["pending"]["state"]
    saved = bank_sync.complete("code-1", state, NOW)
    assert (saved["session_id"], saved["account_uid"], saved["account_iban_tail"]) == ("sess-code-1", "acc-1", "1234")
    assert "pending" not in saved
    assert bank_sync.status(repo.load_settings(), NOW)["state"] == "active"

    bank.transactions = [
        eb_transaction("R1", "2026-09-20", -45.3, "MERCADONA VALENCIA"),
        eb_transaction("R2", "2026-09-21", -9.99, "COSA RARA SL"),
    ]
    first = bank_sync.sync(NOW, force=True, psu_headers={"Psu-Ip-Address": "1.2.3.4"})
    assert (first.status, first.added, first.needs_review, first.fetched) == ("ok", 2, 1, 2)
    call = bank.calls[-1][1]
    assert call["from"] == (NOW.date() - timedelta(days=bank_sync.FIRST_SYNC_DAYS)).isoformat()
    assert call["psu"] == {"Psu-Ip-Address": "1.2.3.4"}

    later = NOW + timedelta(hours=7)
    second = bank_sync.sync(later)
    assert (second.status, second.added, second.duplicates) == ("ok", 0, 2)
    assert bank.calls[-1][1]["from"] == (NOW.date() - timedelta(days=bank_sync.OVERLAP_DAYS)).isoformat()
    assert bank.calls[-1][1]["psu"] is None  # unattended
    assert repo.load_settings()["bank"]["last_result"]["duplicates"] == 2


def test_the_state_expires_after_an_hour(bank):
    bank_sync.start("https://app.example/cb", NOW)
    state = repo.load_settings()["bank"]["pending"]["state"]
    with pytest.raises(ValueError):
        bank_sync.complete("code", state, NOW + timedelta(hours=2))


def test_unattended_syncs_are_spaced(bank):
    connect(bank)
    assert bank_sync.sync(NOW).status == "ok"
    assert bank_sync.sync(NOW + timedelta(hours=1)).status == "skipped"
    assert bank_sync.sync(NOW + timedelta(hours=1), force=True).status == "ok"


def test_expired_consent(bank):
    connect(bank)
    bank.error = eb.SessionExpiredError(401, "Consent expired")
    result = bank_sync.sync(NOW, force=True)
    assert result.status == "expired"
    status = bank_sync.status(repo.load_settings(), NOW)
    assert status["state"] == "expired" and "Consent expired" in status["last_error"]
    assert not bank_sync.is_stale(repo.load_settings(), NOW + timedelta(days=2))


def test_other_errors_are_reported(bank):
    connect(bank)
    bank.error = eb.EnableBankingError(500, "Boom")
    assert bank_sync.sync(NOW, force=True).status == "error"
    assert bank_sync.status(repo.load_settings(), NOW)["state"] == "error"
    bank.error = None
    assert bank_sync.sync(NOW, force=True).status == "ok"
    assert bank_sync.status(repo.load_settings(), NOW)["state"] == "active"


def test_consent_close_to_expiry(bank):
    bank.valid_until = (NOW + timedelta(days=10)).isoformat()
    connect(bank)
    status = bank_sync.status(repo.load_settings(), NOW)
    assert (status["state"], status["days_left"]) == ("expiring", 10)
    assert bank_sync.status(repo.load_settings(), NOW + timedelta(days=11))["state"] == "expired"
    assert bank_sync.sync(NOW + timedelta(days=11), force=True).status == "expired"


def test_choose_the_shared_account_when_there_are_several(monkeypatch):
    fake = FakeBank(accounts=[
        {"uid": "acc-1", "name": "Personal", "account_id": {"iban": "ES00...1111"}},
        {"uid": "acc-2", "name": "Común", "account_id": {"iban": "ES00...2222"}},
    ])
    configure(monkeypatch, fake)
    connect(fake)
    assert bank_sync.status(repo.load_settings(), NOW)["state"] == "choose_account"
    assert bank_sync.sync(NOW, force=True).status == "not_connected"
    with pytest.raises(ValueError):
        bank_sync.choose_account("nope")
    bank_sync.choose_account("acc-2")
    assert bank_sync.sync(NOW, force=True).status == "ok"
    assert fake.calls[-1][1]["uid"] == "acc-2"

    # Reconnecting keeps the same account even if the bank returns new ids
    fake.accounts = [
        {"uid": "new-1", "name": "Personal", "account_id": {"iban": "ES00...1111"}},
        {"uid": "new-2", "name": "Común", "account_id": {"iban": "ES00...2222"}},
    ]
    assert connect(fake, NOW + timedelta(days=1))["account_uid"] == "new-2"


def test_excel_history_and_bank_sync_do_not_duplicate(bank):
    rows = [{"date": "2026-09-20", "concept": "MERCADONA VALENCIA", "amount": -45.3, "balance": 954.7}]
    repo.update_ledger(lambda l: lg.import_transactions(l, lg.rows_to_transactions(rows, lg.SHARED_ACCOUNT, "now")))
    connect(bank)
    bank.transactions = [eb_transaction("R1", "2026-09-21", -45.3, "Mercadona"), eb_transaction("R2", "2026-09-22", -2, "BAR")]
    result = bank_sync.sync(NOW, force=True)
    assert (result.added, result.duplicates) == (1, 1)
    assert len(repo.load_ledger()["transactions"]) == 2


def test_identical_movements_without_bank_reference_are_both_kept(bank):
    connect(bank)
    coffee = eb_transaction(None, "2026-09-20", -1.5, "CAFE")
    bank.transactions = [dict(coffee), dict(coffee)]
    assert bank_sync.sync(NOW, force=True).added == 2
    again = bank_sync.sync(NOW + timedelta(hours=7))
    assert (again.added, again.duplicates) == (0, 2)


def test_disconnect(bank):
    connect(bank)
    bank_sync.disconnect()
    assert ("delete", "sess-code-1") in bank.calls
    assert bank_sync.status(repo.load_settings(), NOW)["state"] == "not_connected"
    assert bank_sync.sync(NOW, force=True).status == "not_connected"


def test_a_session_without_accounts_explains_what_to_do(monkeypatch):
    fake = FakeBank(accounts=[])
    configure(monkeypatch, fake)
    bank_sync.start("https://app.example/cb", NOW)
    state = repo.load_settings()["bank"]["pending"]["state"]
    with pytest.raises(ValueError, match="vinculada"):
        bank_sync.complete("code", state, NOW)
    assert bank_sync.status(repo.load_settings(), NOW)["state"] == "not_connected"


def test_not_configured():
    assert bank_sync.status({}, NOW)["state"] == "not_configured"
