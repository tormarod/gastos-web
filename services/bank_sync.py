"""
Bank connection and sync for the shared BBVA account, through Enable Banking.

Flow:
  1. start()     → URL of the bank's login page (the user authorises there).
  2. The bank redirects to /banco/callback?code=…&state=…
  3. complete()  → exchanges the code for a session and stores it.
  4. sync()      → fetches booked movements since the last sync and imports
                   them into the ledger (duplicates are skipped).

State lives in settings.json under "bank":
  {"session_id", "valid_until", "accounts": [{"uid", "name", "iban_tail"}],
   "account_uid", "account_iban_tail", "status", "connected_at",
   "last_attempt_at", "last_sync_at", "last_result", "last_error", "pending"}

PSD2 allows about four unattended requests per account and day, so
unattended syncs (the daily cron) are spaced at least six hours apart.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

import httpx

from services import enable_banking as eb
from services import ledger as lg
from services import repo

log = logging.getLogger(__name__)

ASPSP_NAME = os.environ.get("BANK_ASPSP_NAME", "BBVA")
ASPSP_COUNTRY = os.environ.get("BANK_ASPSP_COUNTRY", "ES")
LEDGER_ACCOUNT = lg.SHARED_ACCOUNT
CONSENT_DAYS = (180, 90)          # tried in order; some banks cap consents at 90 days
PENDING_MAX_AGE = timedelta(hours=1)
UNATTENDED_MIN_INTERVAL = timedelta(hours=6)
FIRST_SYNC_DAYS = 89
OVERLAP_DAYS = 7
EXPIRY_WARNING_DAYS = 14

# Replaced in tests.
client_factory: Callable[[], eb.EnableBankingClient] = eb.EnableBankingClient.from_env


@dataclass
class SyncResult:
    status: str                    # ok | skipped | not_connected | expired | error
    added: int = 0
    duplicates: int = 0
    needs_review: int = 0
    fetched: int = 0
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iban_tail(account: Mapping[str, Any]) -> str:
    ids = account.get("account_id") or {}
    iban = ids.get("iban") if isinstance(ids, dict) else None
    return str(iban or "")[-4:]


def _account_summary(account: Any) -> dict[str, Any]:
    if isinstance(account, str):
        return {"uid": account, "name": "Cuenta", "iban_tail": ""}
    return {
        "uid": account.get("uid"),
        "name": account.get("name") or account.get("product") or "Cuenta",
        "iban_tail": _iban_tail(account),
    }


# ── Status for the UI ────────────────────────────────────────────────────────

def status(settings: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    bank = settings.get("bank") or {}
    valid_until = _parse_dt(bank.get("valid_until"))
    days_left = (valid_until - now).days if valid_until else None
    connected = bool(bank.get("session_id"))

    if not eb.is_configured():
        state = "not_configured"
    elif not connected:
        state = "not_connected"
    elif bank.get("status") == "expired" or (valid_until is not None and valid_until <= now):
        state = "expired"
    elif not bank.get("account_uid"):
        state = "choose_account"
    elif bank.get("status") == "error":
        state = "error"
    elif days_left is not None and days_left < EXPIRY_WARNING_DAYS:
        state = "expiring"
    else:
        state = "active"

    return {
        "state": state,
        "bank_name": ASPSP_NAME,
        "connected": connected,
        "valid_until": valid_until,
        "days_left": days_left,
        "accounts": bank.get("accounts") or [],
        "account_uid": bank.get("account_uid"),
        "last_sync_at": _parse_dt(bank.get("last_sync_at")),
        "last_result": bank.get("last_result"),
        "last_error": bank.get("last_error"),
    }


# ── Connecting ───────────────────────────────────────────────────────────────

def start(redirect_url: str, now: datetime) -> str:
    """Begin authorisation; returns the bank URL to send the user to."""
    client = client_factory()
    state = secrets.token_urlsafe(24)
    last_error: eb.EnableBankingError | None = None
    for days in CONSENT_DAYS:
        try:
            resp = client.start_authorization(
                aspsp_name=ASPSP_NAME,
                country=ASPSP_COUNTRY,
                redirect_url=redirect_url,
                state=state,
                valid_until=(now + timedelta(days=days)).isoformat(),
            )
            break
        except eb.EnableBankingError as exc:
            last_error = exc
            if exc.status not in (400, 422):
                raise
    else:
        assert last_error is not None
        raise last_error

    def remember(s: dict[str, Any]) -> None:
        s.setdefault("bank", {})["pending"] = {"state": state, "created_at": now.isoformat()}

    repo.update_settings(remember)
    return resp["url"]


def complete(code: str, state: str, now: datetime) -> dict[str, Any]:
    """Finish authorisation after the bank redirects back."""
    pending = (repo.load_settings().get("bank") or {}).get("pending") or {}
    created = _parse_dt(pending.get("created_at"))
    if (
        not pending.get("state")
        or not hmac.compare_digest(str(pending["state"]).encode(), str(state).encode())
        or created is None
        or now - created > PENDING_MAX_AGE
    ):
        raise ValueError("El enlace de autorización no es válido o ha caducado. Vuelve a conectar el banco.")

    session = client_factory().create_session(code)
    accounts = [_account_summary(a) for a in session.get("accounts") or []]
    if not accounts:
        raise ValueError(
            "El banco no ha compartido ninguna cuenta. En el modo gratuito de Enable Banking la cuenta "
            "tiene que estar vinculada antes en su panel («Activate by linking accounts»)."
        )
    valid_until = (session.get("access") or {}).get("valid_until")

    def apply(s: dict[str, Any]) -> dict[str, Any]:
        bank = s.setdefault("bank", {})
        previous_tail = bank.get("account_iban_tail")
        bank.update({
            "session_id": session["session_id"],
            "valid_until": valid_until,
            "accounts": accounts,
            "status": "active",
            "connected_at": now.isoformat(),
            "last_error": None,
        })
        bank.pop("pending", None)
        # Keep the account chosen before a reconnection; pick it if there is only one.
        same = [a for a in accounts if previous_tail and a["iban_tail"] == previous_tail]
        chosen = same[0] if same else (accounts[0] if len(accounts) == 1 else None)
        bank["account_uid"] = chosen["uid"] if chosen else None
        bank["account_iban_tail"] = chosen["iban_tail"] if chosen else None
        return bank

    return repo.update_settings(apply)


def choose_account(uid: str) -> None:
    def apply(s: dict[str, Any]) -> None:
        bank = s.setdefault("bank", {})
        account = next((a for a in bank.get("accounts") or [] if a["uid"] == uid), None)
        if account is None:
            raise ValueError("Esa cuenta no está en la conexión actual.")
        bank["account_uid"] = account["uid"]
        bank["account_iban_tail"] = account["iban_tail"]

    repo.update_settings(apply)


def disconnect() -> None:
    session_id = (repo.load_settings().get("bank") or {}).get("session_id")
    if session_id and eb.is_configured():
        try:
            client_factory().delete_session(session_id)
        except (eb.EnableBankingError, httpx.HTTPError) as exc:
            log.warning("Could not revoke the bank session: %s", exc)

    def apply(s: dict[str, Any]) -> None:
        bank = s.setdefault("bank", {})
        for key in ("session_id", "valid_until", "accounts", "account_uid", "pending"):
            bank.pop(key, None)
        bank["status"] = "disconnected"

    repo.update_settings(apply)


# ── Syncing ──────────────────────────────────────────────────────────────────

def map_transaction(t: Mapping[str, Any], imported_at: str) -> lg.Transaction | None:
    """Enable Banking transaction → ledger movement. Pending movements are skipped."""
    if str(t.get("status") or "BOOK").upper() not in ("BOOK", "BOOKED"):
        return None
    money = t.get("transaction_amount") or {}
    try:
        raw_amount = float(money.get("amount") or "")
    except (TypeError, ValueError):
        return None
    indicator = str(t.get("credit_debit_indicator") or "").upper()
    amount = -abs(raw_amount) if indicator == "DBIT" else abs(raw_amount) if indicator == "CRDT" else raw_amount

    tx_date = lg.iso_date(t.get("value_date") or t.get("booking_date") or t.get("transaction_date"))
    remittance = t.get("remittance_information") or []
    if isinstance(remittance, str):
        remittance = [remittance]
    concept = " ".join(str(r).strip() for r in remittance if r).strip()
    party = t.get("creditor") if amount < 0 else t.get("debtor")
    counterparty = party.get("name") if isinstance(party, dict) else None
    code = t.get("bank_transaction_code")
    code_text = code.get("description") if isinstance(code, dict) else None
    if not concept:
        concept = counterparty or code_text or t.get("note") or "Movimiento"
    details = code_text if code_text and code_text not in concept else None

    balance = None
    after = t.get("balance_after_transaction")
    if isinstance(after, dict):
        try:
            balance = float((after.get("balance_amount") or {}).get("amount") or "")
        except (TypeError, ValueError):
            balance = None

    ref = t.get("entry_reference") or t.get("transaction_id")
    if ref:
        tx_id = f"b:{LEDGER_ACCOUNT}:{ref}"
    else:
        digest = hashlib.sha1(f"{tx_date}|{amount:.2f}|{concept}|{balance}".encode()).hexdigest()[:20]
        tx_id = f"b:{LEDGER_ACCOUNT}:h{digest}"

    return lg.make_transaction(
        id=tx_id,
        account=LEDGER_ACCOUNT,
        date=tx_date,
        amount=amount,
        concept=concept,
        source="bank",
        imported_at=imported_at,
        balance=balance,
        details=details,
        counterparty=counterparty,
        mcc=str(t["merchant_category_code"]) if t.get("merchant_category_code") else None,
    )


def _number_repeated_ids(txs: list[lg.Transaction]) -> list[lg.Transaction]:
    """
    Without a bank reference, two identical coffees on the same day would get
    the same id. Number the repeats in the order the bank returns them, which
    is the same on every sync of an overlapping window.
    """
    seen: Counter[str] = Counter()
    for tx in txs:
        n = seen[tx["id"]]
        seen[tx["id"]] += 1
        if n:
            tx["id"] = f"{tx['id']}#{n}"
    return txs


def is_stale(settings: Mapping[str, Any], now: datetime, hours: int = 20) -> bool:
    """True when connected and the last sync attempt is older than `hours`."""
    bank = settings.get("bank") or {}
    if not bank.get("session_id") or not bank.get("account_uid") or bank.get("status") == "expired":
        return False
    last = _parse_dt(bank.get("last_attempt_at"))
    return last is None or now - last > timedelta(hours=hours)


def sync(now: datetime, *, force: bool = False, psu_headers: dict[str, str] | None = None) -> SyncResult:
    """
    Import new movements. `force` is for syncs the user asked for (button);
    pass their IP and user agent in psu_headers so the bank counts them as
    user-present requests.
    """
    bank = repo.load_settings().get("bank") or {}
    uid = bank.get("account_uid")
    if not bank.get("session_id") or not uid:
        return SyncResult("not_connected", message="No hay ninguna cuenta conectada.")

    valid_until = _parse_dt(bank.get("valid_until"))
    if bank.get("status") == "expired" or (valid_until is not None and valid_until <= now):
        _mark(now, status="expired", error="El permiso del banco ha caducado. Vuelve a conectar.")
        return SyncResult("expired", message="El permiso del banco ha caducado.")

    last_attempt = _parse_dt(bank.get("last_attempt_at"))
    if not force and last_attempt and now - last_attempt < UNATTENDED_MIN_INTERVAL:
        return SyncResult("skipped", message="Ya se sincronizó hace menos de 6 horas.")

    last_sync = _parse_dt(bank.get("last_sync_at"))
    if last_sync:
        since = last_sync.date() - timedelta(days=OVERLAP_DAYS)
    else:
        since = now.date() - timedelta(days=FIRST_SYNC_DAYS)
    _mark(now, attempt=True)

    imported_at = now.isoformat(timespec="seconds")
    try:
        raw = list(client_factory().iter_transactions(
            uid, since.isoformat(), psu_headers=psu_headers if force else None,
        ))
    except eb.SessionExpiredError as exc:
        _mark(now, status="expired", error=f"El banco ha rechazado el acceso: {exc.message}")
        return SyncResult("expired", message=exc.message)
    except (eb.EnableBankingError, httpx.HTTPError) as exc:
        _mark(now, status="error", error=str(exc))
        return SyncResult("error", message=str(exc))

    incoming = _number_repeated_ids(
        [tx for t in raw if (tx := map_transaction(t, imported_at)) is not None]
    )
    result = repo.update_ledger(
        lambda ledger: lg.import_transactions(ledger, incoming, repo.load_rules())
    )
    outcome = SyncResult(
        "ok", added=result.added, duplicates=result.duplicates,
        needs_review=result.needs_review, fetched=len(raw),
    )
    _mark(now, status="active", synced=outcome)
    log.info("Bank sync: %s", outcome.as_dict())
    return outcome


def _mark(
    now: datetime,
    *,
    attempt: bool = False,
    status: str | None = None,
    error: str | None = None,
    synced: SyncResult | None = None,
) -> None:
    def apply(s: dict[str, Any]) -> None:
        bank = s.setdefault("bank", {})
        if attempt:
            bank["last_attempt_at"] = now.isoformat()
        if status:
            bank["status"] = status
        if error is not None:
            bank["last_error"] = error
        if synced is not None:
            bank["last_sync_at"] = now.isoformat()
            bank["last_result"] = synced.as_dict()
            bank["last_error"] = None

    repo.update_settings(apply)
