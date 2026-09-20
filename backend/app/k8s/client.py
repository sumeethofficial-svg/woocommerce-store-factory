from functools import lru_cache

from kubernetes import client, config

from app.k8s.cluster import Cluster


def load_kubernetes_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


@lru_cache
def get_cluster() -> Cluster:
    load_kubernetes_config()
    return Cluster(
        core=client.CoreV1Api(),
        apps=client.AppsV1Api(),
        batch=client.BatchV1Api(),
        networking=client.NetworkingV1Api(),
    )
