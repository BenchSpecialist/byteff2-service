"""
Commandline interface for the Distributed MD Job Scheduler

Commands
--------
    jobcli add <file>       Import formulations from CSV/XLSX
    jobcli start            Start the automated job scheduler
    jobcli stop             Stop the running scheduler
    jobcli server-status    Check whether the scheduler is running
    jobcli status           Print a summary of all jobs
"""

import os
import sys
import signal
import logging
import argparse

from job_scheduler.db.session import SessionLocal, init_db
from job_scheduler.db.models import Formulation, Component, Job, JobStatusEnum
from job_scheduler.formulation_io import (
    parse_formulations_file,
    validate_formulation,
    validate_component_names,
    get_uniq_id_from_formulation,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("jobcli")



# jobcli add <file>
def cmd_add(args):
    """Import formulations from a CSV or XLSX file."""
    filepath = args.file
    init_db()

    # 1. Parse the input file
    try:
        formulations = parse_formulations_file(filepath)
    except ValueError as e:
        logger.error(f"Failed to parse {filepath}: {e}")
        sys.exit(1)

    added = 0
    skipped = 0
    errors = 0

    with SessionLocal() as session:
        for fid, components in formulations.items():
            # 2. Validate weight fractions sum to 100
            err = validate_formulation(components)
            if err:
                logger.error(f"Formulation {fid}: {err}")
                errors += 1
                continue

            # 3. Validate component names are resolvable
            name_errors = validate_component_names(components)
            if name_errors:
                for ne in name_errors:
                    logger.error(f"Formulation {fid}: {ne}")
                errors += 1
                continue

            # 4. Compute deterministic UID
            name_to_fracs = {c["Name"]: c["Weight_fraction"] for c in components}
            uid = get_uniq_id_from_formulation(name_to_fracs)

            # 5. Check for duplicate
            existing = (
                session.query(Formulation)
                .filter(Formulation.uid == uid)
                .first()
            )
            if existing:
                logger.warning(
                    f"Formulation {fid}: skipped (duplicate of "
                    f"existing {existing.formulation_id}, uid={uid})"
                )
                skipped += 1
                continue

            # 6. Insert formulation, components, and PENDING job
            formulation = Formulation(uid=uid, formulation_id=fid)
            session.add(formulation)

            for comp in components:
                session.add(
                    Component(
                        formulation_uid=uid,
                        component_id=comp["Component_ID"],
                        name=comp["Name"],
                        weight_fraction=comp["Weight_fraction"],
                    )
                )

            session.add(Job(formulation_uid=uid, status=JobStatusEnum.PENDING))
            added += 1

        session.commit()

    summary = f"Added {added} new formulations ({skipped} skipped as duplicates"
    if errors:
        summary += f", {errors} with errors"
    summary += ")."
    print(summary)


# jobcli start
def cmd_start(args):
    """Start the automated job scheduler as a background daemon."""
    from pathlib import Path
    from job_scheduler.scheduler import Scheduler, is_scheduler_running
    from job_scheduler.config import SchedulerConfig

    config = SchedulerConfig()

    # Quick pre-check so the user gets immediate feedback
    alive, old_pid = is_scheduler_running(config.pid_file)
    if alive:
        print(f"Scheduler is already running (pid {old_pid}).")
        sys.exit(1)

    # Fork to background
    child_pid = os.fork()
    if child_pid > 0:
        # Parent — write PID file immediately to block concurrent starts,
        # then report and return.
        from job_scheduler.scheduler import write_pid_file
        write_pid_file(config.pid_file, pid=child_pid)
        print(f"Scheduler started (pid {child_pid}).")
        print(f"Log: {config.log_file}")
        return

    # ---- Child (daemon) process ----
    os.setsid()  # detach from controlling terminal

    # Redirect stdin/stdout/stderr to log file
    log_path = Path(config.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    devnull_fd = os.open(os.devnull, os.O_RDONLY)
    log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(devnull_fd, sys.stdin.fileno())
    os.dup2(log_fd, sys.stdout.fileno())
    os.dup2(log_fd, sys.stderr.fileno())
    os.close(devnull_fd)
    os.close(log_fd)

    # Re-initialise logging for the daemon process
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    scheduler = Scheduler(config)
    scheduler.run()
    os._exit(0)


# jobcli stop
def cmd_stop(args):
    """Send SIGTERM to the running scheduler process."""
    from job_scheduler.config import SchedulerConfig
    from job_scheduler.scheduler import is_scheduler_running, read_pid_file

    pid_file = SchedulerConfig().pid_file
    alive, pid = is_scheduler_running(pid_file)
    if not alive:
        print("Scheduler is not running.")
        return

    print(f"Stopping scheduler (pid {pid})...")
    os.kill(pid, signal.SIGTERM)

    # Wait briefly for the process to exit
    import time
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            print("Scheduler stopped.")
            return
        time.sleep(0.5)

    print(f"Warning: scheduler (pid {pid}) did not exit within 15 seconds.")


# jobcli server-status
def cmd_server_status(args):
    """Check whether the scheduler process is running."""
    from job_scheduler.config import SchedulerConfig
    from job_scheduler.scheduler import is_scheduler_running

    pid_file = SchedulerConfig().pid_file
    alive, pid = is_scheduler_running(pid_file)
    if alive:
        print(f"Scheduler is running (pid {pid}).")
    else:
        print("Scheduler is not running.")


# jobcli status
def cmd_status(args):
    """Print a summary of all jobs."""
    init_db()

    from job_scheduler.db.queries import get_status_counts

    with SessionLocal() as session:
        counts = get_status_counts(session)

    total = sum(counts.values())
    running = counts.get("RUNNING", 0)
    pending = counts.get("PENDING", 0)
    success = counts.get("SUCCESS", 0)
    failed = counts.get("FAILED", 0)

    header = (
        f"{'TOTAL_JOBS':>12} | {'RUNNING_JOBS':>12} | {'PENDING_JOBS':>12} "
        f"| {'COMPLETED_JOBS':>14} | {'FAILED_JOBS':>11}"
    )
    values = (
        f"{total:>12,} | {running:>12,} | {pending:>12,} "
        f"| {success:>14,} | {failed:>11,}"
    )
    print(header)
    print(values)


def main():
    parser = argparse.ArgumentParser(
        prog="jobcli",
        description="Distributed MD Job Scheduling Platform",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # jobcli add <file>
    add_parser = subparsers.add_parser(
        "add", help="Import formulations from CSV/XLSX"
    )
    add_parser.add_argument("file", type=str, help="Path to .csv or .xlsx file")
    add_parser.set_defaults(func=cmd_add)

    # jobcli start
    start_parser = subparsers.add_parser(
        "start", help="Start the job scheduler"
    )
    start_parser.set_defaults(func=cmd_start)

    # jobcli stop
    stop_parser = subparsers.add_parser(
        "stop", help="Stop the running scheduler"
    )
    stop_parser.set_defaults(func=cmd_stop)

    # jobcli server-status
    server_status_parser = subparsers.add_parser(
        "server-status", help="Check if the scheduler is running"
    )
    server_status_parser.set_defaults(func=cmd_server_status)

    # jobcli status
    status_parser = subparsers.add_parser(
        "status", help="Show job status summary"
    )
    status_parser.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
