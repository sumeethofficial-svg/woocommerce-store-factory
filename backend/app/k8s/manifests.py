import json
import secrets
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings

MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
STORE_ID_LABEL = "store-factory/store-id"
COMPONENT_LABEL = "app.kubernetes.io/component"
MANAGER = "store-factory"

MYSQL_SECRET = "mysql-credentials"
ADMIN_SECRET = "wordpress-admin"
CONFIG_MAP = "store-config"
MYSQL = "mysql"
WORDPRESS = "wordpress"
WORDPRESS_PVC = "wordpress-data"
INIT_JOB = "wordpress-init"
QUOTA = "store-quota"
LIMIT_RANGE = "store-limits"
SCRIPTS_CONFIG = "store-scripts"
RAZORPAY_SECRET = "razorpay-credentials"

WEB_UID = 33

INIT_SCRIPT = r"""
set -eu
cd /var/www/html
export HOME=/tmp WP_CLI_CACHE_DIR=/tmp/wp-cli-cache

set_page_content() {
  page_id=$(wp option get "$1" 2>/dev/null || echo -1)
  if [ "$page_id" -gt 0 ] 2>/dev/null; then
    wp post update "$page_id" --post_content="$2"
  else
    echo "WooCommerce page $1 not found, skipped"
  fi
}

setup_razorpay() {
  if [ -z "${RAZORPAY_KEY_ID:-}" ] || [ -z "${RAZORPAY_KEY_SECRET:-}" ]; then
    echo "Razorpay skipped: no test credentials supplied"
    return 0
  fi
  case "$RAZORPAY_KEY_ID" in
    rzp_test_*) ;;
    *) echo "Razorpay skipped: only rzp_test_ keys are allowed"; return 0 ;;
  esac
  wp plugin is-installed woocommerce-razorpay || wp plugin install woocommerce-razorpay || return 1
  wp plugin is-active woocommerce-razorpay || wp plugin activate woocommerce-razorpay || return 1
  wp option update woocommerce_currency INR || return 1
  wp eval-file /config/configure-razorpay.php || return 1
}

if ! wp core is-installed; then
  wp core install --url="$SITE_URL" --title="$SITE_TITLE" \
    --admin_user="$ADMIN_USER" --admin_password="$ADMIN_PASSWORD" \
    --admin_email="$ADMIN_EMAIL" --skip-email
fi

wp plugin is-installed woocommerce || wp plugin install woocommerce
wp plugin is-active woocommerce || wp plugin activate woocommerce

wp option update woocommerce_currency "$STORE_CURRENCY"
wp option update woocommerce_default_country "$STORE_COUNTRY"
wp option update woocommerce_ship_to_countries disabled
wp option update woocommerce_enable_guest_checkout yes
wp option update woocommerce_cod_settings \
  '{"enabled":"yes","title":"Cash on delivery","description":"Pay with cash upon delivery.","instructions":"Pay with cash upon delivery.","enable_for_methods":[],"enable_for_virtual":"yes"}' \
  --format=json
wp option update woocommerce_onboarding_profile '{"skipped":true}' --format=json

set_page_content woocommerce_cart_page_id '[woocommerce_cart]'
set_page_content woocommerce_checkout_page_id '[woocommerce_checkout]'

shop_id=$(wp option get woocommerce_shop_page_id 2>/dev/null || echo -1)
if [ "$shop_id" -gt 0 ] 2>/dev/null; then
  wp option update show_on_front page
  wp option update page_on_front "$shop_id"
fi

printf 'apache_modules:\n  - mod_rewrite\n' > /tmp/wp-cli.yml
WP_CLI_CONFIG_PATH=/tmp/wp-cli.yml wp rewrite structure '/%postname%/' --hard \
  || echo "Permalink setup skipped"

wp eval-file /config/create-products.php

setup_razorpay || echo "Razorpay setup incomplete, Cash on Delivery remains available"
"""

PRODUCTS_PHP = r"""<?php
$items = json_decode(file_get_contents(getenv('PRODUCTS_FILE') ?: '/config/products.json'), true);
if (!is_array($items)) {
    WP_CLI::warning('products.json is not valid, no products created');
    return;
}
foreach ($items as $item) {
    $name = trim((string) ($item['name'] ?? ''));
    if ($name === '') {
        continue;
    }
    $key = sha1($name);
    $existing = get_posts(array(
        'post_type' => 'product',
        'post_status' => 'any',
        'meta_key' => '_store_factory_key',
        'meta_value' => $key,
        'fields' => 'ids',
        'numberposts' => 1,
    ));
    if ($existing) {
        WP_CLI::log('Product already exists: ' . $name);
        continue;
    }
    $product = new WC_Product_Simple();
    $product->set_name($name);
    $product->set_status('publish');
    $product->set_catalog_visibility('visible');
    $product->set_regular_price((string) ($item['price'] ?? '0'));
    $product->set_description((string) ($item['description'] ?? ''));
    $product->set_short_description((string) ($item['description'] ?? ''));
    $product->update_meta_data('_store_factory_key', $key);
    $id = $product->save();
    WP_CLI::log('Created product #' . $id . ': ' . $name);
}
"""

