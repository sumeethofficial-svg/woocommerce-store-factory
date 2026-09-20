import json
import re
import shutil
import subprocess

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text

from app.config import Settings, get_settings
from app.database import SessionLocal, migrate
from app.k8s import manifests as m
from app.models import Store, StoreStatus
from tests.conftest import register_and_login
from tests.test_manifests import make_spec

PRODUCTS = [
    {"name": "Classic T-Shirt", "price": 499, "description": "A classic cotton t-shirt"},
    {"name": "Denim Jeans", "price": 1299, "description": "Blue denim jeans"},
    {"name": "Sneakers", "price": 2499, "description": "Comfortable running shoes"},
]


def launch(client, auth, name="demo", **fields):
    return client.post("/api/stores", json={"name": name, **fields}, headers=auth)


def config_map(k8s, name, namespace="store-demo"):
    return k8s.objects[("ConfigMap", namespace, name)]


def test_defaults_are_three_stores_and_five_gi():
    settings = get_settings()
    assert settings.max_stores_per_user == 3
    assert settings.max_storage_gi_per_user == 5
    assert settings.wordpress_image == "wordpress:latest"


def test_store_limit_of_three_is_enforced_by_backend(client, auth, k8s):
    for name in ("one", "two", "three"):
        assert launch(client, auth, name).status_code == 201
    response = launch(client, auth, "four")
    assert response.status_code == 403
    assert "limit" in response.json()["detail"].lower()
    assert len(client.get("/api/stores", headers=auth).json()) == 3


def test_storage_quota_sums_wordpress_storage_across_stores(client, auth, k8s):
    assert launch(client, auth, "alpha", storage_gi=2).status_code == 201
    assert launch(client, auth, "bravo", storage_gi=3).status_code == 201
    usage = client.get("/api/stores/usage", headers=auth).json()
    assert usage["storage_gi"] == {"used": 5, "limit": 5}
    assert usage["stores"] == {"used": 2, "limit": 3}

    response = launch(client, auth, "charlie", storage_gi=1)
    assert response.status_code == 403
    assert "quota" in response.json()["detail"].lower()
    assert client.get("/api/stores/usage", headers=auth).json()["stores"]["used"] == 2


def test_single_request_cannot_exceed_remaining_quota(client, auth, k8s):
    launch(client, auth, "alpha", storage_gi=3)
    assert launch(client, auth, "bravo", storage_gi=3).status_code == 403
    assert launch(client, auth, "bravo", storage_gi=2).status_code == 201


def test_per_store_maximum_and_minimum(client, auth, k8s):
    assert launch(client, auth, storage_gi=6).status_code == 422
    assert launch(client, auth, storage_gi=0).status_code == 422


def test_deleting_a_store_frees_its_quota(client, auth, k8s):
    store_id = launch(client, auth, "alpha", storage_gi=5).json()["id"]
    assert launch(client, auth, "bravo", storage_gi=1).status_code == 403
    client.delete(f"/api/stores/{store_id}", headers=auth)
    assert client.get("/api/stores/usage", headers=auth).json()["storage_gi"]["used"] == 0
    assert launch(client, auth, "bravo", storage_gi=5).status_code == 201


def test_quotas_are_per_user(client, auth, k8s):
    launch(client, auth, "alpha", storage_gi=5)
    other = register_and_login(client, "other")
    assert launch(client, other, "bravo", storage_gi=5).status_code == 201
    assert client.get("/api/stores/usage", headers=other).json()["storage_gi"]["used"] == 5


def test_usage_requires_authentication(client):
    assert client.get("/api/stores/usage").status_code == 401


def test_pvc_size_matches_selected_storage(client, auth, k8s):
    launch(client, auth, storage_gi=2)
    pvc = k8s.objects[("PersistentVolumeClaim", "store-demo", m.WORDPRESS_PVC)]
    assert pvc["spec"]["resources"]["requests"]["storage"] == "2Gi"
    sts = k8s.objects[("StatefulSet", "store-demo", m.MYSQL)]
    assert sts["spec"]["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"] == "1Gi"


