import pytest
from kubernetes import client

from app.config import get_settings
from app.database import SessionLocal
from app.k8s import manifests as m
from app.k8s.cluster import Cluster
from app.models import Store
from app.services import provisioner
from tests.conftest import register_and_login
from tests.k8s_http_fake import FakeApiServer
from tests.schema_check import problems
from tests.test_manifests import all_manifests, make_spec

KIND_MODELS = {
    "Namespace": "V1Namespace", "ResourceQuota": "V1ResourceQuota", "LimitRange": "V1LimitRange", "Secret": "V1Secret",
    "ConfigMap": "V1ConfigMap", "Service": "V1Service", "StatefulSet": "V1StatefulSet",
    "PersistentVolumeClaim": "V1PersistentVolumeClaim", "Deployment": "V1Deployment",
    "Ingress": "V1Ingress", "Job": "V1Job",
}


@pytest.fixture
def api_server():
    server = FakeApiServer().start()
    yield server
    server.stop()


@pytest.fixture
def real_cluster(api_server, monkeypatch):
    api_client = client.ApiClient(client.Configuration(host=api_server.url))
    cluster = Cluster(
        core=client.CoreV1Api(api_client),
        apps=client.AppsV1Api(api_client),
        batch=client.BatchV1Api(api_client),
        networking=client.NetworkingV1Api(api_client),
    )
    monkeypatch.setattr(provisioner, "get_cluster", lambda: cluster)
    monkeypatch.setattr("app.api.stores.get_cluster", lambda: cluster)
    return cluster


@pytest.mark.parametrize("manifest", all_manifests(make_spec()), ids=lambda x: x["kind"])
def test_manifest_matches_kubernetes_schema(manifest):
    assert problems(manifest, KIND_MODELS[manifest["kind"]]) == []


@pytest.mark.parametrize("ingress", [True, False])
def test_manifests_match_schema_with_storage_class(ingress):
    spec = make_spec(storage_class="fast", ingress_enabled=ingress)
    for manifest in (m.mysql_statefulset(spec), m.wordpress_pvc(spec)):
        assert problems(manifest, KIND_MODELS[manifest["kind"]]) == []


def test_schema_checker_catches_typos():
    bad = {"spec": {"replicaz": 1}}
    assert problems(bad, "V1Deployment") == [".spec.replicaz: unknown field for V1DeploymentSpec"]


def test_full_lifecycle_through_real_client_over_http(client, api_server, real_cluster):
    headers = register_and_login(client)
    response = client.post("/api/stores", json={"name": "demo"}, headers=headers)
    store_id = response.json()["id"]

    store = client.get(f"/api/stores/{store_id}", headers=headers).json()
    assert store["status"] == "ready", store["error"]
    assert api_server.rejections == []
    assert "store-demo" in api_server.namespaces
    assert api_server.kinds("store-demo") == {
        "ResourceQuota", "LimitRange", "Secret", "ConfigMap", "Service", "StatefulSet",
        "PersistentVolumeClaim", "Deployment", "Ingress", "Job",
    }

    creds = client.get(f"/api/stores/{store_id}/credentials", headers=headers).json()
    assert creds["username"] == "admin" and len(creds["password"]) >= 16

    provisioner.provision_store(store_id)
    with SessionLocal() as db:
        db.get(Store, store_id).status = "requested"
        db.commit()
    provisioner.provision_store(store_id)
    assert client.get(f"/api/stores/{store_id}", headers=headers).json()["status"] == "ready"
    assert api_server.rejections == []

    assert client.delete(f"/api/stores/{store_id}", headers=headers).status_code == 202
    assert api_server.namespaces == {}
    assert api_server.objects == {}
    assert client.get("/api/stores", headers=headers).json() == []


def test_job_failure_surfaces_pod_logs(client, api_server, real_cluster):
    api_server.job_fails = True
    headers = register_and_login(client)
    store_id = client.post("/api/stores", json={"name": "demo"}, headers=headers).json()["id"]
    store = client.get(f"/api/stores/{store_id}", headers=headers).json()
    assert store["status"] == "failed"
    assert "could not download WooCommerce" in store["error"]


def test_readiness_is_polled_not_slept(client, api_server, real_cluster, monkeypatch):
    api_server.ready_after = 0.4
    monkeypatch.setattr(get_settings(), "poll_interval_seconds", 0.05)
    headers = register_and_login(client)
    store_id = client.post("/api/stores", json={"name": "demo"}, headers=headers).json()["id"]
    assert client.get(f"/api/stores/{store_id}", headers=headers).json()["status"] == "ready"
    status_reads = [p for _, p in api_server.requests if p.endswith("/status")]
    assert len(status_reads) > 3


def test_full_launch_spec_through_real_client(client, api_server, real_cluster, monkeypatch):
    from app.config import Settings

    settings = get_settings()
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_abc123")
    monkeypatch.setattr(settings, "razorpay_key_secret", Settings(razorpay_key_secret="shh").razorpay_key_secret)
    headers = register_and_login(client)
    response = client.post(
        "/api/stores",
        json={
            "name": "demo",
            "admin_password": "Wgr^3czxSs$U*F*@",
            "storage_gi": 2,
            "products": [{"name": "Classic T-Shirt", "price": 499, "description": "A classic cotton t-shirt"}],
        },
        headers=headers,
    )
    store_id = response.json()["id"]
    store = client.get(f"/api/stores/{store_id}", headers=headers).json()
    assert store["status"] == "ready", store["error"]
    assert api_server.rejections == []

    objects = {(kind, name): body for (kind, ns, name), (body, _) in api_server.objects.items() if ns == "store-demo"}
    assert objects[("PersistentVolumeClaim", m.WORDPRESS_PVC)]["spec"]["resources"]["requests"]["storage"] == "2Gi"
    assert objects[("Secret", m.ADMIN_SECRET)]["stringData"]["ADMIN_PASSWORD"] == "Wgr^3czxSs$U*F*@"
    assert ("Secret", m.RAZORPAY_SECRET) in objects
    assert "Classic T-Shirt" in objects[("ConfigMap", m.SCRIPTS_CONFIG)]["data"]["products.json"]

    creds = client.get(f"/api/stores/{store_id}/credentials", headers=headers).json()
    assert creds["password"] == "Wgr^3czxSs$U*F*@"