RAZORPAY_PHP = r"""<?php
$settings = get_option('woocommerce_razorpay_settings', array());
if (!is_array($settings)) {
    $settings = array();
}
$settings = array_merge($settings, array(
    'enabled' => 'yes',
    'title' => 'Pay online (UPI, cards, netbanking)',
    'description' => 'Secure test-mode payment through Razorpay.',
    'key_id' => getenv('RAZORPAY_KEY_ID'),
    'key_secret' => getenv('RAZORPAY_KEY_SECRET'),
    'payment_action' => 'capture',
));
update_option('woocommerce_razorpay_settings', $settings);
"""

SAMPLE_PRODUCTS = [
    {"name": "Classic T-Shirt", "price": "499", "description": "A classic cotton t-shirt"},
    {"name": "Denim Jeans", "price": "1299", "description": "Blue denim jeans"},
    {"name": "Sneakers", "price": "2499", "description": "Comfortable running shoes"},
]


@dataclass(frozen=True)
class StoreSpec:
    store_id: int
    name: str
    namespace: str
    site_url: str
    host: str
    admin_email: str
    settings: Settings
    storage_gi: int = 1
    admin_password: str | None = None
    products: list[dict[str, str]] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.name.replace("-", " ").title()


def labels(spec: StoreSpec, component: str | None = None) -> dict[str, str]:
    result = {
        "app.kubernetes.io/name": "woocommerce-store",
        "app.kubernetes.io/instance": spec.name,
        MANAGED_BY_LABEL: MANAGER,
    }
    if component:
        result[COMPONENT_LABEL] = component
    return result


def _meta(spec: StoreSpec, name: str, component: str | None = None) -> dict[str, Any]:
    return {"name": name, "namespace": spec.namespace, "labels": labels(spec, component)}


def _secret_ref(secret: str, key: str, optional: bool = False) -> dict[str, Any]:
    ref: dict[str, Any] = {"name": secret, "key": key}
    if optional:
        ref["optional"] = True
    return {"secretKeyRef": ref}


def namespace(spec: StoreSpec) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": spec.namespace,
            "labels": {**labels(spec), STORE_ID_LABEL: str(spec.store_id)},
        },
    }


def resource_quota(spec: StoreSpec) -> dict[str, Any]:
    total_storage = spec.storage_gi + spec.settings.mysql_storage_gi
    return {
        "apiVersion": "v1",
        "kind": "ResourceQuota",
        "metadata": _meta(spec, QUOTA),
        "spec": {
            "hard": {
                "pods": "10",
                "persistentvolumeclaims": "4",
                "requests.storage": f"{total_storage}Gi",
                "requests.cpu": "2",
                "requests.memory": "2Gi",
                "limits.cpu": "4",
                "limits.memory": "4Gi",
            }
        },
    }


def limit_range(spec: StoreSpec) -> dict[str, Any]:
    largest_volume = max(spec.storage_gi, spec.settings.mysql_storage_gi)
    return {
        "apiVersion": "v1",
        "kind": "LimitRange",
        "metadata": _meta(spec, LIMIT_RANGE),
        "spec": {
            "limits": [
                {
                    "type": "Container",
                    "default": {"cpu": "500m", "memory": "512Mi"},
                    "defaultRequest": {"cpu": "50m", "memory": "128Mi"},
                },
                {
                    "type": "PersistentVolumeClaim",
                    "min": {"storage": "1Gi"},
                    "max": {"storage": f"{largest_volume}Gi"},
                },
            ]
        },
    }


def mysql_secret(spec: StoreSpec) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": _meta(spec, MYSQL_SECRET),
        "type": "Opaque",
        "stringData": {
            "MYSQL_ROOT_PASSWORD": secrets.token_urlsafe(24),
            "MYSQL_DATABASE": "wordpress",
            "MYSQL_USER": "wordpress",
            "MYSQL_PASSWORD": secrets.token_urlsafe(24),
        },
    }


def admin_secret(spec: StoreSpec) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": _meta(spec, ADMIN_SECRET),
        "type": "Opaque",
        "stringData": {
            "ADMIN_USER": "admin",
            "ADMIN_PASSWORD": spec.admin_password or secrets.token_urlsafe(18),
            "ADMIN_EMAIL": spec.admin_email,
        },
    }


