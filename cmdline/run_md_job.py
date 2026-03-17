import os
import sys
import json
import shutil
import signal
import polars
import argparse
import threading
from pathlib import Path
from time import perf_counter

from byteff2.toolkit.protocol import TransportProtocol
from byteff2.utils.utilities import get_human_readable_duration_str

from tools.formulation import build_simulation_box_config
from job_util import JobStatus, Progress, MDProgress, get_backend
from job_util.validate import validate_smiles

# Read JOB_STORAGE_TYPE from env; raises ValueError early on bad value
_backend = get_backend()
download_config = _backend.download_config
upload_result = _backend.upload_result
update_progress = _backend.update_progress

# Global variable to track cleanup directories
cleanup_dirs = []

WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "/app/workspace")

## Environment variables
# How often to update progress in seconds
PROGRESS_UPDATE_INTERVAL = int(os.environ.get("PROGRESS_UPDATE_INTERVAL", 3600))
# Used to set a small number of total steps in MD (unphysical and will usually
# cause error in post-analysis) for quick testing
DEBUG_TOTAL_STEPS = os.environ.get("DEBUG_TOTAL_STEPS")


def cleanup_on_exit():
    """Clean up directories on exit"""
    for dir_path in cleanup_dirs:
        if Path(dir_path).exists():
            try:
                shutil.rmtree(dir_path)
                print(f"Cleaned up: {dir_path}")
            except Exception as e:
                print(f"Warning: Failed to clean up {dir_path}: {e}", file=sys.stderr)


def signal_handler(signum, _):
    """Handle signals (SIGTERM, SIGINT)"""
    print(f"Received signal {signum}, cleaning up...", file=sys.stderr)
    cleanup_on_exit()
    sys.exit(1)


# Register signal handlers
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def main():
    t_start = perf_counter()
    parser = argparse.ArgumentParser()
    parser.add_argument("--task_name", type=str, required=True)
    args = parser.parse_args()

    task_name = args.task_name
    config_mu = download_config(task_name=task_name)
    if "protocol" not in config_mu:
        config = build_simulation_box_config(config_mu)

    # Input validation
    inp_smiles = config['smiles'].values()
    errors = validate_smiles(inp_smiles)
    if errors:
        err_msg = "\n".join(errors)
        print(err_msg, file=sys.stderr)
        update_progress(Progress(task_name=task_name, status=JobStatus.FAILED, message=err_msg))
        cleanup_on_exit()
        sys.exit(1)

    config["task_name"] = task_name

    base_work_dir = Path(WORKSPACE_DIR) / task_name
    config["params_dir"] = str(base_work_dir / "params_dir")
    config["output_dir"] = str(base_work_dir / "output_dir")
    config["working_dir"] = str(base_work_dir / "working_dir")

    if DEBUG_TOTAL_STEPS:
        _total_steps = int(DEBUG_TOTAL_STEPS)
        config["npt_steps"], config["nvt_steps"], config["nonequ_steps"] = \
            _total_steps, _total_steps, _total_steps
        print(f'WARNING: Set total steps in NPT, NVT and NEMD to {_total_steps} for quick testing. '
              'Note that using a small value for total steps will likely cause errors in post-analysis.')
    else:
        config["npt_steps"] = TransportProtocol.STAGE_TO_TOTAL_STEPS["NPT"]
        config["nvt_steps"] = TransportProtocol.STAGE_TO_TOTAL_STEPS["NVT"]
        config["nonequ_steps"] = TransportProtocol.STAGE_TO_TOTAL_STEPS["NEMD"]
        print(f'Using default total steps for TransportProtocol:\n'
              f'- NPT: {config["npt_steps"]:_} steps\n'
              f'- NVT: {config["nvt_steps"]:_} steps\n'
              f'- NEMD: {config["nonequ_steps"]:_} steps')

    try:
        run_transport_protocol(config)
    except Exception as e:
        print(f"Error occurred: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        update_progress(
            Progress(task_name=task_name, status=JobStatus.FAILED, message=f'MD simulation failed: {str(e)}'))
        cleanup_on_exit()
        sys.exit(1)
    finally:
        try:
            print(f"Total time taken: {get_human_readable_duration_str(perf_counter() - t_start)}")
        except Exception:
            pass


