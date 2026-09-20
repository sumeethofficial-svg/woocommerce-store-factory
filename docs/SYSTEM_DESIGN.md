# WooCommerce Store Factory — System Design

```text
USER
  |
  v
React/Vite Dashboard
  |
  | REST/JSON
  v
FastAPI + JWT
  |---------------------> SQLite (factory metadata)
  |
  | Kubernetes Python Client
  v
Kubernetes Cluster (Kind locally)
  |
  +-- namespace: store-a
  |     +-- WordPress Deployment
  |     |     +-- WooCommerce
  |     |     +-- WP-CLI initialization
  |     +-- MySQL StatefulSet
  |     +-- MySQL PVC
  |     +-- Services
  |     +-- Secrets/ConfigMaps
  |     +-- optional Ingress
  |
  +-- namespace: store-b
        +-- same isolated store stack
```

## Store creation

```text
POST /api/stores
 -> validate JWT/request
 -> register in SQLite
 -> create namespace
 -> create Secret
 -> create MySQL Service + StatefulSet/PVC
 -> wait for MySQL readiness
 -> create WordPress PVC/config/deployment/service
 -> wait for WordPress readiness
 -> run WP-CLI
 -> install/configure WooCommerce
 -> mark READY
```

## Store deletion

```text
DELETE /api/stores/{id}
 -> validate JWT + ownership
 -> delete namespace
 -> Kubernetes removes namespaced resources
 -> update SQLite metadata
```

## Cloud-native concepts
Containers, Kubernetes orchestration, declarative resources, namespaces, service discovery, StatefulSet, PVC, ConfigMaps, Secrets, probes, resource requests/limits, API-driven infrastructure, idempotency, status polling, Docker, Helm, CI/CD.

## Implementation notes

- WP-CLI runs as a Kubernetes Job, not an exec into the WordPress pod, because the `wordpress` image does not ship WP-CLI. The Job mounts the WordPress PVC and uses pod affinity so it lands on the same node as the WordPress pod. It runs as uid 33 to match `www-data` in the WordPress image.
- Provisioning runs on a worker thread pool. Each step checks the store row first, so a delete request cancels provisioning cleanly.
- Every namespace carries `app.kubernetes.io/managed-by` and `store-factory/store-id` labels. A pre-existing namespace without matching labels is never adopted or deleted.
- Each namespace gets a ResourceQuota. All containers set requests, limits and probes.
- `store_events` records the lifecycle log shown in the dashboard. `POST /api/stores/{id}/retry` re-runs a failed store.
- Launch spec: name, admin password, WordPress storage (Gi) and initial products are stored on the `stores` row. The password is encrypted with a key derived from `SECRET_KEY`, kept only until the Secret exists, then cleared.
- Quotas are checked in the same database transaction that inserts the store, so concurrent requests cannot exceed 3 stores or 5 Gi.
- Products and the Razorpay setup run as PHP files from a per-namespace ConfigMap (`wp eval-file`). Product text is JSON data, never interpolated into shell.
- Razorpay is optional and test-only. Keys reach the Job through an optional Secret reference, and any failure in that step leaves Cash on Delivery working.
