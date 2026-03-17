# jobcli usage guide

## Prerequisites

```bash
pip install -r job_scheduler/requirements.txt
```

Set `DATABASE_URL` (defaults to `sqlite:///jobcli.db` if unset):

```bash
# Local dev
export DATABASE_URL="sqlite:///jobcli.db"

# AWS RDS PostgreSQL
export DATABASE_URL="postgresql://user:pass@host:5432/jobcli"
```

Tables are created automatically on first run.

---

## Commands

### `jobcli add <file>`

Import formulations from a `.csv` or `.xlsx` file.

```bash
python -m cmdline.jobcli add formulations.csv
python -m cmdline.jobcli add formulations.xlsx
```

**Input file format:**

| Formulation_ID | Component_ID | Name  | Weight_fraction |
|----------------|-------------|-------|-----------------|
| F1837          | Solvent     | EC    | 10              |
| F1837          | Solvent     | EMC   | 70              |
| F1837          | Salt        | LiPF6 | 12              |
| F1837          | Additive    | FEC   | 8               |

- `Weight_fraction` values **must sum to 100** per formulation.
- `Component_ID` must be one of: `Solvent`, `Salt`, `Additive`.
- `Name` must be a known component (see *Supported names* below).
- Duplicate formulations (same components & fractions) are skipped automatically.

**Output:**

```
Added 2 new formulations (0 skipped as duplicates).
```

### `jobcli start`

Start the scheduler as a background daemon. The process forks, writes its PID to `~/.jobcli/scheduler.pid`, and logs to `~/.jobcli/scheduler.log`.

```bash
python -m cmdline.jobcli start
```

```
Scheduler started (pid 48201).
Log: /home/user/.jobcli/scheduler.log
```

Starting a second instance while one is already running will print an error and exit.

### `jobcli stop`

Send SIGTERM to the running scheduler and wait for it to exit.

```bash
python -m cmdline.jobcli stop
```

```
Stopping scheduler (pid 48201)...
Scheduler stopped.
```

### `jobcli server-status`

Check whether the scheduler process is alive.

```bash
python -m cmdline.jobcli server-status
```

```
Scheduler is running (pid 48201).
```

or

```
Scheduler is not running.
```

### `jobcli status`

Print a one-line summary of all jobs.

```bash
python -m cmdline.jobcli status
```

```
  TOTAL_JOBS | RUNNING_JOBS | PENDING_JOBS | COMPLETED_JOBS | FAILED_JOBS
      10,000 |          128 |        1,890 |          7,950 |          32
```

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///jobcli.db` | Database connection string |
| `K8S_NAMESPACE` | `default` | K8s namespace for job pods |
| `DOCKER_IMAGE` | `byteff2-service:latest` | Docker image for runner pods |
| `CLUSTER_NUM_NODES` | `16` | Number of GPU nodes |
| `CLUSTER_GPUS_PER_NODE` | `8` | GPUs per node |
| `SCHEDULER_POLL_INTERVAL` | `5` | Seconds between scheduling cycles |
| `SCHEDULER_PID_FILE` | `~/.jobcli/scheduler.pid` | Path to scheduler PID file |
| `SCHEDULER_LOG_FILE` | `~/.jobcli/scheduler.log` | Path to scheduler log file |

---

## Supported component names

**Solvents:** EC, EMC, DMC, DEC, PC, DMSO, THF, DME, DOL, GBL, MeCN, CBS

**Salts:** LiPF6, LiBF4, LiTFSI, LiFSI, LiDFP

**Additives:** FEC, VC (plus any solvent name)

---

## Database setup

See [`job_scheduler/db/README.md`](../job_scheduler/db/README.md) for AWS RDS PostgreSQL setup instructions.
