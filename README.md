# Byteff2 Service

Byteff2 service is a containerized molecular dynamics (MD) simulation platform built on byteff-pol,
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

## Local setup
