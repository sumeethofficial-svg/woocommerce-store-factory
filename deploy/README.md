# Deployment

Two separate paths. Nothing here changes the local Kind setup.

| | Local development | Kubernetes | K3s |
| --- | --- | --- | --- |
| Cluster | Kind on Docker Desktop | Any conformant cluster | Single or multi node K3s |
| How the factory runs | `uvicorn` and `npm run dev` on your machine | Helm chart, in the cluster | Helm chart, in the cluster |
| Config | `backend/.env` | `deploy/k8s/values.yaml` | `deploy/k3s/values.yaml` |
| Ingress class | `nginx` (ingress-nginx) | Your controller, `nginx` by default | `traefik` (bundled) |
| Storage class | Kind default (`standard`) | Cluster default | `local-path` (bundled) |
| Store addresses | `<name>.localhost` | `<name>.<your domain>` | `<name>.<node ip>.nip.io` |
| Images | Not needed | Push to a registry | Import into containerd, or a registry |

There is one Helm chart, `helm/store-factory`. The two values files only override what differs.

## Local development: Kind and Docker Desktop

Follow "Run locally" in the root README. The Kind config is `deploy/kind-config.yaml` and is unchanged. The factory runs on your machine and reaches the cluster through your kubeconfig.

## Standard Kubernetes

Requirements:

- An ingress controller and a default StorageClass.
- A wildcard DNS record, for example `*.stores.example.com`, pointing at the ingress. Store addresses are `<name>.stores.example.com`.
- A container registry the cluster can pull from.

```bash
docker build -t registry.example.com/store-factory-backend:0.2.0 backend
docker build -t registry.example.com/store-factory-frontend:0.2.0 frontend
docker push registry.example.com/store-factory-backend:0.2.0
docker push registry.example.com/store-factory-frontend:0.2.0

helm install store-factory helm/store-factory -f deploy/k8s/values.yaml
```

Edit `deploy/k8s/values.yaml` first: registry, `storeBaseDomain`, `ingress.host`, `ingressClass`.

The chart creates a ServiceAccount with a ClusterRole limited to namespaces, the store resources, jobs, ingresses and pod logs. It also creates a persistent volume for SQLite and a generated `SECRET_KEY`. Keep that secret. Losing it invalidates sessions and any password still waiting to be provisioned.

## K3s

K3s already ships Traefik and the `local-path` StorageClass, so the values file only sets those and a wildcard DNS name.

1. Find the node address and put it in `deploy/k3s/values.yaml` in place of `192.168.1.50` (`storeBaseDomain` and `ingress.host`). `nip.io` resolves `anything.<ip>.nip.io` to `<ip>`, so no DNS setup is needed.
2. Build the images and import them into K3s containerd:

```bash
docker build -t store-factory-backend:0.2.0 backend
docker build -t store-factory-frontend:0.2.0 frontend
docker save store-factory-backend:0.2.0 store-factory-frontend:0.2.0 | sudo k3s ctr images import -
```

3. Install:

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
helm install store-factory helm/store-factory -f deploy/k3s/values.yaml
```

The dashboard is at `http://factory.<node ip>.nip.io`.

## Razorpay test keys

Store the test keys in a Secret and reference it. Only `rzp_test_` keys are accepted. The backend refuses to start with a live key.

```bash
kubectl create secret generic razorpay-test \
  --from-literal=RAZORPAY_KEY_ID=rzp_test_xxxxxxxx \
  --from-literal=RAZORPAY_KEY_SECRET=xxxxxxxx

helm upgrade store-factory helm/store-factory -f deploy/k8s/values.yaml \
  --set backend.razorpay.existingSecret=razorpay-test
```

Without the Secret, stores are created with Cash on Delivery only.

## Storage limits

The size a user picks becomes the WordPress PersistentVolumeClaim. Each namespace also gets a ResourceQuota (`requests.storage`) and a LimitRange that cap what can be requested inside it.

Kind and K3s use the `local-path` provisioner, which records the size but does not enforce it on disk. A hard disk cap needs a storage driver that enforces volume size, which most cloud and Longhorn setups do.

## Not covered

TLS. Stores are served over HTTP. Add cert-manager and set TLS on the ingress before exposing this beyond a trusted network.