def razorpay_secret(spec: StoreSpec) -> dict[str, Any]:
    key_secret = spec.settings.razorpay_key_secret
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": _meta(spec, RAZORPAY_SECRET),
        "type": "Opaque",
        "stringData": {
            "RAZORPAY_KEY_ID": spec.settings.razorpay_key_id or "",
            "RAZORPAY_KEY_SECRET": key_secret.get_secret_value() if key_secret else "",
        },
    }


def store_config(spec: StoreSpec) -> dict[str, Any]:
    s = spec.settings
    config_extra = (
        f"define('WP_HOME','{spec.site_url}');"
        f"define('WP_SITEURL','{spec.site_url}');"
        "define('FS_METHOD','direct');"
        "define('WP_MEMORY_LIMIT','256M');"
    )
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": _meta(spec, CONFIG_MAP),
        "data": {
            "SITE_URL": spec.site_url,
            "SITE_TITLE": spec.title,
            "STORE_CURRENCY": "INR" if s.razorpay_enabled else s.store_currency,
            "STORE_COUNTRY": s.store_country,
            "WORDPRESS_CONFIG_EXTRA": config_extra,
        },
    }


def scripts_config(spec: StoreSpec) -> dict[str, Any]:
    products = spec.products or (SAMPLE_PRODUCTS if spec.settings.sample_products else [])
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": _meta(spec, SCRIPTS_CONFIG),
        "data": {
            "products.json": json.dumps(products),
            "create-products.php": PRODUCTS_PHP,
            "configure-razorpay.php": RAZORPAY_PHP,
        },
    }


def mysql_service(spec: StoreSpec) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _meta(spec, MYSQL, MYSQL),
        "spec": {
            "clusterIP": "None",
            "selector": {COMPONENT_LABEL: MYSQL},
            "ports": [{"name": "mysql", "port": 3306, "targetPort": 3306}],
        },
    }


def mysql_statefulset(spec: StoreSpec) -> dict[str, Any]:
    s = spec.settings
    claim: dict[str, Any] = {
        "accessModes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": f"{s.mysql_storage_gi}Gi"}},
    }
    if s.storage_class:
        claim["storageClassName"] = s.storage_class
    ping = 'mysqladmin ping -h 127.0.0.1 -uroot -p"$MYSQL_ROOT_PASSWORD" --silent'
    return {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": _meta(spec, MYSQL, MYSQL),
        "spec": {
            "serviceName": MYSQL,
            "replicas": 1,
            "selector": {"matchLabels": {COMPONENT_LABEL: MYSQL}},
            "template": {
                "metadata": {"labels": labels(spec, MYSQL)},
                "spec": {
                    "containers": [
                        {
                            "name": MYSQL,
                            "image": s.mysql_image,
                            "args": [
                                "--character-set-server=utf8mb4",
                                "--performance_schema=OFF",
                            ],
                            "envFrom": [{"secretRef": {"name": MYSQL_SECRET}}],
                            "ports": [{"name": "mysql", "containerPort": 3306}],
                            "volumeMounts": [
                                {"name": "data", "mountPath": "/var/lib/mysql", "subPath": "data"}
                            ],
                            "readinessProbe": {
                                "exec": {"command": ["sh", "-c", ping]},
                                "initialDelaySeconds": 10,
                                "periodSeconds": 5,
                                "timeoutSeconds": 5,
                                "failureThreshold": 30,
                            },
                            "livenessProbe": {
                                "exec": {"command": ["sh", "-c", ping]},
                                "initialDelaySeconds": 60,
                                "periodSeconds": 20,
                                "timeoutSeconds": 5,
                                "failureThreshold": 6,
                            },
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "384Mi"},
                                "limits": {"cpu": "1", "memory": "768Mi"},
                            },
                        }
                    ]
                },
            },
            "volumeClaimTemplates": [
                {"metadata": {"name": "data", "labels": labels(spec, MYSQL)}, "spec": claim}
            ],
        },
    }


def wordpress_pvc(spec: StoreSpec) -> dict[str, Any]:
    s = spec.settings
    body: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": _meta(spec, WORDPRESS_PVC, WORDPRESS),
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": f"{spec.storage_gi}Gi"}},
        },
    }
    if s.storage_class:
        body["spec"]["storageClassName"] = s.storage_class
    return body


def wordpress_env() -> list[dict[str, Any]]:
    return [
        {"name": "WORDPRESS_DB_HOST", "value": f"{MYSQL}:3306"},
        {"name": "WORDPRESS_DB_NAME", "valueFrom": _secret_ref(MYSQL_SECRET, "MYSQL_DATABASE")},
        {"name": "WORDPRESS_DB_USER", "valueFrom": _secret_ref(MYSQL_SECRET, "MYSQL_USER")},
        {"name": "WORDPRESS_DB_PASSWORD", "valueFrom": _secret_ref(MYSQL_SECRET, "MYSQL_PASSWORD")},
    ]


