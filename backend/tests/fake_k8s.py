import base64
from types import SimpleNamespace

from kubernetes.client.exceptions import ApiException

KINDS = {
    "secret": "Secret",
    "config_map": "ConfigMap",
    "service": "Service",
    "persistent_volume_claim": "PersistentVolumeClaim",
    "resource_quota": "ResourceQuota",
    "limit_range": "LimitRange",
    "stateful_set": "StatefulSet",
    "deployment": "Deployment",
    "ingress": "Ingress",
    "job": "Job",
}


class FakeK8s:
    def __init__(self) -> None:
        self.namespaces: dict[str, dict] = {}
        self.objects: dict[tuple[str, str, str], dict] = {}
        self.workloads_ready = True
        self.job_outcome = "succeeded"
        self.fail_on: dict[str, int] = {}
        self.create_calls: list[tuple[str, str, str]] = []

    def _maybe_fail(self, method: str) -> None:
        if self.fail_on.get(method, 0) > 0:
            self.fail_on[method] -= 1
            raise ApiException(status=500, reason=f"injected failure in {method}")

    def create_namespace(self, body):
        self._maybe_fail("create_namespace")
        name = body["metadata"]["name"]
        if name in self.namespaces:
            raise ApiException(status=409)
        self.namespaces[name] = body

    def read_namespace(self, name):
        if name not in self.namespaces:
            raise ApiException(status=404)
        return SimpleNamespace(metadata=SimpleNamespace(labels=self.namespaces[name]["metadata"]["labels"]))

    def delete_namespace(self, name):
        if name not in self.namespaces:
            raise ApiException(status=404)
        del self.namespaces[name]
        self.objects = {k: v for k, v in self.objects.items() if k[1] != name}

    def _create(self, method, kind, namespace, body):
        self._maybe_fail(method)
        if namespace not in self.namespaces:
            raise ApiException(status=404)
        key = (kind, namespace, body["metadata"]["name"])
        if key in self.objects:
            raise ApiException(status=409)
        self.objects[key] = body
        self.create_calls.append(key)

    def __getattr__(self, name):
        if name.startswith("create_namespaced_"):
            kind = KINDS[name.removeprefix("create_namespaced_")]
            return lambda namespace, body: self._create(name, kind, namespace, body)
        raise AttributeError(name)

    def read_namespaced_secret(self, name, namespace):
        body = self.objects.get(("Secret", namespace, name))
        if body is None:
            raise ApiException(status=404)
        data = {k: base64.b64encode(v.encode()).decode() for k, v in body["stringData"].items()}
        return SimpleNamespace(data=data)

    def _workload(self, kind, name, namespace):
        if (kind, namespace, name) not in self.objects:
            raise ApiException(status=404)
        ready = 1 if self.workloads_ready else 0
        return SimpleNamespace(status=SimpleNamespace(ready_replicas=ready))

    def read_namespaced_stateful_set_status(self, name, namespace):
        return self._workload("StatefulSet", name, namespace)

    def read_namespaced_deployment_status(self, name, namespace):
        return self._workload("Deployment", name, namespace)

    def read_namespaced_job_status(self, name, namespace):
        if ("Job", namespace, name) not in self.objects:
            raise ApiException(status=404)
        succeeded = 1 if self.job_outcome == "succeeded" else None
        conditions = (
            [SimpleNamespace(type="Failed", status="True")] if self.job_outcome == "failed" else []
        )
        return SimpleNamespace(status=SimpleNamespace(succeeded=succeeded, conditions=conditions))

    def delete_namespaced_job(self, name, namespace, propagation_policy=None):
        if self.objects.pop(("Job", namespace, name), None) is None:
            raise ApiException(status=404)

    def list_namespaced_pod(self, namespace, label_selector=None):
        return SimpleNamespace(items=[SimpleNamespace(metadata=SimpleNamespace(name="init-pod"))])

    def read_namespaced_pod_log(self, name, namespace, tail_lines=None):
        return "Error: plugin download failed"
