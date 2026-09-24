"""
Minimal client for the Enable Banking API (PSD2 account information, read only).

Free "restricted production" mode gives access to accounts you link yourself
in the Enable Banking control panel. Setup steps are in DEPLOY.md.

Auth: every request carries a short-lived JWT signed (RS256) with the
application's private key; the key id ("kid") is the application id.

Configuration (environment):
  ENABLE_BANKING_APP_ID            application id from the control panel
  ENABLE_BANKING_PRIVATE_KEY       PEM contents (literal "\\n" are accepted), or
  ENABLE_BANKING_PRIVATE_KEY_PATH  path to the PEM file (e.g. a Render secret file)
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Iterator

import httpx
import jwt

API_URL = "https://api.enablebanking.com"
TOKEN_TTL_SECONDS = 3600


class EnableBankingError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Enable Banking {status}: {message}")
        self.status = status
        self.message = message


class SessionExpiredError(EnableBankingError):
    """The bank consent is no longer valid; the user has to authorise again."""


def _load_private_key() -> str | None:
    inline = os.environ.get("ENABLE_BANKING_PRIVATE_KEY")
    if inline:
        return inline.replace("\\n", "\n").strip() + "\n"
    path = os.environ.get("ENABLE_BANKING_PRIVATE_KEY_PATH")
    if path and Path(path).exists():
        return Path(path).read_text()
    return None


def is_configured() -> bool:
    return bool(os.environ.get("ENABLE_BANKING_APP_ID")) and _load_private_key() is not None


class EnableBankingClient:
    def __init__(
        self,
        app_id: str,
        private_key_pem: str,
        *,
        base_url: str = API_URL,
        http: httpx.Client | None = None,
    ):
        self.app_id = app_id
        self._key = private_key_pem
        self._http = http or httpx.Client(base_url=base_url, timeout=30.0)
        self._token: str | None = None
        self._token_exp = 0.0

    @classmethod
    def from_env(cls) -> "EnableBankingClient":
        app_id = os.environ.get("ENABLE_BANKING_APP_ID")
        key = _load_private_key()
        if not app_id or not key:
            raise RuntimeError("Faltan ENABLE_BANKING_APP_ID o la clave privada de Enable Banking.")
        return cls(app_id, key)

    def _auth_header(self) -> dict[str, str]:
        now = time.time()
        if self._token is None or now > self._token_exp - 60:
            iat = int(now)
            self._token = jwt.encode(
                {"iss": "enablebanking.com", "aud": "api.enablebanking.com",
                 "iat": iat, "exp": iat + TOKEN_TTL_SECONDS},
                self._key,
                algorithm="RS256",
                headers={"kid": self.app_id},
            )
            self._token_exp = iat + TOKEN_TTL_SECONDS
        return {"Authorization": f"Bearer {self._token}"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        resp = self._http.request(
            method, path, json=json, params=params,
            headers={**self._auth_header(), **(headers or {})},
        )
        if resp.status_code >= 400:
            try:
                body = resp.json()
                message = str(body.get("message") or body.get("error") or body)
            except ValueError:
                body, message = {}, resp.text[:300]
            code = str(body.get("code") or body.get("error") or "") if isinstance(body, dict) else ""
            expired = resp.status_code in (401, 403) or "EXPIRED" in (code + message).upper()
            raise (SessionExpiredError if expired else EnableBankingError)(resp.status_code, message)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # ── Endpoints ────────────────────────────────────────────────────────────

    def start_authorization(
        self,
        *,
        aspsp_name: str,
        country: str,
        redirect_url: str,
        state: str,
        valid_until: str,
        psu_type: str = "personal",
    ) -> dict[str, Any]:
        """Returns {"url": <bank login page>, "authorization_id": ...}."""
        return self._request("POST", "/auth", json={
            "access": {"valid_until": valid_until},
            "aspsp": {"name": aspsp_name, "country": country},
            "state": state,
            "redirect_url": redirect_url,
            "psu_type": psu_type,
        })

    def create_session(self, code: str) -> dict[str, Any]:
        """Exchange the code from the redirect for a session with its accounts."""
        return self._request("POST", "/sessions", json={"code": code})

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/sessions/{session_id}")

    def delete_session(self, session_id: str) -> None:
        self._request("DELETE", f"/sessions/{session_id}")

    def iter_transactions(
        self,
        account_uid: str,
        date_from: str,
        date_to: str | None = None,
        psu_headers: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """All transactions since date_from, following continuation keys."""
        params: dict[str, Any] = {"date_from": date_from}
        if date_to:
            params["date_to"] = date_to
        for _ in range(100):  # hard stop in case the API keeps returning a key
            page = self._request(
                "GET", f"/accounts/{account_uid}/transactions", params=params, headers=psu_headers,
            ) or {}
            yield from page.get("transactions") or []
            key = page.get("continuation_key")
            if not key:
                return
            params = {**params, "continuation_key": key}
