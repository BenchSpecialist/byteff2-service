"""
S3 File Manager handles all interactions with S3-compatible storage.
It provides methods for uploading/downloading job configurations and results.

Environment Variables Required:
- S3_ENDPOINT_URL: S3 endpoint URL
- S3_ACCESS_KEY:   S3 access key ID
- S3_SECRET_KEY:   S3 secret access key
- S3_BUCKET_NAME:  Target bucket name
"""
import os
import json
import logging
from enum import Enum
from pathlib import Path
from dataclasses import asdict
from functools import lru_cache
from typing import Optional, Dict, Any

import boto3
from botocore.exceptions import ClientError

from .common import (CONFIG_KEY_TEMPLATE, RESULT_KEY_TEMPLATE, Progress, MDProgress,
    STATUS_FILE_KEY_TEMPLATE, DEFAULT_BUCKET_NAME) # yapf: disable

logger = logging.getLogger(__name__)

S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")
BUCKET = os.environ.get("S3_BUCKET_NAME", DEFAULT_BUCKET_NAME)
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY")


class S3FileManager:
    """
    Service class for interacting with S3-compatible storage.

    Handles upload/download of job configurations and results to/from S3.
    """

    def __init__(self, access_key: Optional[str] = None, secret_key: Optional[str] = None):
        """
        Initialize S3FileManager with credentials and configuration.

        :param access_key: Optional access key. If None, uses S3_ACCESS_KEY env var
        :param secret_key: Optional secret key. If None, uses S3_SECRET_KEY env var
        :raises ValueError: If required environment variables are not set
        """
        resolved_access_key = access_key or S3_ACCESS_KEY
        resolved_secret_key = secret_key or S3_SECRET_KEY

        if resolved_access_key is None or resolved_secret_key is None:
            raise ValueError(
                "Environment variables 'S3_ACCESS_KEY' and 'S3_SECRET_KEY' must be set. Please configure them in your environment."
            )

        # Initialize S3 client
        self.s3_client = boto3.client('s3',
                                      endpoint_url=S3_ENDPOINT_URL,
                                      aws_access_key_id=resolved_access_key,
                                      aws_secret_access_key=resolved_secret_key)
        self.bucket_name = BUCKET
        logger.info(f"S3FileManager initialized with bucket: {self.bucket_name}")

    def upload(self,
               object_key: str,
               data: Optional[bytes] = None,
               file_or_dir_path: Optional[Path] = None,
               content_type: Optional[str] = None,
               bucket_name: Optional[str] = None) -> str | list[str]:
        """
        General-purpose upload method for uploading data or files/folders to S3.

        :param object_key: S3 object key (path within bucket)
        :param data: Raw bytes data to upload (use this OR file_path)
        :param file_or_dir_path: Path to local file or directory to upload (use this OR data)
        :param content_type: Optional content type for the object
        :param bucket_name: Optional bucket name (defaults to self.bucket_name)

        :return: Single S3 path (bucket/key format) if uploading file/data,
                List of S3 paths if uploading directory
        :raises ValueError: If neither data nor file_path is provided, or both are provided
        :raises FileNotFoundError: If file_path is provided but doesn't exist
        :raises ClientError: If upload fails
        """
        if (data is None and file_or_dir_path is None) or (data is not None and file_or_dir_path is not None):
            raise ValueError("Must provide exactly one of: data or file_path")

        bucket = bucket_name or self.bucket_name
        uploaded_paths = []

        try:
            if data is not None:
                # Case 1: Upload raw bytes data
                kwargs = {'Bucket': bucket, 'Key': object_key, 'Body': data}
                if content_type:
                    kwargs['ContentType'] = content_type
                self.s3_client.put_object(**kwargs)
                s3_path = f"{bucket}/{object_key}"
                logger.debug(f"Uploaded bytes to {s3_path}")
                return s3_path
            else:
                # Case 2: Handle file or directory upload
                local_path = Path(file_or_dir_path)
                if not local_path.exists():
                    raise FileNotFoundError(f"Local path '{file_or_dir_path}' does not exist")

                if local_path.is_file():
                    # Upload single file
                    extra_args = {}
                    if content_type:
                        extra_args['ContentType'] = content_type

                    self.s3_client.upload_file(str(local_path),
                                               bucket,
                                               object_key,
                                               ExtraArgs=extra_args if extra_args else None)

                    s3_path = f"{bucket}/{object_key}"
                    logger.info(f"Uploaded file to {s3_path}")
                    return s3_path
                else:
                    # Upload directory recursively
                    base_path = local_path
                    for item in local_path.rglob("*"):
                        if item.is_file():
                            # Calculate relative path for S3 key
                            rel_path = item.relative_to(base_path)
                            s3_key = f"{object_key.rstrip('/')}/{rel_path}"

                            self.s3_client.upload_file(str(item), bucket, s3_key)

                            s3_path = f"{bucket}/{s3_key}"
                            uploaded_paths.append(s3_path)
                            logger.debug(f"Uploaded {s3_path}")

                    logger.info(f"Uploaded directory with {len(uploaded_paths)} files")
                    return uploaded_paths

        except ClientError as e:
            logger.error(f"Failed to upload to {object_key}: {e}")
            raise

    def download(self,
                 object_key: str,
                 local_path: Optional[Path] = None,
                 return_bytes: bool = False,
                 bucket_name: Optional[str] = None) -> Optional[bytes | Path | list[Path]]:
        """
        General-purpose download method for downloading objects or folders from S3.

        :param object_key: S3 object key (path within bucket)
        :param bucket_name: Optional bucket name (defaults to self.bucket_name)
        :param local_path: Optional local file/directory path to save the object(s)
        :param return_bytes: If True, returns bytes instead of writing to file
                           (only for single file downloads)
        :return: - Bytes if return_bytes=True (single file only)
                 - Path if local_path provided and single file
                 - List[Path] if downloading multiple files (directory)
                 - None if no files found
        :raises ClientError: If download fails
        """
        bucket = bucket_name or self.bucket_name
        downloaded_paths = []

        try:
            # List objects with the given prefix to check if it's a directory
            response = self.s3_client.list_objects_v2(Bucket=bucket, Prefix=object_key)

            # Check if we're dealing with multiple objects (directory)
            is_directory = False
            matching_objects = []
            if 'Contents' in response:
                matching_objects = response['Contents']
                is_directory = len(matching_objects) > 1 or (len(matching_objects) == 1 and
                                                             matching_objects[0]['Key'].endswith('/'))

            if not matching_objects:
                logger.warning(f"No objects found at '{bucket}/{object_key}'")
                return None

            if is_directory and return_bytes:
                raise ValueError("Cannot return bytes for directory download")

            if is_directory:
                base_prefix = object_key.rstrip('/')
                if local_path is None:
                    local_path = Path.cwd()
                else:
                    local_path = Path(local_path)

                for obj in matching_objects:
                    # Skip if it's a directory marker
                    if obj['Key'].endswith('/'):
                        continue

                    # Calculate relative path
                    rel_path = obj['Key'][len(base_prefix):].lstrip('/')
                    target_path = local_path / rel_path

                    # Create parent directories
                    target_path.parent.mkdir(parents=True, exist_ok=True)

                    # Download file
                    self.s3_client.download_file(bucket, obj['Key'], str(target_path))
                    downloaded_paths.append(target_path)
                    logger.info(f"Downloaded '{obj['Key']}' to '{target_path}'")

                logger.info(f"Downloaded {len(downloaded_paths)} files from directory")
                return downloaded_paths
            else:
                # Handle single file download
                if return_bytes:
                    # Download and return bytes
                    response = self.s3_client.get_object(Bucket=bucket, Key=matching_objects[0]['Key'])
                    data = response['Body'].read()
                    logger.debug(f"Downloaded {bucket}/{object_key} as bytes")
                    return data
                else:
                    # Download to file
                    if local_path is None:
                        local_path = Path.cwd() / Path(object_key).name
                    else:
                        local_path = Path(local_path)

                    # Create parent directories if needed
                    local_path.parent.mkdir(parents=True, exist_ok=True)

                    self.s3_client.download_file(bucket, matching_objects[0]['Key'], str(local_path))
                    logger.info(f"Downloaded '{bucket}/{object_key}' to '{local_path}'")
                    return local_path

        except ClientError as e:
            logger.error(f"Failed to download '{bucket}/{object_key}': {e}")
            raise

    def list_objects(self, prefix: str = "") -> list[Dict[str, Any]]:
        """
        List objects in the bucket with optional prefix filter.

        :param prefix: Optional prefix to filter objects (e.g., 'configs/', 'results/job123/')
        :return: List of object metadata dictionaries
        :raises ClientError: If listing fails
        """
        try:
            response = self.s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=prefix)

            if 'Contents' not in response:
                logger.info(f"No objects found with prefix '{prefix}'")
                return []

            objects = [{
                'Key': obj['Key'],
                'Size': obj['Size'],
                'LastModified': obj['LastModified']
            } for obj in response['Contents']]

            logger.info(f"Found {len(objects)} objects with prefix '{prefix}'")
            return objects

        except ClientError as e:
            logger.error(f"Failed to list objects with prefix '{prefix}': {e}")
            raise

    def delete_object(self, object_key: str) -> bool:
        """
        Delete an object from the bucket.

        :param object_key: S3 object key to delete
        :return: True if deletion was successful
        :raises ClientError: If deletion fails
        """
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=object_key)

            logger.info(f"Deleted object {object_key} from bucket {self.bucket_name}")
            return True

        except ClientError as e:
            logger.error(f"Failed to delete object {object_key}: {e}")
            raise


