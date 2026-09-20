import base64
from typing import Any

from kubernetes.client.exceptions import ApiException

from app.k8s.manifests import MANAGED_BY_LABEL, STORE_ID_LABEL

JOB_RUNNING = "running"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"


class NamespaceConflict(RuntimeError):
    pass


def _absent(exc: ApiException) -> bool:
    return exc.status == 404


class Cluster:
    def __init__(self, core, apps, batch, networking) -> None:
        self.core = core
        self.apps = apps
        self.batch = batch
        self.networking = networking
        self._creators = {
            "Secret": core.create_namespaced_secret,
            "ConfigMap": core.create_namespaced_config_map,
            "Service": core.create_namespaced_service,
            "PersistentVolumeClaim": core.create_namespaced_persistent_volume_claim,
            "ResourceQuota": core.create_namespaced_resource_quota,
            "LimitRange": core.create_namespaced_limit_range,
            "StatefulSet": apps.create_namespaced_stateful_set,
            "Deployment": apps.create_namespaced_deployment,
            "Job": batch.create_namespaced_job,
            "Ingress": networking.create_namespaced_ingress,
        }

    def ensure(self, manifest: dict[str, Any]) -> bool:
        create = self._creators[manifest["kind"]]
        try:
            create(manifest["metadata"]["namespace"], body=manifest)
        except ApiException as exc:
            if exc.status == 409:
                return False
            raise
        return True

    def ensure_namespace(self, manifest: dict[str, Any]) -> bool:
        name = manifest["metadata"]["name"]
        try:
            self.core.create_namespace(body=manifest)
            return True
        except ApiException as exc:
            if exc.status != 409:
                raise
        labels = self.core.read_namespace(name).metadata.labels or {}
        expected = manifest["metadata"]["labels"]
        if any(labels.get(key) != expected[key] for key in (MANAGED_BY_LABEL, STORE_ID_LABEL)):
            raise NamespaceConflict(f"Namespace {name} exists and is not managed by this store")
        return False

    def namespace_exists(self, name: str) -> bool:
        try:
            self.core.read_namespace(name)
        except ApiException as exc:
            if _absent(exc):
                return False
            raise
        return True

    def delete_namespace(self, name: str) -> None:
        try:
            self.core.delete_namespace(name)
        except ApiException as exc:
            if not _absent(exc):
                raise

    def statefulset_ready(self, namespace: str, name: str) -> bool:
        status = self.apps.read_namespaced_stateful_set_status(name, namespace).status
        return (status.ready_replicas or 0) >= 1

    def deployment_ready(self, namespace: str, name: str) -> bool:
        status = self.apps.read_namespaced_deployment_status(name, namespace).status
        return (status.ready_replicas or 0) >= 1

    def job_state(self, namespace: str, name: str) -> str | None:
        try:
            status = self.batch.read_namespaced_job_status(name, namespace).status
        except ApiException as exc:
            if _absent(exc):
                return None
            raise
        if (status.succeeded or 0) > 0:
            return JOB_SUCCEEDED
        if any(c.type == "Failed" and c.status == "True" for c in status.conditions or []):
            return JOB_FAILED
        return JOB_RUNNING

    def delete_job(self, namespace: str, name: str) -> None:
        try:
            self.batch.delete_namespaced_job(name, namespace, propagation_policy="Background")
        except ApiException as exc:
            if not _absent(exc):
                raise

    def job_log_tail(self, namespace: str, job: str, lines: int = 20) -> str:
        try:
            pods = self.core.list_namespaced_pod(namespace, label_selector=f"job-name={job}")
            if not pods.items:
                return ""
            return self.core.read_namespaced_pod_log(
                pods.items[-1].metadata.name, namespace, tail_lines=lines
            )
        except ApiException:
            return ""

    def read_secret(self, namespace: str, name: str) -> dict[str, str] | None:
        try:
            secret = self.core.read_namespaced_secret(name, namespace)
        except ApiException as exc:
            if _absent(exc):
                return None
            raise
        return {key: base64.b64decode(value).decode() for key, value in (secret.data or {}).items()}
