`byteff2-service` is a containerized molecular dynamics (MD) simulation platform built on byteff-pol,
a graph-neural-network-parameterized polarizable force field.
It integrates with object storage systems (AWS S3 or MinIO) for configuration retrieval, results uploading and real-time progress tracking for long-running jobs.
It is containerized for both local execution and Kubernetes deployment.

## Build Docker Image

When building image for the first time, clone two submodule repositories first,
```bash
cd submodules/openmm
git clone --branch 8.3.1 --single-branch https://github.com/openmm/openmm.git
git clone https://github.com/z-gong/openmm-velocityVerlet.git
```
then build the image by:
```bash
# Run in the root directory where the Dockerfile is located
docker build -t byteff2-service:latest .
```

## Running a Job

### 1. Upload the config file

Upload a formulation spec config file (example: [cmdline/example_spec.json.json](cmdline/example_spec.json.json)) to your storage backend.
The default path is `byteff2-jobs/{job_name}/config.json`.

### 2. Start the container

Set the variables below and run the `docker run` command for your chosen storage backend.

```bash
TASK_NAME=...        # e.g. "test_job"
HOST_WORKSPACE=...   # host path mounted into the container, e.g. "/home/youruser/workspace"
CONTAINER_NAME="${TASK_NAME}_$(date +%Y%m%d_%H%M%S)"
```

#### Option A – AWS S3 (default)

```bash
docker run --gpus all --rm -d \
  -v "${HOST_WORKSPACE}:/app/workspace" \
  -e S3_ACCESS_KEY="${S3_ACCESS_KEY}" \
  -e S3_SECRET_KEY="${S3_SECRET_KEY}" \
  --name "${CONTAINER_NAME}" byteff2-service:latest \
  python3 /app/byteff2-service/cmdline/run_md_job.py --task_name "${TASK_NAME}"
```

Set `S3_ENDPOINT_URL` and `S3_BUCKET_NAME` to override the default endpoint or bucket.
Ensure the credentials have read/write permission on the bucket.

#### Option B – MinIO

When using a MinIO server on the host, add `--network host` so the container can reach `localhost`:

```bash
docker run --gpus all --rm -d \
  --network host \
  -v "${HOST_WORKSPACE}:/app/workspace" \
  -e MINIO_ENDPOINT="localhost:9000" \
  -e MINIO_ACCESS_KEY="minioadmin" \
  -e MINIO_SECRET_KEY="minioadmin123" \
  --name "${CONTAINER_NAME}" byteff2-service:latest \
  python3 /app/byteff2-service/cmdline/run_md_job.py --task_name "${TASK_NAME}"
```

<details>
<summary>Setting up a local MinIO server</summary>

Run [setup_minio.sh](setup_minio.sh) to launch a MinIO container with persistent storage at `$HOME/minio-data`:

```bash
bash setup_minio.sh
```

MinIO will be available on port `9000` (API) and `9001` (console).
Verify health: `curl http://localhost:9000/minio/health/live` or open `http://localhost:9001`.

</details>

### 3. Tail the logs (optional)

```bash
LOGFILE="${HOST_WORKSPACE}/${CONTAINER_NAME}.log"
docker logs -f "${CONTAINER_NAME}" &> "${LOGFILE}" &
echo "Logs streaming to ${LOGFILE}"
```

After the job completes, results are saved to `byteff2-jobs/<task_name>/` in the configured bucket.

### Environment variables

All supported environment variables are documented in [cmdline/.env.example](cmdline/.env.example).

#### Storage backend

| Variable | Description |
|---|---|
| `JOB_STORAGE_TYPE` | `S3` (default) or `MINIO`. |
| **S3** | |
| `S3_ENDPOINT_URL` | S3 endpoint URL (e.g. `https://s3.amazonaws.com`). |
| `S3_ACCESS_KEY` | S3 access key (**required**). |
| `S3_SECRET_KEY` | S3 secret key (**required**). |
| `S3_BUCKET_NAME` | Bucket name. Falls back to the default bucket when unset. |
| **MinIO** | |
| `MINIO_ENDPOINT` | MinIO endpoint (e.g. `minio.example.com:9000`). |
| `MINIO_ACCESS_KEY` | MinIO access key (**required** when using MinIO). |
| `MINIO_SECRET_KEY` | MinIO secret key (**required** when using MinIO). |
| `MINIO_BUCKET` | Bucket name. Falls back to the default bucket when unset. |
| `MINIO_SECURE` | `true` to use TLS/HTTPS (default: `false`). |

#### Runtime

| Variable | Description |
|---|---|
| `WORKSPACE_DIR` | Working root directory inside the container (default: `/app/workspace`). |
| `PROGRESS_UPDATE_INTERVAL` | Interval in seconds for reporting job progress to storage (default: `3600`). |
| `DEBUG_TOTAL_STEPS` | Override total simulation steps for NPT, NVT, and NEMD stages. Unset by default. |

> **Quick-test tip:** Add `-e DEBUG_TOTAL_STEPS="500000"` to run a short simulation for
> smoke-testing. Very small step counts reduce run time but may cause post-analysis errors
> due to insufficient sampling.