def wordpress_deployment(spec: StoreSpec) -> dict[str, Any]:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": _meta(spec, WORDPRESS, WORDPRESS),
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": {COMPONENT_LABEL: WORDPRESS}},
            "template": {
                "metadata": {"labels": labels(spec, WORDPRESS)},
                "spec": {
                    "containers": [
                        {
                            "name": WORDPRESS,
                            "image": spec.settings.wordpress_image,
                            "env": wordpress_env(),
                            "envFrom": [{"configMapRef": {"name": CONFIG_MAP}}],
                            "ports": [{"name": "http", "containerPort": 80}],
                            "volumeMounts": [{"name": "content", "mountPath": "/var/www/html"}],
                            "readinessProbe": {
                                "httpGet": {"path": "/wp-login.php", "port": "http"},
                                "initialDelaySeconds": 10,
                                "periodSeconds": 5,
                                "timeoutSeconds": 5,
                                "failureThreshold": 30,
                            },
                            "livenessProbe": {
                                "tcpSocket": {"port": "http"},
                                "initialDelaySeconds": 60,
                                "periodSeconds": 20,
                                "failureThreshold": 6,
                            },
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "256Mi"},
                                "limits": {"cpu": "1", "memory": "512Mi"},
                            },
                        }
                    ],
                    "volumes": [
                        {
                            "name": "content",
                            "persistentVolumeClaim": {"claimName": WORDPRESS_PVC},
                        }
                    ],
                },
            },
        },
    }


def wordpress_service(spec: StoreSpec) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _meta(spec, WORDPRESS, WORDPRESS),
        "spec": {
            "type": "ClusterIP",
            "selector": {COMPONENT_LABEL: WORDPRESS},
            "ports": [{"name": "http", "port": 80, "targetPort": "http"}],
        },
    }


def wordpress_ingress(spec: StoreSpec) -> dict[str, Any]:
    meta = _meta(spec, WORDPRESS, WORDPRESS)
    meta["annotations"] = {"nginx.ingress.kubernetes.io/proxy-body-size": "64m"}
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": meta,
        "spec": {
            "ingressClassName": spec.settings.ingress_class,
            "rules": [
                {
                    "host": spec.host,
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {"service": {"name": WORDPRESS, "port": {"number": 80}}},
                            }
                        ]
                    },
                }
            ],
        },
    }


def init_job(spec: StoreSpec) -> dict[str, Any]:
    env = wordpress_env() + [
        {"name": "ADMIN_USER", "valueFrom": _secret_ref(ADMIN_SECRET, "ADMIN_USER")},
        {"name": "ADMIN_PASSWORD", "valueFrom": _secret_ref(ADMIN_SECRET, "ADMIN_PASSWORD")},
        {"name": "ADMIN_EMAIL", "valueFrom": _secret_ref(ADMIN_SECRET, "ADMIN_EMAIL")},
        {
            "name": "RAZORPAY_KEY_ID",
            "valueFrom": _secret_ref(RAZORPAY_SECRET, "RAZORPAY_KEY_ID", optional=True),
        },
        {
            "name": "RAZORPAY_KEY_SECRET",
            "valueFrom": _secret_ref(RAZORPAY_SECRET, "RAZORPAY_KEY_SECRET", optional=True),
        },
    ]
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": _meta(spec, INIT_JOB, "wp-cli"),
        "spec": {
            "backoffLimit": 3,
            "activeDeadlineSeconds": spec.settings.init_timeout,
            "template": {
                "metadata": {"labels": labels(spec, "wp-cli")},
                "spec": {
                    "restartPolicy": "Never",
                    "securityContext": {"runAsUser": WEB_UID, "runAsGroup": WEB_UID},
                    "affinity": {
                        "podAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": [
                                {
                                    "labelSelector": {"matchLabels": {COMPONENT_LABEL: WORDPRESS}},
                                    "topologyKey": "kubernetes.io/hostname",
                                }
                            ]
                        }
                    },
                    "containers": [
                        {
                            "name": "wp-cli",
                            "image": spec.settings.wpcli_image,
                            "command": ["sh", "-c", INIT_SCRIPT],
                            "env": env,
                            "envFrom": [{"configMapRef": {"name": CONFIG_MAP}}],
                            "volumeMounts": [
                                {"name": "content", "mountPath": "/var/www/html"},
                                {"name": "config", "mountPath": "/config", "readOnly": True},
                            ],
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "256Mi"},
                                "limits": {"cpu": "1", "memory": "512Mi"},
                            },
                        }
                    ],
                    "volumes": [
                        {
                            "name": "content",
                            "persistentVolumeClaim": {"claimName": WORDPRESS_PVC},
                        },
                        {"name": "config", "configMap": {"name": SCRIPTS_CONFIG}},
                    ],
                },
            },
        },
    }
