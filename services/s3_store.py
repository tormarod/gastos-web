"""
AWS S3 data store.

All financial data is stored as a single JSON object at s3://<bucket>/data.json.
Uploaded XLSX files are stored at s3://<bucket>/statements/<month>.xlsx.

The data.json schema:
{
  "months": {
    "2026-01": { "month": ..., "transactions": [...], "summary": {...}, "income": 0, "total_expense": 0, "balance": 0 },
    ...
  }
}
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

DATA_KEY = "data.json"
CUSTOM_RULES_KEY = "merchant_rules.json"
STATEMENTS_PREFIX = "statements/"


def _client():
    return boto3.client(
        "s3",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("AWS_REGION", "eu-west-1"),
    )


def _bucket() -> str:
    return os.environ["S3_BUCKET_NAME"]


def load_data() -> dict[str, Any]:
    """Load the full data.json from S3. Returns empty structure if not found."""
    try:
        resp = _client().get_object(Bucket=_bucket(), Key=DATA_KEY)
        return json.loads(resp["Body"].read())
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return {"months": {}}
        raise


def save_data(data: dict[str, Any]) -> None:
    """Overwrite data.json in S3."""
    _client().put_object(
        Bucket=_bucket(),
        Key=DATA_KEY,
        Body=json.dumps(data, ensure_ascii=False).encode(),
        ContentType="application/json",
    )


def upload_statement(month_label: str, file_bytes: bytes) -> None:
    """Store the raw XLSX for archival under statements/<month>.xlsx."""
    key = f"{STATEMENTS_PREFIX}{month_label}.xlsx"
    _client().put_object(
        Bucket=_bucket(),
        Key=key,
        Body=file_bytes,
        ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def upsert_month(month_data: dict[str, Any]) -> None:
    """Insert or replace a month's data in data.json."""
    data = load_data()
    data["months"][month_data["month"]] = month_data
    save_data(data)


def delete_month(month_label: str) -> None:
    data = load_data()
    data["months"].pop(month_label, None)
    save_data(data)


# ── Custom merchant rules ────────────────────────────────────────────────────

def load_custom_rules() -> list[dict[str, str]]:
    """Load user-defined merchant → category rules. Returns [] if none saved."""
    try:
        resp = _client().get_object(Bucket=_bucket(), Key=CUSTOM_RULES_KEY)
        return json.loads(resp["Body"].read()).get("rules", [])
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return []
        raise


def save_custom_rules(rules: list[dict[str, str]]) -> None:
    _client().put_object(
        Bucket=_bucket(),
        Key=CUSTOM_RULES_KEY,
        Body=json.dumps({"rules": rules}, ensure_ascii=False).encode(),
        ContentType="application/json",
    )


def recategorize_all(custom_rules: list[dict[str, str]]) -> None:
    """
    Re-run categorization on every stored transaction using the current rules
    and rebuild each month's summary totals. Called after any rule change.
    """
    from services.categorizer import categorize

    data = load_data()
    for month_data in data["months"].values():
        for tx in month_data["transactions"]:
            tx["category"] = categorize(tx["concept"], custom_rules)

        summary: dict[str, float] = {}
        for tx in month_data["transactions"]:
            if tx["amount"] < 0:
                cat = tx["category"]
                summary[cat] = round(summary.get(cat, 0) + abs(tx["amount"]), 2)
        month_data["summary"] = summary

    save_data(data)
