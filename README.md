# KubePulse Helm Chart Repository

This is the official Helm chart repository for [KubePulse](https://github.com/dhruvmistry2000/kubepulse) — a lightweight, push-based Kubernetes monitoring dashboard.

---

## Prerequisites

- Helm 3.x installed
- `kubectl` configured and pointing at your target cluster
- A running KubePulse dashboard (see [dashboard setup](#dashboard-setup))

---

## Add the Helm Repository

```bash
helm repo add kubepulse https://dhruvmistry2000.github.io/kubepulse
helm repo update
```

Verify the repo was added:

```bash
helm search repo kubepulse
```

---

## Quick Install

Once your dashboard is running and you have a registration token, install the agent into your cluster:

```bash
helm install kubepulse-agent kubepulse/kubepulse \
  --set registrationToken=<TOKEN_FROM_DASHBOARD> \
  --set dashboardUrl=https://<YOUR_DASHBOARD_URL> \
  --set clusterName=<YOUR_CLUSTER_NAME>
```

---

## Dashboard Setup

The agent needs a running KubePulse dashboard to push data to. The easiest option is Google Cloud Run:

```bash
gcloud run deploy kubepulse-dashboard \
  --image dhruvmistry200/kubepulse-dashboard:latest \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars SECRET_KEY=your-strong-secret \
  --set-env-vars ADMIN_KEY=your-admin-key \
  --port 8080
```

Or run it with Docker on any VM:

```bash
docker run -d \
  --name kubepulse-dashboard \
  -p 8080:8080 \
  -e SECRET_KEY=your-strong-secret \
  -e ADMIN_KEY=your-admin-key \
  dhruvmistry200/kubepulse-dashboard:latest
```

> The dashboard **must** be reachable over HTTPS from your cluster nodes.

---

## Generating a Registration Token

1. Open your dashboard URL in a browser.
2. Click the **➕ Add Cluster** tab.
3. Enter your `ADMIN_KEY`.
4. Enter a display name for the cluster (e.g. `production`, `staging`).
5. Click **Generate Install Token**.
6. Copy the token — it expires in **10 minutes**.

---

## Configuration

All configurable values and their defaults:

| Value | Required | Default | Description |
|---|---|---|---|
| `registrationToken` | ✅ Yes | — | One-time token from the dashboard |
| `dashboardUrl` | ✅ Yes | — | Full HTTPS URL of your dashboard |
| `clusterName` | ✅ Yes | — | Display name for this cluster |
| `image.repository` | No | `dhruvmistry200/kubepulse-agent` | Agent image repository |
| `image.tag` | No | `latest` | Agent image tag |
| `image.pullPolicy` | No | `Always` | Image pull policy |

### Example with all options

```bash
helm install kubepulse-agent kubepulse/kubepulse \
  --set registrationToken=abc123 \
  --set dashboardUrl=https://my-dashboard.run.app \
  --set clusterName=production \
  --set image.repository=dhruvmistry200/kubepulse-agent \
  --set image.tag=latest \
  --set image.pullPolicy=Always
```

Or using a custom `values.yaml` file:

```yaml
# my-values.yaml
registrationToken: abc123
dashboardUrl: https://my-dashboard.run.app
clusterName: production

image:
  repository: dhruvmistry200/kubepulse-agent
  tag: latest
  pullPolicy: Always
```

```bash
helm install kubepulse-agent kubepulse/kubepulse -f my-values.yaml
```

---

## Adding Multiple Clusters

Each cluster needs its own unique `clusterName` and a fresh registration token. Repeat the steps above for each cluster — a single dashboard supports unlimited clusters simultaneously.

---

## Upgrading the Agent

To upgrade without generating a new token:

```bash
helm upgrade kubepulse-agent kubepulse/kubepulse --reuse-values
```

If the registration token has expired, generate a new one from the dashboard and run:

```bash
helm upgrade kubepulse-agent kubepulse/kubepulse \
  --set registrationToken=NEW_TOKEN \
  --reuse-values
```

---

## Uninstalling

```bash
helm uninstall kubepulse-agent
```

To also remove the cluster entry from the dashboard:

```bash
curl -X DELETE https://<YOUR_DASHBOARD_URL>/api/cluster/<CLUSTER_NAME> \
  -H "X-Admin-Key: your-admin-key"
```

Or use the 🗑 button in the dashboard sidebar.

---

## Troubleshooting

**Cluster not appearing in dashboard after install:**
```bash
kubectl logs -f deployment/kubepulse-agent
```
- Confirm `dashboardUrl` is correct and reachable from the cluster.
- Confirm the registration token had not expired when you ran `helm install`.

**Token expired before running helm install:**
Generate a new token from the dashboard and use `helm upgrade` instead of `helm install`.

**Metrics showing `—` instead of values:**
The Kubernetes `metrics-server` must be installed in your cluster. On EKS:
```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```
On GKE and AKS, metrics-server is enabled by default.

---

## Source & Documentation

- **GitHub Repository:** [dhruvmistry2000/kubepulse](https://github.com/dhruvmistry2000/kubepulse)
- **Docker Hub:** `dhruvmistry200/kubepulse-agent:latest`
- **GHCR:** `ghcr.io/dhruvmistry200/kubepulse-agent:latest`