def test_namespace_quota_and_limit_range_track_storage(client, auth, k8s):
    launch(client, auth, storage_gi=2)
    quota = k8s.objects[("ResourceQuota", "store-demo", m.QUOTA)]
    assert quota["spec"]["hard"]["requests.storage"] == "3Gi"
    limits = k8s.objects[("LimitRange", "store-demo", m.LIMIT_RANGE)]["spec"]["limits"]
    pvc_limit = next(item for item in limits if item["type"] == "PersistentVolumeClaim")
    assert pvc_limit["max"]["storage"] == "2Gi"


def test_admin_password_reaches_the_kubernetes_secret(client, auth, k8s):
    launch(client, auth, admin_password="Wgr^3czxSs$U*F*@")
    secret = k8s.objects[("Secret", "store-demo", m.ADMIN_SECRET)]
    assert secret["stringData"]["ADMIN_PASSWORD"] == "Wgr^3czxSs$U*F*@"
    store_id = client.get("/api/stores", headers=auth).json()[0]["id"]
    creds = client.get(f"/api/stores/{store_id}/credentials", headers=auth).json()
    assert creds["password"] == "Wgr^3czxSs$U*F*@"


def test_password_is_removed_from_database_after_secret_exists(client, auth, k8s):
    launch(client, auth, admin_password="Sup3rSecret!pw")
    with SessionLocal() as db:
        assert db.query(Store).one().admin_password_enc is None


def test_password_is_encrypted_at_rest_before_provisioning(client, auth, monkeypatch):
    from app.services import executor

    monkeypatch.setattr(executor, "submit", lambda fn, *args: None)
    launch(client, auth, admin_password="Sup3rSecret!pw")
    with SessionLocal() as db:
        stored = db.query(Store).one().admin_password_enc
    assert stored and "Sup3rSecret" not in stored


def test_password_never_appears_in_api_responses(client, auth, k8s):
    password = "Sup3rSecret!pw"
    created = launch(client, auth, admin_password=password)
    store_id = created.json()["id"]
    bodies = [
        created.text,
        client.get("/api/stores", headers=auth).text,
        client.get(f"/api/stores/{store_id}", headers=auth).text,
        client.get(f"/api/stores/{store_id}/events", headers=auth).text,
        client.get("/api/stores/usage", headers=auth).text,
    ]
    assert all(password not in body for body in bodies)
    assert "credentials" not in client.get("/api/stores", headers=auth).text.lower()


def test_validation_errors_do_not_echo_the_password(client, auth, k8s):
    response = launch(client, auth, name="x", admin_password="Sup3rSecret!pw")
    assert response.status_code == 422
    assert "Sup3rSecret" not in response.text


@pytest.mark.parametrize("password", ["short", "has space in it", "x" * 65])
def test_invalid_admin_passwords_are_rejected(client, auth, k8s, password):
    assert launch(client, auth, admin_password=password).status_code == 422


def test_password_is_generated_when_omitted(client, auth, k8s):
    launch(client, auth)
    secret = k8s.objects[("Secret", "store-demo", m.ADMIN_SECRET)]
    assert len(secret["stringData"]["ADMIN_PASSWORD"]) >= 16


def test_products_are_passed_to_the_store_config(client, auth, k8s):
    launch(client, auth, products=PRODUCTS)
    data = config_map(k8s, m.SCRIPTS_CONFIG)["data"]
    assert json.loads(data["products.json"]) == [
        {"name": "Classic T-Shirt", "price": "499", "description": "A classic cotton t-shirt"},
        {"name": "Denim Jeans", "price": "1299", "description": "Blue denim jeans"},
        {"name": "Sneakers", "price": "2499", "description": "Comfortable running shoes"},
    ]
    assert data["create-products.php"].startswith("<?php")


def test_product_text_is_data_not_shell(client, auth, k8s):
    nasty = "Tee'; rm -rf / #\"$(id)"
    launch(client, auth, products=[{"name": nasty, "price": 10, "description": "`x`"}])
    data = config_map(k8s, m.SCRIPTS_CONFIG)["data"]
    assert json.loads(data["products.json"])[0]["name"] == nasty
    assert nasty not in m.INIT_SCRIPT


