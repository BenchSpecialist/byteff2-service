"""
Long-running job scheduler that keeps the GPU cluster fully utilised.

Launched by ``jobcli start``.
"""

import signal
import logging
import time

from job_scheduler.db.session import SessionLocal, init_db
from job_scheduler.db.queries import claim_pending_jobs, reset_orphaned_running_jobs
from job_scheduler.db.models import Job, JobStatusEnum
from job_scheduler.k8s_client import K8sJobManager
from job_scheduler.config import SchedulerConfig

logger = logging.getLogger(__name__)


class Scheduler:
    """Poll-based scheduler that fills GPU slots with PENDING jobs."""

    def __init__(self, config: SchedulerConfig):
        self.config = config
        self.total_gpus = config.num_nodes * config.gpus_per_node
        self.k8s = K8sJobManager(
            namespace=config.k8s_namespace,
            image=config.docker_image,
        )
        self._shutdown = False

    # Public API
    def run(self):
        """Main scheduler loop. Runs until SIGTERM/SIGINT."""
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        init_db()
        self._recover_orphans()

        logger.info(
            f"Scheduler started. Total GPU capacity: {self.total_gpus}"
        )

        while not self._shutdown:
            try:
                self._tick()
            except Exception:
                logger.exception("Error in scheduler tick")
            time.sleep(self.config.poll_interval_seconds)

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
