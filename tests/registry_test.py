import pytest

from job_util.common import JobStorageType
from job_util.registry import get_backend


def test_get_backend_reads_env(monkeypatch):
    """
    Test that get_backend returns the correct backend module based on the
    JOB_STORAGE_TYPE environment variable.
    """
    monkeypatch.setenv("JOB_STORAGE_TYPE", "S3")
    backend = get_backend()
    import job_util.s3_util as expected
    assert backend is expected

    monkeypatch.setenv("JOB_STORAGE_TYPE", "MINIO")
    backend = get_backend()
    import job_util.minio_util as expected
    assert backend is expected


def test_get_backend_explicit_type():
    """Test that passing an explicit JobStorageType returns the correct module."""
    backend = get_backend(JobStorageType.S3)
    import job_util.s3_util as expected
    assert backend is expected

    backend = get_backend(JobStorageType.MINIO)
    import job_util.minio_util as expected
    assert backend is expected


def test_get_backend_invalid_env(monkeypatch):
    """Test that an unsupported JOB_STORAGE_TYPE env value raises ValueError."""
    monkeypatch.setenv("JOB_STORAGE_TYPE", "INVALID")
    with pytest.raises(ValueError, match="Unsupported JOB_STORAGE_TYPE"):
        get_backend()
