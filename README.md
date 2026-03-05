# kubepulse 🫀
![Kubernetes](https://img.shields.io/badge/Kubernetes-1.24+-326CE5?logo=kubernetes&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![GKE](https://img.shields.io/badge/GKE-supported-4285F4?logo=googlecloud&logoColor=white)
![EKS](https://img.shields.io/badge/EKS-supported-FF9900?logo=amazonaws&logoColor=white)
![AKS](https://img.shields.io/badge/AKS-supported-0078D4?logo=microsoftazure&logoColor=white)
![Helm](https://img.shields.io/badge/Helm-3.x-0F1689?logo=helm&logoColor=white)

---


kubepulse is a lightweight, self-hosted Kubernetes monitoring dashboard.  
You install a small agent Helm chart into any cluster, and it securely pushes live data to a
central dashboard over HTTPS. No inbound ports, no LoadBalancers, and no firewall changes are
required on your clusters.

### What is kubepulse?

kubepulse provides a simple, push-based way to monitor one or many Kubernetes clusters from a
single web UI.

**Key highlights:**

- **Push-based architecture**: Agent → dashboard (no inbound access to your clusters).
- **Multi-cluster support**: View and switch between many clusters from one dashboard.
- **Managed Kubernetes support**: Works on GKE, EKS, and AKS.
- **One-time registration tokens**: Simple, time-limited bootstrap flow per cluster.
- **Real-time metrics**: Pod metrics, CPU/memory usage, deployment health.
- **Namespace-aware analytics**: Per-namespace graphs and filters.
- **System namespace filtering**: Hides `kube-system` and similar namespaces by default.
- **Modern UI**: Dark theme, expandable rows, and cross-linking between resources.

---

## Architecture

The dashboard follows a push-based architecture: agents inside your clusters periodically send
data to a central dashboard, which stores the latest state in memory and serves it to browsers.

```
  ┌──────────────────────────────────────────────────┐
  │  Browser                                         │
  │  polls every 10–15s                              │
  └──────────────────┬───────────────────────────────┘
                     │ HTTPS
                     ▼
  ┌──────────────────────────────────────────────────┐
  │  Dashboard (Cloud Run / any HTTPS host)         │
  │  - Serves the UI                                │
  │  - Stores latest cluster data in memory         │
  │  - Issues one-time registration tokens          │
  └──────────────────▲───────────────────────────────┘
                     │ POST /api/heartbeat every 30s
                     │ outbound HTTPS only
  ┌──────────────────┴──────────────────────────────┐
  │  Agent (Helm chart, runs inside your cluster)   │
  │  - Reads K8s API via read-only ServiceAccount   │
  │  - Collects nodes, pods, deployments, services  │
  │  - Pushes data to dashboard every 30s           │
  │  - Supports GKE / EKS / AKS                     │
  └─────────────────────────────────────────────────┘
```

---

## Project Structure

```
kubepulse/
├── dashboard/
│   ├── app.py              # Flask dashboard backend
│   ├── Dockerfile          # Dashboard container
│   └── templates/
│       └── index.html      # Single-page UI
├── agent/
│   ├── app.py              # Flask agent (runs in cluster)
│   └── Dockerfile          # Agent container
└── helm/
    ├── Chart.yaml
    ├── values.yaml
    └── templates/
        ├── deployment.yaml
        ├── serviceaccount.yaml
        ├── clusterrole.yaml
        ├── clusterrolebinding.yaml
        ├── secret.yaml
        └── service.yaml
```

---

## Prerequisites

- **Docker**
- **kubectl** configured and pointing at the target cluster
- **Helm 3.x** installed
- **Container registry** (any of):
  - Docker Hub (easiest, free)
  - Google Artifact Registry
  - Amazon ECR
  - Azure ACR
  - Any private registry
- **Dashboard hosting with HTTPS**:
  - Google Cloud Run (recommended; has a free tier)
  - Any VM or server behind nginx + SSL
  - `ngrok` for local testing

---

## Deployment Guide

### Step 1 – Build and push the images

Replace `YOUR_REGISTRY` with your actual registry prefix.

**Examples:**

- **Docker Hub**: `yourname/kubepulse-dashboard`
- **Google Artifact Registry**:
  `us-central1-docker.pkg.dev/PROJECT/REPO/kubepulse-dashboard`
- **Amazon ECR**:
  `123456789.dkr.ecr.us-east-1.amazonaws.com/kubepulse-dashboard`
- **Azure ACR**: `yourregistry.azurecr.io/kubepulse-dashboard`

**Build and push dashboard:**

```bash
docker build -t YOUR_REGISTRY/kubepulse-dashboard:v1 ./dashboard
docker push YOUR_REGISTRY/kubepulse-dashboard:v1
```

**Build and push agent:**

```bash
docker build -t YOUR_REGISTRY/kubepulse-agent:v1 ./agent
docker push YOUR_REGISTRY/kubepulse-agent:v1
```

**Registry login commands (reference):**

```bash
# Docker Hub
docker login
```

```bash
# Google Artifact Registry
gcloud auth configure-docker us-central1-docker.pkg.dev
```

```bash
# Amazon ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS \
  --password-stdin YOUR_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com
```

```bash
# Azure ACR
az acr login --name YOUR_REGISTRY_NAME
```

---

### Step 2 – Deploy the dashboard

Pick one option depending on where you want to host the dashboard.  
The dashboard URL must be reachable over HTTPS by your cluster nodes.

#### Option A – Google Cloud Run (recommended)

```bash
gcloud run deploy kubepulse-dashboard \
  --image YOUR_REGISTRY/kubepulse-dashboard:v1 \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars SECRET_KEY=change-me-to-a-strong-secret \
  --set-env-vars ADMIN_KEY=change-me-to-admin-key \
  --port 8080
```

Cloud Run provides an HTTPS URL immediately.  
Note this URL; you will use it in Step 3.

#### Option B – Docker on any VM

```bash
docker run -d \
  --name kubepulse-dashboard \
  -p 8080:8080 \
  -e SECRET_KEY=change-me-to-a-strong-secret \
  -e ADMIN_KEY=change-me-to-admin-key \
  YOUR_REGISTRY/kubepulse-dashboard:v1
```

Place this container behind nginx (or similar) with SSL/TLS termination.  
The dashboard **must** be accessible over HTTPS for agents to reach it.

#### Option C – Inside Kubernetes

```bash
kubectl create deployment kubepulse-dashboard \
  --image=YOUR_REGISTRY/kubepulse-dashboard:v1
```

```bash
kubectl set env deployment/kubepulse-dashboard \
  SECRET_KEY=change-me-to-a-strong-secret \
  ADMIN_KEY=change-me-to-admin-key
```

```bash
kubectl expose deployment kubepulse-dashboard \
  --port=8080 --type=LoadBalancer
```

---

### Step 3 – Add your first cluster

1. Open your dashboard URL in a browser.
2. Click the **➕ Add Cluster** tab.
3. Enter the `ADMIN_KEY` you set in Step 2.
4. Enter a name for your cluster (for example, `production`, `staging`, `dev`).
5. Click **Generate Install Token**.
6. Copy the generated Helm command and run it against your cluster:

```bash
helm install kubepulse-agent ./helm \
  --set image.repository=YOUR_REGISTRY/kubepulse-agent \
  --set image.tag=v1 \
  --set registrationToken=PASTE_TOKEN_FROM_DASHBOARD \
  --set dashboardUrl=https://YOUR_DASHBOARD_URL \
  --set clusterName=production
```

7. Within a few seconds the cluster should appear in the sidebar ✅

> ⚠️ **Token expiry:**  
> The token expires in 10 minutes. If it expires, generate a new one in the
> dashboard and use `helm upgrade` instead of `helm install`.

---

### Step 4 – Add more clusters

Repeat **Step 3** for each additional cluster.

Each cluster needs:

- **Unique `clusterName`**
- **Fresh registration token** from the dashboard
- **Access to the agent image** in your registry

A single dashboard can handle many clusters simultaneously.

---

### Upgrading the agent

```bash
# Build and push new version
docker build -t YOUR_REGISTRY/kubepulse-agent:v2 ./agent
docker push YOUR_REGISTRY/kubepulse-agent:v2
```

```bash
# Upgrade without needing a new registration token
helm upgrade kubepulse-agent ./helm \
  --set image.repository=YOUR_REGISTRY/kubepulse-agent \
  --set image.tag=v2 \
  --reuse-values
```

---

### Uninstalling

```bash
# Remove agent from a cluster
helm uninstall kubepulse-agent
```

```bash
# Remove a cluster from the dashboard via API
curl -X DELETE https://YOUR_DASHBOARD_URL/api/cluster/production \
  -H "X-Admin-Key: your-admin-key"
```

You can also delete a cluster using the 🗑 button in the dashboard sidebar.

---

## Environment Variables

### Dashboard

| Variable    | Required | Default      | Description                         |
|------------|----------|--------------|-------------------------------------|
| SECRET_KEY | Yes      | —            | Master key for agent auth           |
| ADMIN_KEY  | No       | = SECRET_KEY | Key used to generate tokens         |
| PORT       | No       | 8080         | Port the dashboard listens on       |

### Agent (Helm values)

| Helm value        | Required | Description                             |
|-------------------|----------|-----------------------------------------|
| image.repository  | Yes      | Agent image repository                  |
| image.tag         | Yes      | Agent image tag                         |
| registrationToken | Yes      | One-time token generated by dashboard   |
| dashboardUrl      | Yes      | Full HTTPS URL of your dashboard        |
| clusterName       | Yes      | Display name for this cluster           |

---

## Cloud-Specific Notes

### GKE

- **Outbound-only:** Agent only needs outbound internet (port 443).
- **Private clusters:** If org policy blocks external IPs, use private clusters with
  master authorized networks; the agent is unaffected.
- **Images:** Google Artifact Registry is recommended for image storage.

### EKS

- **Networking:** Ensure node groups have outbound internet (NAT Gateway or public subnets).
- **Images:** Amazon ECR is recommended for image storage.
- **Permissions:** Agent ServiceAccount needs no AWS IAM permissions; it only uses
  read-only Kubernetes API access via a ClusterRole.

### AKS

- **Networking:** Works with both kubenet and Azure CNI.
- **Images:** Azure Container Registry (ACR) is recommended.
- **Private clusters:** Supported; only outbound HTTPS is required.

---

## How It Works

### Registration (one-time per cluster)

1. Admin generates a one-time token (10 minute expiry) from the dashboard.
2. The token is embedded in the `helm install` command.
3. The agent starts and calls `POST /api/register` with the token.
4. The dashboard validates the token, marks it as used, and returns the `SECRET_KEY`.
5. The agent stores `SECRET_KEY` in memory only (never written to disk).

### Heartbeat (every 30 seconds)

1. The agent queries the Kubernetes API from inside the cluster.
2. The agent posts the collected data to `POST /api/heartbeat`.
3. The dashboard stores the latest data per cluster in memory.
4. The browser polls the dashboard every 10–15 seconds and updates the UI.

### Security

| Layer              | Protects           | Used by            |
|--------------------|-------------------|--------------------|
| ADMIN_KEY          | Token generation  | Admin browser only |
| REGISTRATION_TOKEN | `POST /register`  | Agent (one-time)   |
| SECRET_KEY         | `POST /heartbeat` | Agent (permanent)  |

---

## Dashboard Features

| Feature                    | Description                                     |
|----------------------------|-------------------------------------------------|
| Multi-cluster sidebar      | Switch between clusters; collapsible navigation |
| Graphs tab                 | CPU, memory, and pod count by namespace         |
| System namespace filter    | One-click hide for `kube-system` and friends    |
| Namespace and label filter | Filter all tables and graphs consistently       |
| Expandable rows            | Drill into full resource details                |
| Cross-linking              | Navigate Pod → Node → Deployment                |
| CPU / Memory bars          | Live usage vs request vs limit per pod and node |
| Pod events                 | Last 5 Kubernetes events per pod                |
| SPA updates                | Single-page app with silent background refresh  |

---

## Troubleshooting

### Agent pod is Running but cluster is not visible

- **Check logs:**

  ```bash
  kubectl logs -f deployment/kubepulse-agent
  ```

- **Verify `dashboardUrl`:** Confirm it is correct and reachable from the cluster.
- **Confirm token validity:** Ensure the registration token has not expired (10 minute limit).
- **Dashboard config:** Verify the `SECRET_KEY` environment variable is set correctly.

### Token expired before running `helm install`

- Open the **Add Cluster** tab and generate a new token.
- Use `helm upgrade` instead of `helm install`:

  ```bash
  helm upgrade kubepulse-agent ./helm \
    --set registrationToken=NEW_TOKEN \
    --reuse-values
  ```

### Metrics show `—` instead of values

- The Kubernetes `metrics-server` must be installed in the cluster.
- **GKE:** Metrics server is enabled by default.
- **EKS:** Install metrics server:

  ```bash
  kubectl apply -f \
    https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
  ```

- **AKS:** Metrics server is enabled by default.

### Dashboard shows a stale data banner

- The agent has not sent a heartbeat in 90+ seconds.
- Confirm the agent pod is running and has outbound internet access.
- Check dashboard logs for heartbeat or authentication errors.

---

## Contributing

Pull requests are welcome.  
For significant changes, please open an issue first to discuss what you would like to change.

