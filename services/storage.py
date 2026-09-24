"""
Key/value storage for JSON documents and raw files.

Production uses a private S3 bucket. Writes are conditional on the ETag that
was read, so two writers (both of you saving at once, or two open tabs) can
never silently overwrite each other: the loser gets ConflictError and retries
with fresh data (see services.repo).

Set STORAGE_BACKEND=local to keep everything in a folder instead
(LOCAL_DATA_DIR, default ".data"). Useful for development and tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol


class ConflictError(Exception):
    """The object changed (or appeared) since it was read."""


class Backend(Protocol):
    def get(self, key: str) -> tuple[bytes | None, str | None]: ...

    def put(
        self,
        key: str,
        body: bytes,
        content_type: str,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str: ...


class S3Backend:
    def __init__(self, bucket: str, region: str, key_id: str | None, secret: str | None):
        import boto3

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
            region_name=region,
        )

    def get(self, key: str) -> tuple[bytes | None, str | None]:
        from botocore.exceptions import ClientError

        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None, None
            raise
        return resp["Body"].read(), resp.get("ETag")

    def put(
        self,
        key: str,
        body: bytes,
        content_type: str,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str:
        from botocore.exceptions import ClientError

        kwargs: dict[str, Any] = {
            "Bucket": self.bucket, "Key": key, "Body": body, "ContentType": content_type,
        }
        if if_match:
            kwargs["IfMatch"] = if_match
        elif if_none_match:
            kwargs["IfNoneMatch"] = "*"
        try:
            resp = self.client.put_object(**kwargs)
        except ClientError as e:
            error = e.response.get("Error", {})
            status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if error.get("Code") in ("PreconditionFailed", "ConditionalRequestConflict") or status in (409, 412):
                raise ConflictError(key) from e
            raise
        return resp.get("ETag", "")


class LocalBackend:
    """Files in a folder, with the same conditional-write semantics as S3."""

    _lock = threading.Lock()

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError(f"Invalid key: {key}")
        return path

    @staticmethod
    def _etag(body: bytes) -> str:
        return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'

    def get(self, key: str) -> tuple[bytes | None, str | None]:
        path = self._path(key)
        with self._lock:
            if not path.exists():
                return None, None
            body = path.read_bytes()
        return body, self._etag(body)

    def put(
        self,
        key: str,
        body: bytes,
        content_type: str,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str:
        path = self._path(key)
        with self._lock:
            exists = path.exists()
            if if_none_match and exists:
                raise ConflictError(key)
            if if_match is not None and (not exists or self._etag(path.read_bytes()) != if_match):
                raise ConflictError(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(body)
            tmp.replace(path)
        return self._etag(body)


def backend() -> Backend:
    kind = os.environ.get("STORAGE_BACKEND", "s3").lower()
    if kind == "local":
        return _local_backend(os.environ.get("LOCAL_DATA_DIR", ".data"))
    return _s3_backend(
        os.environ["S3_BUCKET_NAME"],
        os.environ.get("AWS_REGION", "eu-west-1"),
        os.environ.get("AWS_ACCESS_KEY_ID"),
        os.environ.get("AWS_SECRET_ACCESS_KEY"),
    )


@lru_cache(maxsize=4)
def _local_backend(root: str) -> LocalBackend:
    return LocalBackend(root)


@lru_cache(maxsize=4)
def _s3_backend(bucket: str, region: str, key_id: str | None, secret: str | None) -> S3Backend:
    return S3Backend(bucket, region, key_id, secret)


def read_json(key: str) -> tuple[Any | None, str | None]:
    """(document, etag), or (None, None) if it doesn't exist."""
    body, etag = backend().get(key)
    if body is None:
        return None, None
    return json.loads(body), etag


def write_json(key: str, data: Any, etag: str | None) -> str:
    """
    Write a document only if it hasn't changed since it was read with `etag`.
    With etag=None the document must not exist yet. Raises ConflictError.
    """
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    return backend().put(
        key, body, "application/json", if_match=etag, if_none_match=etag is None,
    )


def put_file(key: str, body: bytes, content_type: str) -> None:
    backend().put(key, body, content_type)
