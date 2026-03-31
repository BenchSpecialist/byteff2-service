"""
MinIO File Manager for uploading/downloading job configurations and results on
the local server.

Environment Variables Required:
- MINIO_ENDPOINT:   MinIO server endpoint (e.g. ``minio.example.com:9000``)
- MINIO_ACCESS_KEY: MinIO access key ID
- MINIO_SECRET_KEY: MinIO secret access key
- MINIO_BUCKET:     Target bucket name
"""
import os
import json
import logging
import urllib.request
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import asdict
from functools import lru_cache

from minio import Minio
from minio.error import S3Error

from .common import (CONFIG_KEY_TEMPLATE, RESULT_KEY_TEMPLATE, Progress, MDProgress,
    STATUS_FILE_KEY_TEMPLATE, DEFAULT_BUCKET_NAME) # yapf: disable

logger = logging.getLogger(__name__)

MINIO_ENDPOINT: Optional[str] = os.environ.get("MINIO_ENDPOINT")
MINIO_ACCESS_KEY: Optional[str] = os.environ.get("MINIO_ACCESS_KEY")
MINIO_SECRET_KEY: Optional[str] = os.environ.get("MINIO_SECRET_KEY")
BUCKET: str = os.environ.get("MINIO_BUCKET", DEFAULT_BUCKET_NAME)


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

    def check_health(self):
        scheme = "https" if self.client._base_url.is_https else "http"
        url = f"{scheme}://{MINIO_ENDPOINT}/minio/health/live"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    print("MinIO health check passed")
                    return
        except Exception:
            pass
        raise ConnectionError(f"MinIO not reachable at {url}")

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

    def upload(
        self,
        bucket_name: str,
        object_name: str,
        file_or_dir_path: Path | str,
        content_type: Optional[str] = None,
    ) -> list[str]:
        """
        Upload a local file or directory to MinIO.

        For a single file, uploads it directly under *object_name*.
        For a directory, recursively uploads every file under *object_name/* preserving
        the relative sub-path.

        :param bucket_name: Target bucket name.
        :param object_name: Remote object key (prefix for directories).
        :param file_or_dir_path: Local file or directory to upload.
        :param content_type: Optional MIME type (applied to single-file uploads only).
        :return: List of uploaded remote object keys.
        :raises FileNotFoundError: If *file_or_dir_path* does not exist.
        :raises S3Error: If any upload fails.
        """
        local_path = Path(file_or_dir_path)
        if not local_path.exists():
            raise FileNotFoundError(f"Local path not found: {local_path}")

        self.ensure_bucket(bucket_name)
        uploaded: list[str] = []

        if local_path.is_file():
            try:
                self.client.fput_object(bucket_name, object_name, str(local_path), content_type)
                logger.info(f"Uploaded {local_path} → {bucket_name}/{object_name}")
                uploaded.append(object_name)
            except S3Error as exc:
                raise S3Error(exc.code, exc.message, exc.resource, exc.request_id, exc.host_id, exc.response) from exc
        else:
            for item in local_path.rglob("*"):
                if item.is_file():
                    rel_key = f"{object_name.rstrip('/')}/{item.relative_to(local_path)}"
                    try:
                        self.client.fput_object(bucket_name, rel_key, str(item))
                        logger.debug(f"Uploaded {item} → {bucket_name}/{rel_key}")
                        uploaded.append(rel_key)
                    except S3Error as exc:
                        raise S3Error(exc.code, exc.message, exc.resource, exc.request_id, exc.host_id,
                                      exc.response) from exc
            logger.info(f"Uploaded directory {local_path} ({len(uploaded)} files) → {bucket_name}/{object_name}/")

        return uploaded

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

    def download_bytes(self, bucket_name: str, object_name: str) -> bytes:
        """
        Download an object from MinIO and return its content as bytes.

        :param bucket_name: Target bucket name.
        :param object_name: Remote object key.
        :return: Object content as raw bytes.
        :raises LookupError: If the bucket does not exist.
        :raises S3Error: If the download fails.
        """
        if not self.client.bucket_exists(bucket_name):
            raise LookupError(f"Bucket not found: {bucket_name}")

        response = None
        try:
            response = self.client.get_object(bucket_name, object_name)
            data = response.read()
            logger.info(f"Downloaded {bucket_name}/{object_name} into memory ({len(data)} bytes)")
            return data
        except S3Error as exc:
            raise S3Error(exc.code, exc.message, exc.resource, exc.request_id, exc.host_id, exc.response) from exc
        finally:
            if response is not None:
                response.close()
                response.release_conn()


@lru_cache(maxsize=1)
def _get_manager() -> MinioFileManager:
    """
    Return a lazily created, cached singleton :class:`MinioFileManager`.

    :return: Shared ``MinioFileManager`` instance.
    :raises ValueError: If required environment variables are not set.
    """
    secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"
    manager = MinioFileManager(secure=secure)
    manager.check_health()
    manager.ensure_bucket(BUCKET)
    return manager


def download_config(task_name: str, local_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Download job configuration JSON from MinIO.

    :param task_name: Unique task/job identifier used as the remote path prefix.
    :param local_path: Optional local file path to save the config. If ``None``, only returns dict.
    :return: Configuration data as a dictionary.
    :raises S3Error: If the download fails.
    """
    config_key = CONFIG_KEY_TEMPLATE.format(job_id=task_name)

    config_bytes = _get_manager().download_bytes(BUCKET, config_key)
    config_data: Dict[str, Any] = json.loads(config_bytes.decode("utf-8"))

    if local_path is not None:
        resolved = Path(local_path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with resolved.open("w") as f:
            json.dump(config_data, f, indent=2)
        logger.info(f"Saved config to {resolved}")

    return config_data


def upload_result(task_name: str, file_or_dir_path: Path | str) -> None:
    """
    Upload a result file or directory to MinIO under *{task_name}/*.

    :param task_name: Unique task/job identifier used as the remote path prefix.
    :param file_or_dir_path: Local file or directory path to upload.
    """
    object_name = RESULT_KEY_TEMPLATE.format(job_id=task_name, file_or_dir_name=Path(file_or_dir_path).name)
    _get_manager().upload(bucket_name=BUCKET, object_name=object_name, file_or_dir_path=Path(file_or_dir_path))


def update_progress(progress: Progress | MDProgress) -> None:
    """
    Save the latest job progress snapshot as JSON to MinIO, overwriting any previous value.

    :param progress: Progress dataclass instance containing task_name, status, and message
    :raises S3Error: If writing the status file fails.
    """
    object_name = STATUS_FILE_KEY_TEMPLATE.format(job_id=progress.task_name)
    progress_dict = {
        key: value.value if isinstance(value, Enum) else value
        for key, value in asdict(progress).items()
        if value is not None
    }
    payload = json.dumps(progress_dict, indent=2).encode("utf-8")
    data_stream = BytesIO(payload)
    manager = _get_manager()
    manager.ensure_bucket(BUCKET)
    try:
        manager.client.put_object(
            BUCKET,
            object_name,
            data_stream,
            length=len(payload),
            content_type="application/json",
        )
        logger.info(
            f"Saved progress for task {progress.task_name}: {progress_dict.get('status')} - {progress_dict.get('message')}"
        )
    except S3Error as exc:
        logger.error(f"Failed to save progress for task {progress.task_name}: {exc}")
        raise


__all__ = ['download_config', 'upload_result', 'update_progress']