@pytest.mark.parametrize(
    "product",
    [
        {"name": "", "price": 1},
        {"name": "x", "price": -1},
        {"name": "x", "price": "abc"},
        {"name": "x", "price": 1.234},
        {"name": "x" * 101, "price": 1},
        {"name": "x", "price": 1, "description": "d" * 501},
    ],
)
def test_invalid_products_are_rejected(client, auth, k8s, product):
    assert launch(client, auth, products=[product]).status_code == 422


def test_at_most_twenty_products(client, auth, k8s):
    many = [{"name": f"p{i}", "price": 1} for i in range(21)]
    assert launch(client, auth, products=many).status_code == 422


def test_no_products_means_none_unless_sample_flag(client, auth, k8s, monkeypatch):
    launch(client, auth, "plain")
    assert json.loads(config_map(k8s, m.SCRIPTS_CONFIG, "store-plain")["data"]["products.json"]) == []

    monkeypatch.setattr(get_settings(), "sample_products", True)
    launch(client, auth, "seeded")
    seeded = json.loads(config_map(k8s, m.SCRIPTS_CONFIG, "store-seeded")["data"]["products.json"])
    assert len(seeded) == 3


def test_store_out_exposes_card_fields(client, auth, k8s):
    launch(client, auth, storage_gi=2)
    store = client.get("/api/stores", headers=auth).json()[0]
    assert store["owner"] == "owner"
    assert store["storage_gi"] == 2
    assert store["namespace"] == "store-demo"
    assert store["created_at"].endswith(("Z", "+00:00"))


def test_multiple_stores_are_isolated_in_their_own_namespaces(client, auth, k8s):
    launch(client, auth, "alpha", storage_gi=1, admin_password="AlphaPass123!")
    launch(client, auth, "bravo", storage_gi=2, admin_password="BravoPass123!")
    assert {"store-alpha", "store-bravo"} <= set(k8s.namespaces)
    alpha = k8s.objects[("Secret", "store-alpha", m.ADMIN_SECRET)]["stringData"]["ADMIN_PASSWORD"]
    bravo = k8s.objects[("Secret", "store-bravo", m.ADMIN_SECRET)]["stringData"]["ADMIN_PASSWORD"]
    assert (alpha, bravo) == ("AlphaPass123!", "BravoPass123!")
    pvc = lambda ns: k8s.objects[("PersistentVolumeClaim", ns, m.WORDPRESS_PVC)]["spec"]["resources"]["requests"]["storage"]
    assert (pvc("store-alpha"), pvc("store-bravo")) == ("1Gi", "2Gi")


def test_undecryptable_password_fails_with_clear_error(client, auth, k8s, monkeypatch):
    from app.services import executor

    monkeypatch.setattr(executor, "submit", lambda fn, *args: None)
    store_id = launch(client, auth, admin_password="Sup3rSecret!pw").json()["id"]
    with SessionLocal() as db:
        db.get(Store, store_id).admin_password_enc = "not-a-valid-token"
        db.commit()
    from app.services import provisioner

    provisioner.provision_store(store_id)
    store = client.get(f"/api/stores/{store_id}", headers=auth).json()
    assert store["status"] == StoreStatus.FAILED
    assert "can no longer be decrypted" in store["error"]


