"""
MD job runner for K8s pods.

Reads formulation data from the database, runs the MD simulation, and
updates progress/status in the database.
"""

import os
import sys
import shutil
import logging
import argparse
import threading
from pathlib import Path
from time import perf_counter

import polars

from byteff2.toolkit.protocol import TransportProtocol

from tools.formulation import COMMON_NAME_TO_SMILES, SALT_TO_IONS, build_config_from_weight_fractions
from tools.validate import validate_smiles

from job_scheduler.db.session import SessionLocal, init_db
from job_scheduler.db.models import Job, JobStatusEnum, Component

logger = logging.getLogger(__name__)

WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "/app/workspace")
PROGRESS_UPDATE_INTERVAL = int(os.environ.get("PROGRESS_UPDATE_INTERVAL", 60 * 10)  # 10 minutes
                              )
DEBUG_TOTAL_STEPS = os.environ.get("DEBUG_TOTAL_STEPS")


def update_job_progress(
    formulation_uid: str,
    status: JobStatusEnum,
    progress_pct: float = None,
    stage_name: str = None,
    message: str = None,
):
    """
    Write job progress to the database.
    """
    with SessionLocal() as session:
        job = (session.query(Job).filter(Job.formulation_uid == formulation_uid).first())
        if job:
            job.status = status
            if progress_pct is not None:
                job.progress_pct = progress_pct
            if stage_name is not None:
                job.stage_name = stage_name
            if message is not None:
                job.message = message
            session.commit()


######################
# Progress tracking  #
######################
def get_completed_steps(csv_file: str) -> int:
    """Read the last step number from a simulation CSV file."""
    value = (polars.scan_csv(csv_file).select(polars.first()).tail(1).collect(engine="streaming").item())
    if isinstance(value, str):
        return int(value.strip().strip("'").split()[0])
    return int(value)


def background_progress_updater(
    config: dict,
    stop_event: threading.Event,
    polling_interval: int,
):
    """Periodically check CSV files and update progress in the database.

    Follows the same stage-priority logic as ``run_md_job.py``
    (NEMD > NVT > NPT) but writes to the database instead of S3.
    """
    stages = [
        (
            "NEMD",
            "nonequ_steps",
            Path(config["output_dir"]) / "viscosity.csv",
            config["npt_steps"] + config["nvt_steps"],
        ),
        (
            "NVT",
            "nvt_steps",
            Path(config["output_dir"]) / "nvt_state.csv",
            config["npt_steps"],
        ),
        (
            "NPT",
            "npt_steps",
            Path(config["output_dir"]) / "npt_state.csv",
            0,
        ),
    ]

    total_steps = (config["npt_steps"] + config["nvt_steps"] + config["nonequ_steps"])
    formulation_uid = config["formulation_uid"]

    while not stop_event.is_set():
        try:
            for stage, config_key, csv_path, previous_steps in stages:
                if csv_path.exists():
                    completed = get_completed_steps(str(csv_path))
                    total_completed = completed + previous_steps
                    pct = (total_completed / total_steps) * 100.0

                    update_job_progress(
                        formulation_uid=formulation_uid,
                        status=JobStatusEnum.RUNNING,
                        progress_pct=round(pct, 2),
                        stage_name=stage,
                        message=(f"{stage}: {completed}/{config[config_key]} steps"),
                    )
                    break
        except Exception as e:
            logger.warning(f"Progress updater error: {e}")

        stop_event.wait(timeout=polling_interval)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="MD job runner (K8s pod)")
    parser.add_argument("--formulation_uid", type=str, required=True)
    args = parser.parse_args()

    formulation_uid = args.formulation_uid
    t_start = perf_counter()

    init_db()

    # Load formulation from database
    with SessionLocal() as session:
        components = (session.query(Component).filter(Component.formulation_uid == formulation_uid).all())
        if not components:
            logger.error(f"No components found for formulation {formulation_uid}")
            sys.exit(1)

        name_to_fractions = {c.name: c.weight_fraction for c in components}
        component_roles = {c.name: c.component_id for c in components}

    # Resolve SMILES and validate
    all_smiles = []
    for name in name_to_fractions:
        role = component_roles[name]
        if role == "Salt" and name in SALT_TO_IONS:
            cat, ani = SALT_TO_IONS[name]
            all_smiles.append(COMMON_NAME_TO_SMILES[cat])
            all_smiles.append(COMMON_NAME_TO_SMILES[ani])
        else:
            smi = COMMON_NAME_TO_SMILES.get(name)
            if smi is None:
                update_job_progress(
                    formulation_uid,
                    JobStatusEnum.FAILED,
                    message=f"Unknown component: {name}",
                )
                sys.exit(1)
            all_smiles.append(smi)

    errors = validate_smiles(all_smiles)
    if errors:
        update_job_progress(
            formulation_uid,
            JobStatusEnum.FAILED,
            message="\n".join(errors),
        )
        sys.exit(1)

    # Build simulation config
    try:
        config = build_config_from_weight_fractions(name_to_fractions, component_roles)
    except Exception as e:
        update_job_progress(
            formulation_uid,
            JobStatusEnum.FAILED,
            message=f"Config build failed: {e}",
        )
        sys.exit(1)

    config["task_name"] = formulation_uid
    config["formulation_uid"] = formulation_uid

    base_work_dir = Path(WORKSPACE_DIR) / formulation_uid
    config["params_dir"] = str(base_work_dir / "params_dir")
    config["output_dir"] = str(base_work_dir / "output_dir")
    config["working_dir"] = str(base_work_dir / "working_dir")

    # Set step counts
    if DEBUG_TOTAL_STEPS:
        steps = int(DEBUG_TOTAL_STEPS)
        config["npt_steps"] = steps
        config["nvt_steps"] = steps
        config["nonequ_steps"] = steps
        logger.warning(f"DEBUG: Set total steps in NPT, NVT and NEMD to {steps}")
    else:
        config["npt_steps"] = TransportProtocol.STAGE_TO_TOTAL_STEPS["NPT"]
        config["nvt_steps"] = TransportProtocol.STAGE_TO_TOTAL_STEPS["NVT"]
        config["nonequ_steps"] = TransportProtocol.STAGE_TO_TOTAL_STEPS["NEMD"]

    # Run the MD protocol with background progress tracking
    stop_event = threading.Event()
    progress_thread = threading.Thread(
        target=background_progress_updater,
        args=(config, stop_event, PROGRESS_UPDATE_INTERVAL),
        daemon=True,
    )
    progress_thread.start()

    try:
        md_protocol = TransportProtocol(config)
        md_protocol.run_protocol()
        md_protocol.post_process()

        update_job_progress(
            formulation_uid,
            JobStatusEnum.SUCCESS,
            progress_pct=100.0,
            message="MD simulation completed successfully",
        )
        logger.info(f"Job {formulation_uid} completed successfully")
    except Exception as e:
        logger.exception(f"MD simulation failed: {e}")
        update_job_progress(
            formulation_uid,
            JobStatusEnum.FAILED,
            message=f"MD simulation failed: {str(e)}",
        )
        sys.exit(1)
    finally:
        stop_event.set()
        progress_thread.join(timeout=10.0)
        if base_work_dir.exists():
            shutil.rmtree(base_work_dir, ignore_errors=True)
        try:
            from byteff2.utils.utilities import get_human_readable_duration_str
            elapsed = perf_counter() - t_start
            logger.info(f"Total time: {get_human_readable_duration_str(elapsed)}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