########################################################
@lru_cache(maxsize=1)
def _get_manager() -> S3FileManager:
    """
    Lazily create and cache a singleton S3FileManager.

    :return: Cached S3FileManager instance
    """
    return S3FileManager()


def upload_config(task_name: str, config_data: Dict[str, Any]) -> str:
    """
    Upload job configuration JSON to S3 bucket.

    :param task_name: A unique identifier for the task
    :param config_data: Configuration data as dictionary (will be serialized to JSON)

    :return: S3 path to the uploaded config (bucket/key format)
    """
    try:
        object_key = CONFIG_KEY_TEMPLATE.format(job_id=task_name)
        json_str = json.dumps(config_data, indent=2)
        s3_path = _get_manager().upload(object_key=object_key,
                                        data=json_str.encode('utf-8'),
                                        content_type='application/json')
        return s3_path
    except ClientError as e:
        logger.error(f"Failed to upload config for task {task_name}: {e}")
        raise


def download_config(task_name: str, local_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Download job configuration JSON from S3 bucket.

    :param task_name: Task name / object key on S3
    :param local_path: (Optional) local file path to save the config. If None, only returns dict

    :return: Configuration data as dictionary
    """
    config_path = CONFIG_KEY_TEMPLATE.format(job_id=task_name)

    try:
        config_bytes = _get_manager().download(object_key=config_path, return_bytes=True)
    except ClientError as e:
        logger.error(f"Failed to download config from {config_path}: {e}")
        raise

    if config_bytes is None:
        logger.error(f"Config file not found at {config_path}")
        raise

    try:
        config_data = json.loads(config_bytes.decode('utf-8'))
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode config JSON from {config_path}: {e}")
        raise

    if local_path:
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with open(local_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2)
        logger.info(f"Save config to {local_path}")

    return config_data


def upload_result(task_name: str, file_or_dir_path: Path | str):
    """
    Upload result (a file or a folder) to S3 under the path *{task_name}/*.

    :param task_name: Task name / job ID
    :param file_or_dir_path: Local file or folder path to upload
    """
    object_key = RESULT_KEY_TEMPLATE.format(job_id=task_name, file_or_dir_name=Path(file_or_dir_path).name)
    _get_manager().upload(object_key=object_key, file_or_dir_path=Path(file_or_dir_path))


def update_progress(progress: Progress | MDProgress):
    """
    Save the latest progress as JSON to S3, overwriting any existing progress file.

    :param progress: Progress dataclass instance containing task_name, status, and message
    """
    object_key = STATUS_FILE_KEY_TEMPLATE.format(job_id=progress.task_name)
    try:
        # Convert progress to dict, keeping only non-None fields
        progress_dict = {k: v.value if isinstance(v, Enum) else v for k, v in asdict(progress).items() if v is not None}

        # Write latest progress as JSON to S3 (overwrites existing file)
        json_str = json.dumps(progress_dict, indent=2)
        _get_manager().upload(object_key=object_key, data=json_str.encode('utf-8'), content_type='application/json')
        logger.info(f"Saved progress for task {progress.task_name}: {progress.status.value} - {progress.message}")

    except ClientError as e:
        logger.error(f"Failed to save progress for task {progress.task_name}: {e}")
        raise


__all__ = ['download_config', 'upload_result', 'update_progress']