class TestRazorpay:
    def test_live_keys_are_refused(self):
        with pytest.raises(ValidationError):
            Settings(razorpay_key_id="rzp_live_abc123", razorpay_key_secret="s")

    def test_disabled_without_credentials(self):
        assert Settings().razorpay_enabled is False
        assert Settings(razorpay_key_id="rzp_test_abc").razorpay_enabled is False

    def test_secret_is_hidden_from_repr(self):
        settings = Settings(razorpay_key_id="rzp_test_abc", razorpay_key_secret="topsecret")
        assert "topsecret" not in repr(settings)

    def test_store_without_credentials_keeps_cod_only(self, client, auth, k8s):
        launch(client, auth)
        assert ("Secret", "store-demo", m.RAZORPAY_SECRET) not in k8s.objects
        assert client.get("/api/stores/usage", headers=auth).json()["online_payments"] is False
        data = config_map(k8s, m.CONFIG_MAP)["data"]
        assert data["STORE_CURRENCY"] == get_settings().store_currency

    def test_store_with_test_credentials_gets_secret_and_inr(self, client, auth, k8s, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_abc123")
        monkeypatch.setattr(settings, "razorpay_key_secret", Settings(razorpay_key_secret="shh").razorpay_key_secret)
        monkeypatch.setattr(settings, "store_currency", "USD")
        launch(client, auth)
        secret = k8s.objects[("Secret", "store-demo", m.RAZORPAY_SECRET)]["stringData"]
        assert secret == {"RAZORPAY_KEY_ID": "rzp_test_abc123", "RAZORPAY_KEY_SECRET": "shh"}
        assert config_map(k8s, m.CONFIG_MAP)["data"]["STORE_CURRENCY"] == "INR"
        assert client.get("/api/stores/usage", headers=auth).json()["online_payments"] is True

    def test_razorpay_secret_never_leaks_through_apis(self, client, auth, k8s, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_abc123")
        monkeypatch.setattr(settings, "razorpay_key_secret", Settings(razorpay_key_secret="shh-very-secret").razorpay_key_secret)
        store_id = launch(client, auth).json()["id"]
        for path in ("", f"/{store_id}", f"/{store_id}/events", "/usage"):
            assert "shh-very-secret" not in client.get(f"/api/stores{path}", headers=auth).text

    def test_job_reads_razorpay_credentials_as_optional_secret_refs(self):
        env = m.init_job(make_spec())["spec"]["template"]["spec"]["containers"][0]["env"]
        refs = {v["name"]: v["valueFrom"]["secretKeyRef"] for v in env if v["name"].startswith("RAZORPAY")}
        assert set(refs) == {"RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"}
        assert all(ref["optional"] and ref["name"] == m.RAZORPAY_SECRET for ref in refs.values())

    def test_no_credentials_are_baked_into_the_init_script(self):
        assert not re.search(r"rzp_(test|live)_[A-Za-z0-9]{6,}", m.INIT_SCRIPT + m.PRODUCTS_PHP)
        assert "configure-razorpay.php" in m.INIT_SCRIPT


class TestInitScript:
    def test_checkout_flow_is_configured(self):
        script = m.INIT_SCRIPT
        for expected in (
            "woocommerce_cod_settings",
            "woocommerce_enable_guest_checkout yes",
            "woocommerce_ship_to_countries disabled",
            "[woocommerce_checkout]",
            "[woocommerce_cart]",
            "page_on_front",
            "wp eval-file /config/create-products.php",
        ):
            assert expected in script

    def test_razorpay_failure_cannot_fail_the_job(self):
        assert 'setup_razorpay || echo "Razorpay setup incomplete' in m.INIT_SCRIPT

    @pytest.mark.skipif(shutil.which("sh") is None, reason="sh not available")
    def test_script_is_valid_shell(self):
        result = subprocess.run(["sh", "-n", "-c", m.INIT_SCRIPT], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    @pytest.mark.skipif(shutil.which("php") is None, reason="php not available")
    @pytest.mark.parametrize("source", ["PRODUCTS_PHP", "RAZORPAY_PHP"])
    def test_php_scripts_are_valid(self, tmp_path, source):
        path = tmp_path / "script.php"
        path.write_text(getattr(m, source))
        result = subprocess.run(["php", "-l", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr


def test_migration_upgrades_a_pre_existing_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255) NOT NULL UNIQUE, password_hash VARCHAR(128) NOT NULL, created_at DATETIME)"))
        conn.execute(text("CREATE TABLE stores (id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, name VARCHAR(30) NOT NULL, namespace VARCHAR(63) NOT NULL, status VARCHAR(16), url VARCHAR(255), error TEXT, created_at DATETIME, updated_at DATETIME)"))
        conn.execute(text("INSERT INTO users (id, email, password_hash) VALUES (1, 'Sam.Lee@example.com', 'x'), (2, 'sam.lee@other.org', 'x')"))
        conn.execute(text("INSERT INTO stores (id, owner_id, name, namespace, status) VALUES (1, 1, 'demo', 'store-demo', 'ready')"))

    migrate(engine)
    migrate(engine)

    with engine.connect() as conn:
        names = [row[0] for row in conn.execute(text("SELECT username FROM users ORDER BY id"))]
        store = conn.execute(text("SELECT storage_gi, products, admin_password_enc FROM stores")).one()
    assert names[0] == "sam_lee" and names[1] != names[0]
    assert store[0] == 1 and json.loads(store[1]) == [] and store[2] is None


@pytest.mark.skipif(shutil.which("php") is None, reason="php not available")
class TestProductsScript:
    def run_script(self, tmp_path, products_json: str):
        script = tmp_path / "create-products.php"
        script.write_text(m.PRODUCTS_PHP)
        data = tmp_path / "products.json"
        data.write_text(products_json)
        harness = __file__.replace("test_launch_spec.py", "products_harness.php")
        result = subprocess.run(
            ["php", harness, str(script)],
            capture_output=True,
            text=True,
            env={"PRODUCTS_FILE": str(data), "PATH": "/usr/bin:/bin"},
        )
        assert result.returncode == 0, result.stderr + result.stdout
        return json.loads(result.stdout)

    def test_creates_each_product_once_even_when_run_twice(self, tmp_path):
        payload = json.dumps([
            {"name": "Classic T-Shirt", "price": "499", "description": "A classic cotton t-shirt"},
            {"name": "Denim Jeans", "price": "1299", "description": "Blue denim jeans"},
        ])
        out = self.run_script(tmp_path, payload)
        assert [p["name"] for p in out["products"]] == ["Classic T-Shirt", "Denim Jeans"]
        assert out["products"][0]["price"] == "499"
        assert out["products"][0]["status"] == "publish"
        assert out["products"][1]["description"] == "Blue denim jeans"
        assert sum(1 for line in out["log"] if line.startswith("log: Created")) == 2
        assert sum(1 for line in out["log"] if "already exists" in line) == 2

    def test_hostile_names_are_stored_verbatim(self, tmp_path):
        name = "Tee'; DROP TABLE x; <script>\"$(id)`"
        out = self.run_script(tmp_path, json.dumps([{"name": name, "price": "1", "description": ""}]))
        assert out["products"][0]["name"] == name

    def test_invalid_json_creates_nothing(self, tmp_path):
        out = self.run_script(tmp_path, "{not json")
        assert out["products"] == []
        assert any("not valid" in line for line in out["log"])

    def test_blank_names_are_skipped(self, tmp_path):
        out = self.run_script(tmp_path, json.dumps([{"name": "  ", "price": "5"}]))
        assert out["products"] == []


RAZORPAY_HARNESS = """<?php
$GLOBALS['options'] = array('woocommerce_razorpay_settings' => array('webhook_secret' => 'keep-me'));
function get_option($k, $d = false) { return $GLOBALS['options'][$k] ?? $d; }
function update_option($k, $v) { $GLOBALS['options'][$k] = $v; }
include $argv[1];
echo json_encode($GLOBALS['options']);
"""


@pytest.mark.skipif(shutil.which("php") is None, reason="php not available")
def test_razorpay_script_enables_gateway_and_keeps_other_settings(tmp_path):
    script = tmp_path / "configure-razorpay.php"
    script.write_text(m.RAZORPAY_PHP)
    harness = tmp_path / "harness.php"
    harness.write_text(RAZORPAY_HARNESS)
    result = subprocess.run(
        ["php", str(harness), str(script)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "RAZORPAY_KEY_ID": "rzp_test_abc123", "RAZORPAY_KEY_SECRET": "s3cret"},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    settings = json.loads(result.stdout)["woocommerce_razorpay_settings"]
    assert settings["enabled"] == "yes"
    assert settings["key_id"] == "rzp_test_abc123"
    assert settings["key_secret"] == "s3cret"
    assert settings["payment_action"] == "capture"
    assert settings["webhook_secret"] == "keep-me"
