# Byteff2 Service

Byteff2 service is a containerized molecular dynamics (MD) simulation platform built on byteff-pol,
a graph-neural-network-parameterized polarizable force field.
It integrates with object storage systems (AWS S3 or MinIO) for configuration retrieval, results uploading and real-time progress tracking for long-running jobs.
It is containerized for both local execution and Kubernetes deployment.