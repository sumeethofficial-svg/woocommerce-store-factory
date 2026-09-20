import re

from app.config import Settings
from app.k8s import manifests as m

DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


def make_spec(**overrides) -> m.StoreSpec:
    return m.StoreSpec(
        store_id=7,
        name="demo",
        namespace="store-demo",
        site_url="http://demo.localhost",
        host="demo.localhost",
        admin_email="owner@example.com",
        settings=Settings(**overrides),
    )


def all_manifests(spec):
    return [
        m.namespace(spec),
        m.resource_quota(spec),
        m.limit_range(spec),
        m.mysql_secret(spec),
        m.admin_secret(spec),
        m.razorpay_secret(spec),
        m.store_config(spec),
        m.scripts_config(spec),
        m.mysql_service(spec),
        m.mysql_statefulset(spec),
        m.wordpress_pvc(spec),
        m.wordpress_deployment(spec),
        m.wordpress_service(spec),
        m.wordpress_ingress(spec),
        m.init_job(spec),
    ]


def test_resource_names_are_valid_dns_labels():
    for manifest in all_manifests(make_spec()):
        assert DNS_LABEL.match(manifest["metadata"]["name"]), manifest["metadata"]["name"]


def test_namespaced_resources_target_store_namespace():
    for manifest in all_manifests(make_spec())[1:]:
        assert manifest["metadata"]["namespace"] == "store-demo"


def test_namespace_carries_ownership_labels():
    labels = m.namespace(make_spec())["metadata"]["labels"]
    assert labels[m.MANAGED_BY_LABEL] == m.MANAGER
    assert labels[m.STORE_ID_LABEL] == "7"


def test_workloads_define_requests_and_limits_and_probes():
    spec = make_spec()
    containers = [
        m.mysql_statefulset(spec)["spec"]["template"]["spec"]["containers"][0],
        m.wordpress_deployment(spec)["spec"]["template"]["spec"]["containers"][0],
        m.init_job(spec)["spec"]["template"]["spec"]["containers"][0],
    ]
    for container in containers:
        assert {"requests", "limits"} <= container["resources"].keys()
    assert "readinessProbe" in containers[0]
    assert "readinessProbe" in containers[1]


def test_secrets_never_appear_in_config_map_or_job_env_values():
    spec = make_spec()
    config = m.store_config(spec)["data"]
    assert not any("PASSWORD" in key for key in config)
    for var in m.init_job(spec)["spec"]["template"]["spec"]["containers"][0]["env"]:
        if "PASSWORD" in var["name"]:
            assert "valueFrom" in var and "value" not in var


def test_generated_passwords_are_unique():
    spec = make_spec()
    first = m.mysql_secret(spec)["stringData"]["MYSQL_PASSWORD"]
    second = m.mysql_secret(spec)["stringData"]["MYSQL_PASSWORD"]
    assert first != second


def test_ingress_uses_store_host_and_class():
    ingress = m.wordpress_ingress(make_spec(ingress_class="traefik"))
    assert ingress["spec"]["rules"][0]["host"] == "demo.localhost"
    assert ingress["spec"]["ingressClassName"] == "traefik"


def test_storage_class_only_set_when_configured():
    without = m.wordpress_pvc(make_spec())
    assert "storageClassName" not in without["spec"]
    with_class = m.wordpress_pvc(make_spec(storage_class="fast"))
    assert with_class["spec"]["storageClassName"] == "fast"


def test_init_job_shares_wordpress_volume_on_same_node():
    pod = m.init_job(make_spec())["spec"]["template"]["spec"]
    assert pod["volumes"][0]["persistentVolumeClaim"]["claimName"] == m.WORDPRESS_PVC
    affinity = pod["affinity"]["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][0]
    assert affinity["labelSelector"]["matchLabels"][m.COMPONENT_LABEL] == m.WORDPRESS