def run_transport_protocol(config: dict):
    task_name = config["task_name"]
    # Track directories for cleanup
    cleanup_dirs.extend([
        config["params_dir"],
        config["output_dir"],
        config["working_dir"],
    ])

    print(f"Protocol config:\n{json.dumps(config, indent=4)}")

    # Create stop event for background progress updater
    stop_event = threading.Event()

    # Start background progress updater as a daemon thread (runs every 1 hour)
    progress_thread = threading.Thread(target=background_progress_updater,
                                       args=(config, stop_event, PROGRESS_UPDATE_INTERVAL),
                                       daemon=True)
    progress_thread.start()

    try:
        md_protocol = TransportProtocol(config)
        md_protocol.run_protocol()
        md_protocol.post_process()

        result_json = Path(config["output_dir"]) / "results_mu.json"

        if result_json.is_file():
            upload_result(task_name=task_name, file_or_dir_path=str(result_json))
            msg = 'Completed and results_mu.json uploaded to S3'
            print(msg)
            update_progress(Progress(task_name=task_name, status=JobStatus.SUCCESS, message=msg))
        else:
            msg = 'No results_mu.json found'
            print(msg)
            update_progress(Progress(task_name=task_name, status=JobStatus.FAILED, message=msg))
            cleanup_on_exit()
            sys.exit(1)
    finally:
        # Signal the background thread to stop
        stop_event.set()
        # Wait briefly for the thread to finish its last update
        progress_thread.join(timeout=10.0)


def get_completed_steps(csv_file: str) -> int:
    value = (polars.scan_csv(csv_file).select(polars.first()).tail(1).collect(engine="streaming").item())
    if isinstance(value, str):
        val = int(value.strip().strip("'").split()[0])
    else:
        val = int(value)
    return val


def background_progress_updater(config: dict,
                                stop_event: threading.Event,
                                polling_interval: int = PROGRESS_UPDATE_INTERVAL):
    """
    Background task that periodically updates progress by checking CSV files.

    :param config: Configuration dictionary containing task_name and output_dir
    :param stop_event: threading.Event to signal when to stop polling
    :param polling_interval: Interval in seconds between progress checks
    """
    # Priority (NEMD > NVT > NPT) is reversed order in time sequence
    stages = [
        # stage_name, config_key, csv_path, previous_steps
        ("NEMD","nonequ_steps", Path(config["output_dir"])/"viscosity.csv", config["npt_steps"]+config["nvt_steps"]),
        ("NVT",    "nvt_steps", Path(config["output_dir"])/"nvt_state.csv", config["npt_steps"]),
        ("NPT",    "npt_steps", Path(config["output_dir"])/"npt_state.csv", 0),
    ]  # yapf: disable

    # Total steps same for all stages
    total_steps = sum([config[config_key] for config_key in ["npt_steps", "nvt_steps", "nonequ_steps"]])
    while not stop_event.is_set():
        try:
            for stage, config_key, csv_path, previous_steps in stages:
                if csv_path.exists():
                    current_stage = stage
                    completed_steps_in_stage = get_completed_steps(csv_path)
                    total_steps_in_stage = config[config_key]
                    update_progress(
                        MDProgress(
                            task_name=config["task_name"],
                            status=JobStatus.RUNNING,
                            message=
                            f"{current_stage} stage: {completed_steps_in_stage} / {total_steps_in_stage} steps completed",
                            stage_name=current_stage,
                            total_steps=total_steps,
                            completed_steps=completed_steps_in_stage + previous_steps))
                    break
        except Exception as e:
            # Log errors but don't propagate them to avoid impacting main protocol execution
            print(f"Warning: Error in background progress updater: {e}", file=sys.stderr)

        # Wait for polling_interval seconds or until stop_event is set
        stop_event.wait(timeout=polling_interval)


if __name__ == '__main__':
    main()
