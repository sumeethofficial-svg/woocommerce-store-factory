import pytest

from app.config import get_settings
from app.database import SessionLocal
from app.models import Store, StoreStatus
from app.services import provisioner
from tests.conftest import register_and_login


def create(client, auth, name="demo"):
    return client.post("/api/stores", json={"name": name}, headers=auth)


def status_of(client, auth, store_id):
    return client.get(f"/api/stores/{store_id}", headers=auth).json()


def kinds(k8s, namespace="store-demo"):
    return {kind for kind, ns, _ in k8s.objects if ns == namespace}


def test_create_provisions_ready_store(client, auth, k8s):
    response = create(client, auth)
    assert response.status_code == 201

    store = status_of(client, auth, response.json()["id"])
    assert store["status"] == "ready"
    assert store["namespace"] == "store-demo"
    assert store["url"] == "http://demo.localhost"
    assert store["admin_url"] == "http://demo.localhost/wp-admin/"
    assert "store-demo" in k8s.namespaces
    assert kinds(k8s) == {
        "ResourceQuota", "LimitRange", "Secret", "ConfigMap", "Service", "StatefulSet",
        "PersistentVolumeClaim", "Deployment", "Ingress", "Job",
    }


def test_events_record_lifecycle(client, auth, k8s):
    store_id = create(client, auth).json()["id"]
    events = client.get(f"/api/stores/{store_id}/events", headers=auth).json()
    messages = [event["message"] for event in events]
    assert messages[0] == "Store requested"
    assert messages[-1] == "Store is ready"
    assert "MySQL is ready" in messages


def test_admin_credentials_available_when_ready(client, auth, k8s):
    store_id = create(client, auth).json()["id"]
    body = client.get(f"/api/stores/{store_id}/credentials", headers=auth).json()
    assert body["username"] == "admin"
    assert len(body["password"]) >= 16
    assert body["admin_url"] == "http://demo.localhost/wp-admin/"


def test_credentials_hidden_until_ready(client, auth, k8s, monkeypatch):
    k8s.workloads_ready = False
    monkeypatch.setattr(get_settings(), "mysql_ready_timeout", 0)
    store_id = create(client, auth).json()["id"]
    response = client.get(f"/api/stores/{store_id}/credentials", headers=auth)
    assert response.status_code == 409


@pytest.mark.parametrize("name", ["ab", "Demo", "9lives", "-demo", "demo-", "has_underscore", "a" * 31])
def test_invalid_names_rejected(client, auth, k8s, name):
    assert create(client, auth, name).status_code == 422


def test_duplicate_name_conflicts(client, auth, k8s):
    assert create(client, auth).status_code == 201
    assert create(client, auth).status_code == 409


def test_store_name_is_unique_across_users(client, auth, k8s):
    create(client, auth)
    other = register_and_login(client, "other")
    assert create(client, other).status_code == 409


