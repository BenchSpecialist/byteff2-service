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

## Run the container

Upload a formulation spec config file (example: [cmdline/example_spec.json.json](cmdline/example_spec.json.json))
to S3 in the target bucket (defaults to `byteff2-jobs/{job_name}/config.json` if not set),
then start the container:

```bash
#!/bin/bash
TASK_NAME=...
HOST_WORKSPACE=...

CONTAINER_NAME="${TASK_NAME}_$(date +%Y%m%d_%H%M%S)"
LOGFILE="${HOST_WORKSPACE}/${CONTAINER_NAME}.log"

# Run container in background
docker run --gpus all --rm -d \
  -v $HOST_WORKSPACE:/app/workspace \
  -e S3_ACCESS_KEY="${S3_ACCESS_KEY}" -e S3_SECRET_KEY="${S3_SECRET_KEY}" \
  --name $CONTAINER_NAME byteff2-service:latest \
  python3 /app/byteff2-service/cmdline/run_md_job.py --task_name $TASK_NAME

# Stream logs to file in background
docker logs -f $CONTAINER_NAME &> $LOGFILE &
echo "Container $CONTAINER_NAME started. Logs streamed to $LOGFILE"
```

### Environment variables

All supported environment variables are documented in [cmdline/.env.example](cmdline/.env.example).
The key variables are summarised below.

#### Storage backend

| Variable | Description |
|---|---|
| `JOB_STORAGE_TYPE` | Storage backend to use. Supported values: `S3` (default) or `MINIO`. |
| `S3_ENDPOINT_URL` | S3 endpoint URL (e.g. `https://s3.amazonaws.com`). |
| `S3_ACCESS_KEY` | S3 access key (**required**). |
| `S3_SECRET_KEY` | S3 secret key (**required**). |
| `S3_BUCKET_NAME` | S3 bucket name. Falls back to the default bucket when unset. |
| `MINIO_ENDPOINT` | MinIO endpoint (e.g. `minio.example.com:9000`). Used when `JOB_STORAGE_TYPE=MINIO`. |
| `MINIO_ACCESS_KEY` | MinIO access key (**required** when using MinIO). |
| `MINIO_SECRET_KEY` | MinIO secret key (**required** when using MinIO). |
| `MINIO_BUCKET` | MinIO bucket name. Falls back to the default bucket when unset. |
| `MINIO_SECURE` | Set to `true` to use TLS (HTTPS) for the MinIO connection (default: `false`). |

To change the S3 bucket used for job management, set `S3_ENDPOINT_URL` and `S3_BUCKET_NAME` in the
`docker run` command. Make sure `S3_ACCESS_KEY` and `S3_SECRET_KEY` match the bucket credentials
and have read/write permissions.

#### Runtime settings

| Variable | Description |
|---|---|
| `WORKSPACE_DIR` | Working root directory inside the container (default: `/app/workspace`). |
| `PROGRESS_UPDATE_INTERVAL` | Interval in seconds at which job progress is reported to storage (default: `3600`). |
| `DEBUG_TOTAL_STEPS` | Override the total simulation steps for all three stages (NPT, NVT, and NEMD) to the given value. Unset by default (uses the values from the job config). |

> **Quick-test tip:** Add `-e DEBUG_TOTAL_STEPS="500000"` to the `docker run` command to run a
> short simulation for smoke-testing. Be aware that a very small step count will reduce run time
> significantly but will cause errors in post-analysis due to insufficient sampling.

