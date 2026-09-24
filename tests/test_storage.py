import io

import pytest
from botocore.stub import Stubber

from services import repo, storage
from services.storage import ConflictError, LocalBackend, S3Backend


def test_local_backend_conditional_writes(tmp_path):
    b = LocalBackend(tmp_path)
    assert b.get("doc.json") == (None, None)
    etag = b.put("doc.json", b"1", "application/json", if_none_match=True)
    with pytest.raises(ConflictError):
        b.put("doc.json", b"2", "application/json", if_none_match=True)
    new_etag = b.put("doc.json", b"2", "application/json", if_match=etag)
    with pytest.raises(ConflictError):
        b.put("doc.json", b"3", "application/json", if_match=etag)  # stale
    assert b.get("doc.json") == (b"2", new_etag)
    with pytest.raises(ValueError):
        b.get("../escape.json")


def test_update_retries_when_someone_else_wrote_first(monkeypatch):
    storage.write_json("settings.json", {"version": 1, "a": 1}, None)
    real_write = repo.write_json
    calls = {"n": 0}

    def racing_write(key, data, etag):
        calls["n"] += 1
        if calls["n"] == 1:  # another process writes between our read and our write
            doc, current = storage.read_json(key)
            assert doc is not None
            doc["b"] = 2
            real_write(key, doc, current)
        return real_write(key, data, etag)

    monkeypatch.setattr(repo, "write_json", racing_write)
    repo.update_settings(lambda s: s.__setitem__("c", 3))
    assert storage.read_json("settings.json")[0] == {"version": 1, "a": 1, "b": 2, "c": 3}
    assert calls["n"] == 2  # first attempt conflicted, second succeeded


def test_update_gives_up_after_repeated_conflicts(monkeypatch):
    def always_conflict(key, data, etag):
        raise ConflictError(key)

    monkeypatch.setattr(repo, "write_json", always_conflict)
    with pytest.raises(RuntimeError, match="otro proceso"):
        repo.update_settings(lambda s: None)


@pytest.fixture
def s3():
    backend = S3Backend("bucket", "eu-west-1", "AKIAXXXXXXXXXXXXXXXX", "secret")
    with Stubber(backend.client) as stub:
        yield backend, stub


def test_s3_writes_are_conditional(s3):
    backend, stub = s3
    stub.add_response(
        "put_object", {"ETag": '"new"'},
        {"Bucket": "bucket", "Key": "ledger.json", "Body": b"{}", "ContentType": "application/json", "IfMatch": '"old"'},
    )
    stub.add_response(
        "put_object", {"ETag": '"first"'},
        {"Bucket": "bucket", "Key": "rules.json", "Body": b"{}", "ContentType": "application/json", "IfNoneMatch": "*"},
    )
    assert backend.put("ledger.json", b"{}", "application/json", if_match='"old"') == '"new"'
    assert backend.put("rules.json", b"{}", "application/json", if_none_match=True) == '"first"'


def test_s3_precondition_failed_is_a_conflict(s3):
    backend, stub = s3
    stub.add_client_error("put_object", service_error_code="PreconditionFailed", http_status_code=412)
    with pytest.raises(ConflictError):
        backend.put("ledger.json", b"{}", "application/json", if_match='"old"')


def test_s3_missing_object(s3):
    backend, stub = s3
    stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
    assert backend.get("ledger.json") == (None, None)
    stub.add_response("get_object", {"Body": io.BytesIO(b"{}"), "ETag": '"e"'}, {"Bucket": "bucket", "Key": "x.json"})
    assert backend.get("x.json") == (b"{}", '"e"')
