"""
MinIO File Manager for uploading/downloading job configurations and results on
the local server.

Environment Variables Required:
- MINIO_ENDPOINT:   MinIO server endpoint (e.g. ``minio.example.com:9000``)
- MINIO_ACCESS_KEY: MinIO access key ID
- MINIO_SECRET_KEY: MinIO secret access key
- MINIO_BUCKET:     Target bucket name
"""
import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional
import os

from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)

_DEFAULT_BUCKET_NAME = "byteff2-jobs"

MINIO_ENDPOINT: Optional[str] = os.environ.get("MINIO_ENDPOINT")
MINIO_ACCESS_KEY: Optional[str] = os.environ.get("MINIO_ACCESS_KEY")
MINIO_SECRET_KEY: Optional[str] = os.environ.get("MINIO_SECRET_KEY")
BUCKET: str = os.environ.get("MINIO_BUCKET", _DEFAULT_BUCKET_NAME)


class MinioFileManager:
    """Service class for interacting with a MinIO-compatible object store."""

    def __init__(self, secure: bool = True) -> None:
        """
        Initialize the MinIO client.

        :param secure: Whether to use TLS (HTTPS). Defaults to ``True``.
        :raises ValueError: If any required environment variable is missing.
        :raises RuntimeError: If the MinIO client cannot be initialised.
        """
        missing = [
            name for name, val in [
                ("MINIO_ENDPOINT", MINIO_ENDPOINT),
                ("MINIO_ACCESS_KEY", MINIO_ACCESS_KEY),
                ("MINIO_SECRET_KEY", MINIO_SECRET_KEY),
            ] if not val
        ]
        if missing:
            raise ValueError(f"Required environment variable(s) not set: {', '.join(missing)}. "
                             "Please configure them in your environment.")

        try:
            self.client = Minio(MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, secure=secure)
        except Exception as exc:
            raise RuntimeError(f"Failed to initialise MinIO client: {exc}") from exc

    def ensure_bucket(self, bucket_name: str) -> None:
        """
        Create bucket_name if it does not already exist.
        """
        if not self.client.bucket_exists(bucket_name):
            self.client.make_bucket(bucket_name)
            logger.info(f"Created bucket: {bucket_name}")

    def upload_file(
        self,
        bucket_name: str,
        object_name: str,
        local_file: Path | str,
        content_type: Optional[str] = None,
    ) -> None:
        """
        Upload a local file to MinIO.
        """
        local_path = Path(local_file)
        if not local_path.exists():
            raise FileNotFoundError(f"Local file not found: {local_path}")

        self.ensure_bucket(bucket_name)
        try:
            self.client.fput_object(bucket_name, object_name, str(local_path), content_type)
            logger.info(f"Uploaded {local_path} → {bucket_name}/{object_name}")
        except S3Error as exc:
            raise S3Error(exc.code, exc.message, exc.resource, exc.request_id, exc.host_id, exc.response) from exc

    def download_file(self, bucket_name: str, object_name: str, local_path: Path | str) -> None:
        """
        Download an object from MinIO to a local path.
        """
        if not self.client.bucket_exists(bucket_name):
            raise LookupError(f"Bucket not found: {bucket_name}")

        dest = Path(local_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            self.client.fget_object(bucket_name, object_name, str(dest))
            logger.info(f"Downloaded {bucket_name}/{object_name} → {dest}")
        except S3Error as exc:
            raise S3Error(exc.code, exc.message, exc.resource, exc.request_id, exc.host_id, exc.response) from exc


@lru_cache(maxsize=1)
def _get_manager() -> MinioFileManager:
    """
    Return a lazily created, cached singleton :class:`MinioFileManager`.

    :return: Shared ``MinioFileManager`` instance.
    :raises ValueError: If required environment variables are not set.
    """
    secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"
    return MinioFileManager(secure=secure)


def download_config(task_name: str, local_folder: Path | str) -> None:
    """
    Download the job configuration file from MinIO.

    :param task_name: Unique task/job identifier used as the remote path prefix.
    :param local_folder: Local directory where ``config.json`` will be saved.
    """
    remote_path = f"{task_name}/config.json"
    local_path = Path(local_folder) / "config.json"
    _get_manager().download_file(BUCKET, remote_path, local_path)


def upload_result(task_name: str, local_folder: Path | str) -> None:
    """
    Upload all files in *local_folder* to MinIO under *task_name/*.

    :param task_name: Unique task/job identifier used as the remote path prefix.
    :param local_folder: Local directory whose contents will be uploaded.
    """
    folder = Path(local_folder)
    for local_file in folder.iterdir():
        if local_file.is_file():
            object_name = f"{task_name}/{local_file.name}"
            _get_manager().upload_file(bucket_name=BUCKET, object_name=object_name, local_file=local_file)


__all__ = ['download_config', 'upload_result']
