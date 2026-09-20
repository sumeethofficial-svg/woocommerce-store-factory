import inspect

from kubernetes import client

from app.k8s.cluster import Cluster

READ_METHODS = {
    "CoreV1Api": [
        "create_namespace", "read_namespace", "delete_namespace", "read_namespaced_secret",
        "list_namespaced_pod", "read_namespaced_pod_log",
    ],
    "AppsV1Api": ["read_namespaced_stateful_set_status", "read_namespaced_deployment_status"],
    "BatchV1Api": ["read_namespaced_job_status", "delete_namespaced_job"],
}


def test_cluster_binds_to_real_kubernetes_client():
    cluster = Cluster(
        core=client.CoreV1Api(),
        apps=client.AppsV1Api(),
        batch=client.BatchV1Api(),
        networking=client.NetworkingV1Api(),
    )
    for create in cluster._creators.values():
        params = inspect.signature(create).parameters
        assert list(params)[:2] == ["namespace", "body"]


def test_used_client_methods_exist():
    for api_name, methods in READ_METHODS.items():
        api = getattr(client, api_name)
        for method in methods:
            assert hasattr(api, method), f"{api_name}.{method}"


def test_keyword_arguments_are_supported_by_client():
    delete_job = inspect.getsource(client.BatchV1Api.delete_namespaced_job_with_http_info)
    pod_log = inspect.getsource(client.CoreV1Api.read_namespaced_pod_log_with_http_info)
    list_pods = inspect.getsource(client.CoreV1Api.list_namespaced_pod_with_http_info)
    assert "'propagation_policy'" in delete_job
    assert "'tail_lines'" in pod_log
    assert "'label_selector'" in list_pods
