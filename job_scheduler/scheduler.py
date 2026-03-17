"""
Long-running job scheduler that keeps the GPU cluster fully utilised.
"""

import os
import signal
import logging
import time
from pathlib import Path

from job_scheduler.db.session import SessionLocal, init_db
from job_scheduler.db.queries import claim_pending_jobs, reset_orphaned_running_jobs
from job_scheduler.db.models import Job, JobStatusEnum
from job_scheduler.k8s_client import K8sJobManager
from job_scheduler.config import SchedulerConfig

logger = logging.getLogger(__name__)


####################
# PID file helpers #
####################
def write_pid_file(path: str, pid: int | None = None) -> None:
    """Write *pid* (default: current PID) to *path*, creating parent dirs."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(str(pid if pid is not None else os.getpid()))


def remove_pid_file(path: str) -> None:
    """Remove the PID file if it exists."""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def read_pid_file(path: str) -> int | None:
    """Return the PID stored in *path*, or ``None``."""
    try:
        return int(Path(path).read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def is_scheduler_running(path: str) -> tuple[bool, int | None]:
    """Check whether the scheduler process recorded in *path* is alive.

    :return: ``(alive, pid)``
    """
    pid = read_pid_file(path)
    if pid is None:
        return False, None
    try:
        os.kill(pid, 0)  # signal 0 = existence check
        return True, pid
    except ProcessLookupError:
        remove_pid_file(path)  # stale file
        return False, pid
    except OSError:
        return False, pid


class Scheduler:
    """Poll-based scheduler that fills GPU slots with PENDING jobs."""

    def __init__(self, config: SchedulerConfig):
        self.config = config
        self.total_gpus = config.num_nodes * config.gpus_per_node
        self.k8s: K8sJobManager | None = None
        self._shutdown = False

    # Public API
    def run(self):
        """Main scheduler loop. Runs until SIGTERM/SIGINT or ``jobcli stop``."""
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        # Guard against double-start (allow if PID file already has our PID,
        # which happens when the parent wrote it before forking).
        alive, old_pid = is_scheduler_running(self.config.pid_file)
        if alive and old_pid != os.getpid():
            logger.error(
                f"Scheduler already running (pid {old_pid}). "
                f"Use 'jobcli stop' first."
            )
            return

        write_pid_file(self.config.pid_file)

        init_db()

        # Connect to K8s after PID file is written so stop/server-status work
        # even if the cluster is temporarily unreachable.
        try:
            self.k8s = K8sJobManager(
                namespace=self.config.k8s_namespace,
                image=self.config.docker_image,
            )
        except Exception:
            logger.exception("Failed to connect to Kubernetes cluster")
            remove_pid_file(self.config.pid_file)
            return

        self._recover_orphans()

        logger.info(
            f"Scheduler started (pid {os.getpid()}). "
            f"Total GPU capacity: {self.total_gpus}"
        )

        try:
            while not self._shutdown:
                try:
                    self._tick()
                except Exception:
                    logger.exception("Error in scheduler tick")
                time.sleep(self.config.poll_interval_seconds)
        finally:
            remove_pid_file(self.config.pid_file)

        logger.info("Scheduler shutting down gracefully.")

    # Internal
    def _tick(self):
        """One scheduling cycle: sync pods, then back-fill available slots."""
        # 1. Poll active pods and reconcile with DB
        active_pods = self.k8s.list_active_job_pods()
        running_count = self._sync_pod_statuses(active_pods)

        # 2. Calculate available GPU slots
        available_slots = self.total_gpus - running_count
        if available_slots <= 0:
            return

        # 3. Claim PENDING jobs and launch pods
        with SessionLocal() as session:
            jobs = claim_pending_jobs(session, limit=available_slots)
            for job in jobs:
                try:
                    pod_name = self.k8s.create_job_pod(
                        formulation_uid=job.formulation_uid,
                        database_url=self.config.database_url,
                    )
                    job.k8s_pod_name = pod_name
                    session.commit()
                    logger.info(
                        f"Launched pod {pod_name} for {job.formulation_uid}"
                    )
                except Exception:
                    logger.exception(
                        f"Failed to launch pod for {job.formulation_uid}"
                    )
                    job.status = JobStatusEnum.PENDING
                    job.k8s_pod_name = None
                    session.commit()

    def _sync_pod_statuses(self, active_pods: dict[str, str]) -> int:
        """Reconcile K8s pod phases with database job statuses.

        :return: Number of currently-running pods.
        """
        running_count = 0
        with SessionLocal() as session:
            running_jobs = (
                session.query(Job)
                .filter(Job.status == JobStatusEnum.RUNNING)
                .all()
            )
            for job in running_jobs:
                if not job.k8s_pod_name:
                    continue

                phase = active_pods.get(job.k8s_pod_name)

                if phase in ("Running", "Pending"):
                    running_count += 1
                elif phase == "Succeeded":
                    self.k8s.delete_pod(job.k8s_pod_name)
                    if job.status == JobStatusEnum.RUNNING:
                        job.status = JobStatusEnum.SUCCESS
                        job.progress_pct = 100.0
                        job.message = "Pod completed successfully"
                elif phase == "Failed" or phase is None:
                    job.status = JobStatusEnum.FAILED
                    job.message = (
                        f"Pod {job.k8s_pod_name} failed or disappeared"
                    )
                    if job.k8s_pod_name and phase == "Failed":
                        self.k8s.delete_pod(job.k8s_pod_name)

            session.commit()
        return running_count

    def _recover_orphans(self):
        """On startup, reset RUNNING jobs whose pods no longer exist."""
        active_pods = self.k8s.list_active_job_pods()
        active_names = set(active_pods.keys())
        with SessionLocal() as session:
            count = reset_orphaned_running_jobs(session, active_names)
            if count:
                logger.warning(
                    f"Reset {count} orphaned RUNNING jobs to PENDING"
                )

    def _handle_signal(self, signum, _frame):
        logger.info(f"Received signal {signum}, initiating shutdown...")
        self._shutdown = True
