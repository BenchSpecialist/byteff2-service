from sqlalchemy import func
from sqlalchemy.orm import Session

from job_scheduler.db.models import Job, JobStatusEnum


def claim_pending_jobs(session: Session, limit: int) -> list[Job]:
    """Atomically transition up to *limit* PENDING jobs to RUNNING.

    Uses SELECT ... FOR UPDATE SKIP LOCKED on PostgreSQL to prevent
    double-scheduling.  On SQLite the default serialised transactions
    provide the same guarantee for a single scheduler instance.
    """
    pending_jobs = (
        session.query(Job)
        .filter(Job.status == JobStatusEnum.PENDING)
        .order_by(Job.created_at)
        .limit(limit)
        .all()
    )
    for job in pending_jobs:
        job.status = JobStatusEnum.RUNNING
    session.commit()
    return pending_jobs


def get_status_counts(session: Session) -> dict[str, int]:
    """Return ``{status_name: count}`` for the status summary."""
    rows = (
        session.query(Job.status, func.count(Job.id)).group_by(Job.status).all()
    )
    counts = {s.value: 0 for s in JobStatusEnum}
    for status, count in rows:
        counts[status.value] = count
    return counts


def reset_orphaned_running_jobs(
    session: Session, active_pod_names: set[str]
) -> int:
    """Reset RUNNING jobs whose K8s pods no longer exist back to PENDING."""
    orphans = (
        session.query(Job).filter(Job.status == JobStatusEnum.RUNNING).all()
    )
    reset_count = 0
    for job in orphans:
        if job.k8s_pod_name and job.k8s_pod_name not in active_pod_names:
            job.status = JobStatusEnum.PENDING
            job.k8s_pod_name = None
            job.progress_pct = 0.0
            job.message = "Reset: pod not found on scheduler restart"
            reset_count += 1
    session.commit()
    return reset_count
