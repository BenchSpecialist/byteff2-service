import importlib
import sys
from typing import Any

import boto3


def _reload_s3_util() -> Any:
    if "job_util.s3_util" in sys.modules:
        del sys.modules["job_util.s3_util"]
    return importlib.import_module("job_util.s3_util")


def test_import_without_env(monkeypatch):
    """
    Test that s3_util can be imported without S3 environment variables set.
    This behavior is useful to allow running the code in environments where S3 is not used, without requiring dummy environment variables to be set.
    """

    monkeypatch.delenv("S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("S3_SECRET_KEY", raising=False)
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)

    _reload_s3_util()


def test_upload_config_lazy_manager(monkeypatch):
    """
    Test that the S3 client is created lazily and upload_config writes the correct object.
    """
    monkeypatch.setenv("S3_ACCESS_KEY", "test-access")
    monkeypatch.setenv("S3_SECRET_KEY", "test-secret")
    monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://example.com")

    call_count = {"count": 0}
    put_calls: list[dict[str, Any]] = []

    class DummyS3Client:

        def put_object(self, **kwargs):
            put_calls.append(kwargs)

    def fake_client(*args, **kwargs):
        call_count["count"] += 1
        return DummyS3Client()

    monkeypatch.setattr(boto3, "client", fake_client)

    s3_util = _reload_s3_util()
    s3_util._get_manager.cache_clear()

    assert call_count["count"] == 0

    s3_path = s3_util.upload_config("task-001", {"a": 1})

    assert call_count["count"] == 1
    assert s3_path == "test-bucket/configs/task-001/config.json"
    assert len(put_calls) == 1