def test_store_limit_enforced(client, auth, k8s, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_stores_per_user", 1)
    assert create(client, auth, "first").status_code == 201
    assert create(client, auth, "second").status_code == 403


def test_stores_are_private_to_owner(client, auth, k8s):
    store_id = create(client, auth).json()["id"]
    other = register_and_login(client, "other")
    assert client.get("/api/stores", headers=other).json() == []
    assert client.get(f"/api/stores/{store_id}", headers=other).status_code == 404
    assert client.delete(f"/api/stores/{store_id}", headers=other).status_code == 404
    assert client.get(f"/api/stores/{store_id}/credentials", headers=other).status_code == 404


def test_delete_removes_namespace_and_frees_name(client, auth, k8s):
    store_id = create(client, auth).json()["id"]
    response = client.delete(f"/api/stores/{store_id}", headers=auth)
    assert response.status_code == 202
    assert "store-demo" not in k8s.namespaces
    assert client.get(f"/api/stores/{store_id}", headers=auth).status_code == 404
    assert client.get("/api/stores", headers=auth).json() == []
    assert create(client, auth).status_code == 201
    assert "store-demo" in k8s.namespaces


def test_failure_marks_store_failed_then_retry_recovers(client, auth, k8s):
    k8s.fail_on["create_namespaced_deployment"] = 1
    store_id = create(client, auth).json()["id"]

    failed = status_of(client, auth, store_id)
    assert failed["status"] == "failed"
    assert "create_namespaced_deployment" in failed["error"]

    before = len(k8s.create_calls)
    response = client.post(f"/api/stores/{store_id}/retry", headers=auth)
    assert response.status_code == 202
    assert status_of(client, auth, store_id)["status"] == "ready"
    assert len(k8s.create_calls) == len(set(k8s.create_calls))
    assert len(k8s.create_calls) > before


def test_retry_only_for_failed_stores(client, auth, k8s):
    store_id = create(client, auth).json()["id"]
    assert client.post(f"/api/stores/{store_id}/retry", headers=auth).status_code == 409


def test_init_job_failure_reports_wpcli_output(client, auth, k8s):
    k8s.job_outcome = "failed"
    store_id = create(client, auth).json()["id"]
    store = status_of(client, auth, store_id)
    assert store["status"] == "failed"
    assert "plugin download failed" in store["error"]

    k8s.job_outcome = "succeeded"
    client.post(f"/api/stores/{store_id}/retry", headers=auth)
    assert status_of(client, auth, store_id)["status"] == "ready"


def test_mysql_readiness_timeout_fails_store(client, auth, k8s, monkeypatch):
    k8s.workloads_ready = False
    monkeypatch.setattr(get_settings(), "mysql_ready_timeout", 0)
    store_id = create(client, auth).json()["id"]
    store = status_of(client, auth, store_id)
    assert store["status"] == "failed"
    assert "waiting for MySQL" in store["error"]
    assert "Deployment" not in kinds(k8s)


def test_unmanaged_namespace_is_never_adopted(client, auth, k8s):
    k8s.namespaces["store-demo"] = {"metadata": {"labels": {}}}
    store_id = create(client, auth).json()["id"]
    store = status_of(client, auth, store_id)
    assert store["status"] == "failed"
    assert "not managed" in store["error"]


def test_provisioning_is_idempotent_when_rerun(client, auth, k8s):
    store_id = create(client, auth).json()["id"]
    with SessionLocal() as db:
        db.get(Store, store_id).status = StoreStatus.PROVISIONING
        db.commit()
    provisioner.provision_store(store_id)
    assert status_of(client, auth, store_id)["status"] == "ready"
    assert len(k8s.create_calls) == len(set(k8s.create_calls))


def test_provisioning_skips_stores_being_deleted(client, auth, k8s):
    store_id = create(client, auth).json()["id"]
    k8s.namespaces.clear()
    k8s.objects.clear()
    with SessionLocal() as db:
        db.get(Store, store_id).status = StoreStatus.DELETING
        db.commit()
    provisioner.provision_store(store_id)
    assert k8s.namespaces == {}


def test_resume_pending_restarts_interrupted_work(client, auth, k8s):
    store_id = create(client, auth).json()["id"]
    k8s.namespaces.clear()
    k8s.objects.clear()
    with SessionLocal() as db:
        db.get(Store, store_id).status = StoreStatus.INITIALIZING
        db.commit()
    provisioner.resume_pending()
    assert status_of(client, auth, store_id)["status"] == "ready"
    assert "store-demo" in k8s.namespaces


def test_resume_pending_finishes_interrupted_deletion(client, auth, k8s):
    store_id = create(client, auth).json()["id"]
    with SessionLocal() as db:
        db.get(Store, store_id).status = StoreStatus.DELETING
        db.commit()
    provisioner.resume_pending()
    assert "store-demo" not in k8s.namespaces
    assert client.get(f"/api/stores/{store_id}", headers=auth).status_code == 404
