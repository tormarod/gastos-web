"""
Load and save the app's documents.

  ledger.json    every movement (services.ledger)
  rules.json     {"version": 2, "rules": [{"pattern", "category", "created_at"}]}
  settings.json  bank connection state (services.bank_sync)
  statements/    raw Excel files, archived on upload

Updates use optimistic concurrency: read with ETag, change, write only if
nobody else wrote in between, otherwise re-read and try again.

The first time the ledger is read, the v1 files (data.json and
merchant_rules.json) are migrated automatically and left in place as backup.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from services import ledger as lg
from services import migration
from services.storage import ConflictError, put_file, read_json, write_json

log = logging.getLogger(__name__)

LEDGER_KEY = "ledger.json"
RULES_KEY = "rules.json"
SETTINGS_KEY = "settings.json"
LEGACY_DATA_KEY = "data.json"
LEGACY_RULES_KEY = "merchant_rules.json"
STATEMENTS_PREFIX = "statements/"
MAX_ATTEMPTS = 6

T = TypeVar("T")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _update(
    key: str,
    read: Callable[[], tuple[Any, str | None]],
    fn: Callable[[Any], T],
) -> T:
    for _ in range(MAX_ATTEMPTS):
        doc, etag = read()
        result = fn(doc)
        try:
            write_json(key, doc, etag)
            return result
        except ConflictError:
            log.info("Conflict writing %s, retrying with fresh data", key)
    raise RuntimeError(f"No se pudo guardar {key}: otro proceso lo está modificando. Inténtalo de nuevo.")


# ── Rules ────────────────────────────────────────────────────────────────────

def _read_rules() -> tuple[dict[str, Any], str | None]:
    doc, etag = read_json(RULES_KEY)
    if doc is None:
        legacy, _ = read_json(LEGACY_RULES_KEY)
        return {"version": 2, "rules": migration.migrate_rules(legacy, now_iso())}, None
    return doc, etag


def load_rules() -> list[dict[str, str]]:
    return _read_rules()[0].get("rules", [])


def update_rules(fn: Callable[[list[dict[str, str]]], list[dict[str, str]]]) -> list[dict[str, str]]:
    def apply(doc: dict[str, Any]) -> list[dict[str, str]]:
        doc["rules"] = fn(doc.get("rules", []))
        return doc["rules"]

    return _update(RULES_KEY, _read_rules, apply)


# ── Ledger ───────────────────────────────────────────────────────────────────

def _read_ledger() -> tuple[lg.Ledger, str | None]:
    doc, etag = read_json(LEDGER_KEY)
    if doc is not None:
        return doc, etag
    migrated = _migrate_v1()
    if migrated is not None:
        return migrated
    return lg.new_ledger(), None


def _migrate_v1() -> tuple[lg.Ledger, str | None] | None:
    legacy, _ = read_json(LEGACY_DATA_KEY)
    if not legacy or not legacy.get("months"):
        return None
    legacy_rules, _ = read_json(LEGACY_RULES_KEY)
    rules_doc, _ = read_json(RULES_KEY)
    now = now_iso()
    rules = rules_doc.get("rules", []) if rules_doc else migration.migrate_rules(legacy_rules, now)
    if rules_doc is None:
        try:
            write_json(RULES_KEY, {"version": 2, "rules": rules}, None)
        except ConflictError:
            pass  # someone else migrated the rules at the same time
    ledger, report = migration.migrate_ledger(legacy, legacy_rules, rules, now)
    try:
        etag = write_json(LEDGER_KEY, ledger, None)
    except ConflictError:
        # Another request migrated at the same time: use its copy.
        doc, etag = read_json(LEDGER_KEY)
        if doc is not None:
            return doc, etag
        return ledger, None
    log.info("Migrated v1 data: %s", report)
    return ledger, etag


def load_ledger() -> lg.Ledger:
    return _read_ledger()[0]


def update_ledger(fn: Callable[[lg.Ledger], T]) -> T:
    """Apply fn to the ledger (mutating it) and save. fn may run more than once."""
    return _update(LEDGER_KEY, _read_ledger, fn)


# ── Settings ─────────────────────────────────────────────────────────────────

def _read_settings() -> tuple[dict[str, Any], str | None]:
    doc, etag = read_json(SETTINGS_KEY)
    return (doc if doc is not None else {"version": 1}), etag


def load_settings() -> dict[str, Any]:
    return _read_settings()[0]


def update_settings(fn: Callable[[dict[str, Any]], T]) -> T:
    return _update(SETTINGS_KEY, _read_settings, fn)


# ── Raw statements ───────────────────────────────────────────────────────────

def archive_statement(content: bytes) -> str:
    """Keep the uploaded Excel for reference. Returns its key."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha1(content).hexdigest()[:8]
    key = f"{STATEMENTS_PREFIX}{stamp}-{digest}.xlsx"
    put_file(key, content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return key
