import os
from dataclasses import dataclass, field


@dataclass
class SchedulerConfig:
    """Scheduler configuration loaded from environment variables."""

    database_url: str = field(
        default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///jobcli.db")
    )
    k8s_namespace: str = field(
        default_factory=lambda: os.environ.get("K8S_NAMESPACE", "default")
    )
    docker_image: str = field(
        default_factory=lambda: os.environ.get(
            "DOCKER_IMAGE", "byteff2-service:latest"
        )
    )
    num_nodes: int = field(
        default_factory=lambda: int(os.environ.get("CLUSTER_NUM_NODES", "16"))
    )
    gpus_per_node: int = field(
        default_factory=lambda: int(os.environ.get("CLUSTER_GPUS_PER_NODE", "8"))
    )
    poll_interval_seconds: float = field(
        default_factory=lambda: float(
            os.environ.get("SCHEDULER_POLL_INTERVAL", "5")
        )
    )
    pid_file: str = field(
        default_factory=lambda: os.environ.get(
            "SCHEDULER_PID_FILE", os.path.expanduser("~/.jobcli/scheduler.pid")
        )
    )
    log_file: str = field(
        default_factory=lambda: os.environ.get(
            "SCHEDULER_LOG_FILE", os.path.expanduser("~/.jobcli/scheduler.log")
        )
    )
