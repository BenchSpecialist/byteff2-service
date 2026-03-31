#!/usr/bin/env bash
# Setup a local MinIO server for MD simulation tasks.
#
# Environment variables (with defaults matching minio_helper.py):
#   MINIO_PORT       - API port        (default: 9000)
#   MINIO_CONSOLE    - Console port    (default: 9001)
#   MINIO_ACCESS_KEY - Root user       (default: minioadmin)
#   MINIO_SECRET_KEY - Root password   (default: minioadmin123)

set -e

MINIO_PORT="${MINIO_PORT:-9000}"
MINIO_CONSOLE="${MINIO_CONSOLE:-9001}"
MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-minioadmin}"
MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-minioadmin123}"
CONTAINER_NAME="minio-md-sim"

echo "Starting MinIO container ($CONTAINER_NAME) ..."
MINIO_DATA="$HOME/minio-data"
rm -rf $MINIO_DATA
mkdir -p "$MINIO_DATA"

docker run -d --name "$CONTAINER_NAME" \
  -p "$MINIO_PORT":9000 \
  -p "$MINIO_CONSOLE":9001 \
  -v "$MINIO_DATA":/data \
  -e MINIO_ROOT_USER="$MINIO_ACCESS_KEY" \
  -e MINIO_ROOT_PASSWORD="$MINIO_SECRET_KEY" \
  minio/minio server /data --console-address ":9001"

echo "Waiting for MinIO to be ready ..."
for i in $(seq 1 30); do
  if curl -sf "http://localhost:${MINIO_PORT}/minio/health/live" > /dev/null 2>&1; then
    echo "MinIO is healthy."
    break
  fi
  sleep 1
done

echo "MinIO is ready."
echo "Access the console at http://localhost:${MINIO_CONSOLE}"
echo "Root user: ${MINIO_ACCESS_KEY}"
echo "Root password: ${MINIO_SECRET_KEY}"