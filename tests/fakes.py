"""A stand-in for the Enable Banking API, used by the sync and app tests."""

from __future__ import annotations

from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from services import enable_banking as eb


def rsa_pem() -> tuple[str, Any]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return pem, key.public_key()


def eb_transaction(ref: str | None, date: str, amount: float, text: str, **extra: Any) -> dict[str, Any]:
    tx: dict[str, Any] = {
        "entry_reference": ref,
        "transaction_amount": {"currency": "EUR", "amount": f"{abs(amount):.2f}"},
        "credit_debit_indicator": "DBIT" if amount < 0 else "CRDT",
        "status": "BOOK",
        "booking_date": date,
        "value_date": date,
        "remittance_information": [text],
    }
    tx.update(extra)
    return tx


class FakeBank:
    def __init__(self, accounts: list[dict[str, Any]] | None = None):
        self.accounts = accounts if accounts is not None else [
            {"uid": "acc-1", "name": "Cuenta Común", "account_id": {"iban": "ES7600000000001234"}},
        ]
        self.transactions: list[dict[str, Any]] = []
        self.error: Exception | None = None
        self.calls: list[tuple[str, Any]] = []
        self.valid_until = "2027-03-22T00:00:00+00:00"

    def start_authorization(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("auth", kwargs))
        return {"url": "https://bank.example/login?session=abc", "authorization_id": "auth-1"}

    def create_session(self, code: str) -> dict[str, Any]:
        self.calls.append(("session", code))
        return {"session_id": f"sess-{code}", "accounts": self.accounts, "access": {"valid_until": self.valid_until}}

    def delete_session(self, session_id: str) -> None:
        self.calls.append(("delete", session_id))

    def iter_transactions(self, account_uid: str, date_from: str, date_to: str | None = None,
                          psu_headers: dict[str, str] | None = None):
        self.calls.append(("transactions", {"uid": account_uid, "from": date_from, "psu": psu_headers}))
        if self.error:
            raise self.error
        yield from self.transactions


def configure(monkeypatch, fake: FakeBank) -> None:
    """Make the app believe Enable Banking is configured and talk to `fake`."""
    from services import bank_sync

    monkeypatch.setenv("ENABLE_BANKING_APP_ID", "app-123")
    monkeypatch.setenv("ENABLE_BANKING_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----\\nfake\\n-----END PRIVATE KEY-----")
    monkeypatch.setattr(bank_sync, "client_factory", lambda: fake)


__all__ = ["FakeBank", "configure", "eb", "eb_transaction", "rsa_pem"]
