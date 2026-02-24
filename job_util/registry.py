"""
Storage backend registry. Maps each JobStorageType to its module's public API lazily,
so backends are only imported when actually requested (and their heavy dependencies,
e.g. boto3 or minio, are not loaded for an unused backend)

Example:
    from job_util.registry import get_backend

    backend = get_backend()          # reads JOB_STORAGE_TYPE from env
    backend.download_config(task_name)
    backend.upload_result(task_name, path)
    backend.update_progress(progress)
"""
import os
import logging
import importlib
from typing import Protocol, cast

from .common import JobStorageType, Progress, MDProgress

logger = logging.getLogger(__name__)


class StorageBackend(Protocol):
    """
    Structural interface that every storage backend module must satisfy.
    """

    def download_config(self, task_name: str, local_path=None) -> dict:
        """Download job configuration JSON and return it as a dict."""
        ...

    def upload_result(self, task_name: str, file_or_dir_path) -> None:
        """Upload a result file or directory."""
        ...

    def update_progress(self, progress: "Progress | MDProgress") -> None:
        """Overwrite the stored progress snapshot."""
        ...


_REGISTRY: dict[JobStorageType, str] = {
    JobStorageType.S3: "job_util.s3_util",
    JobStorageType.MINIO: "job_util.minio_util",
}


def get_backend(storage_type: JobStorageType | None = None) -> StorageBackend:
    """
    Return the storage-backend module for *storage_type*.

    If *storage_type* is ``None`` the value is read from the
    ``JOB_STORAGE_TYPE`` environment variable (default: ``"S3"``).

    :param storage_type: Explicit backend to use, or ``None`` to read from env.
    :return: Imported backend module exposing ``download_config``,
             ``upload_result``, and ``update_progress``.
    :raises ValueError: If the storage type is not registered.
    """
    if storage_type is None:
        raw = os.environ.get("JOB_STORAGE_TYPE", JobStorageType.S3.value).upper()
        try:
            storage_type = JobStorageType(raw)
            logger.info(f"Using storage backend: {storage_type.value}")
        except ValueError:
            supported = ", ".join(t.value for t in JobStorageType)
            raise ValueError(f"Unsupported JOB_STORAGE_TYPE: {raw!r}. Supported types are: {supported}")

    module_path = _REGISTRY.get(storage_type)
    if module_path is None:
        # Defensive: catches any JobStorageType member added to the enum but
        # not yet registered here.
        supported = ", ".join(t.value for t in _REGISTRY)
        # f"{storage_type!r}"  → <JobStorageType.S3: 'S3'>
        raise ValueError(f"No backend registered for {storage_type!r}. Registered types: {supported}")

    return cast(StorageBackend, importlib.import_module(module_path))
