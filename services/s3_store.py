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
