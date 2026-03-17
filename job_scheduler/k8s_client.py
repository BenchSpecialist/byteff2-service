"""
Kubernetes pod management for MD simulation jobs.
"""

import logging
import re
from typing import Optional

from kubernetes import client, config as k8s_config

logger = logging.getLogger(__name__)


def _sanitise_pod_name(raw: str) -> str:
    """Convert an arbitrary string into a valid K8s pod name segment."""
    name = raw.lower()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return name[:50]


class K8sJobManager:
    """Manages Kubernetes pods for MD simulation jobs."""

    def __init__(
        self,
        namespace: str,
        image: str,
        gpu_resource: str = "nvidia.com/gpu",
    ):
        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()

        self.v1 = client.CoreV1Api()
        self.namespace = namespace
        self.image = image
        self.gpu_resource = gpu_resource

    def create_job_pod(
        self, formulation_uid: str, database_url: str
    ) -> str:
        """Create a K8s pod for a single MD job.

        :param formulation_uid: Formulation UID passed to the runner.
        :param database_url: Database connection string for the runner.
        :return: Pod name.
        """
        pod_name = f"md-job-{_sanitise_pod_name(formulation_uid)}"

        pod = client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                namespace=self.namespace,
                labels={
                    "app": "jobcli-md",
                    "formulation-uid": _sanitise_pod_name(formulation_uid),
                },
            ),
            spec=client.V1PodSpec(
                restart_policy="Never",
                containers=[
                    client.V1Container(
                        name="md-runner",
                        image=self.image,
                        command=["python3", "-m", "job_scheduler.runner"],
                        args=["--formulation_uid", formulation_uid],
                        env=[
                            client.V1EnvVar(
                                name="DATABASE_URL", value=database_url
                            ),
                        ],
                        resources=client.V1ResourceRequirements(
                            limits={self.gpu_resource: "1"},
                            requests={self.gpu_resource: "1"},
                        ),
                    )
                ],
            ),
        )

        self.v1.create_namespaced_pod(namespace=self.namespace, body=pod)
        logger.info(f"Created pod {pod_name} for formulation {formulation_uid}")
        return pod_name

    def get_pod_status(self, pod_name: str) -> Optional[str]:
        """Get pod phase: Pending, Running, Succeeded, Failed, Unknown."""
        try:
            pod = self.v1.read_namespaced_pod(
                name=pod_name, namespace=self.namespace
            )
            return pod.status.phase
        except client.ApiException as e:
            if e.status == 404:
                return None
            raise

    def delete_pod(self, pod_name: str):
        """Delete a completed or failed pod."""
        try:
            self.v1.delete_namespaced_pod(
                name=pod_name,
                namespace=self.namespace,
                body=client.V1DeleteOptions(grace_period_seconds=0),
            )
            logger.info(f"Deleted pod {pod_name}")
        except client.ApiException as e:
            if e.status != 404:
                raise

    def list_active_job_pods(self) -> dict[str, str]:
        """List all pods with label ``app=jobcli-md``.

        :return: Dict of ``pod_name -> phase``.
        """
        pods = self.v1.list_namespaced_pod(
            namespace=self.namespace,
            label_selector="app=jobcli-md",
        )
        return {pod.metadata.name: pod.status.phase for pod in pods.items}
