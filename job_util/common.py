from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Literal

DEFAULT_BUCKET_NAME = "byteff2-jobs"

# Object key templates
CONFIG_KEY_TEMPLATE = "{job_id}/config.json"
RESULT_KEY_TEMPLATE = "{job_id}/{file_or_dir_name}"
STATUS_FILE_KEY_TEMPLATE = "{job_id}/status.json"


class JobStatus(Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


@dataclass
class Progress:
    task_name: str
    status: JobStatus
    message: Optional[str] = None
    timestamp: Optional[str] = None  # ISO format timestamp

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")


@dataclass
class MDProgress(Progress):
    """
    MDProgress extends Progress to include additional fields for MD simulation progress tracking.

    :param stage_name: Optional current simulation stage: "NPT", "NVT", or "NEMD"
    :param total_steps: Sum of total stages in all stages (NPT, NVT, NEMD)
    :param completed_steps: Sum of completed steps across all stages
    """
    stage_name: Optional[Literal["NPT", "NVT", "NEMD"]] = None
    total_steps: Optional[int] = None
    completed_steps: Optional[int] = None
