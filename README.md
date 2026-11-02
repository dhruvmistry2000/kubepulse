# kubepulse 🫀
![Kubernetes](https://img.shields.io/badge/Kubernetes-1.24+-326CE5?logo=kubernetes&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![GKE](https://img.shields.io/badge/GKE-supported-4285F4?logo=googlecloud&logoColor=white)
![EKS](https://img.shields.io/badge/EKS-supported-FF9900?logo=amazonaws&logoColor=white)
![AKS](https://img.shields.io/badge/AKS-supported-0078D4?logo=microsoftazure&logoColor=white)
![Helm](https://img.shields.io/badge/Helm-3.x-0F1689?logo=helm&logoColor=white)

> **Quick Start**: Pre-built images are available on Docker Hub and GitHub Container Registry (GHCR).  
> You can use them directly without building anything:  
> - Docker Hub — Dashboard: `dhruvmistry200/kubepulse-dashboard:latest`  
> - Docker Hub — Agent: `dhruvmistry200/kubepulse-agent:latest`  
> - GHCR — Dashboard: `ghcr.io/dhruvmistry200/kubepulse-dashboard:latest`  
> - GHCR — Agent: `ghcr.io/dhruvmistry200/kubepulse-agent:latest`  
>
> Want to build your own? See [Building Custom Images](#building-custom-images) below.

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

## Container Images

Images are available on both Docker Hub and GitHub Container Registry.

### Docker Hub

```bash
docker pull dhruvmistry200/kubepulse-dashboard:latest
docker pull dhruvmistry200/kubepulse-agent:latest
```

### GitHub Container Registry (ghcr.io)

```bash
docker pull ghcr.io/dhruvmistry200/kubepulse-dashboard:latest
docker pull ghcr.io/dhruvmistry200/kubepulse-agent:latest
```

Use whichever registry is closer to your infrastructure:

- Docker Hub: best for general use and Docker Desktop
- GHCR: best for GitHub Actions pipelines and GitHub-hosted runners
  (no rate limits when pulling from GHCR inside GitHub Actions)

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
├── helm/
│   ├── Chart.yaml
│   ├── values.yaml
│   └── templates/
│       ├── deployment.yaml
│       ├── serviceaccount.yaml
│       ├── clusterrole.yaml
│       ├── clusterrolebinding.yaml
│       ├── secret.yaml
│       ├── service.yaml
│       └── poddisruptionbudget.yaml
├── compose.yaml            # dashboard via docker compose
├── scripts/deploy-dashboard.sh  # prompts for the dashboard URL and deploys it
├── .env.example
└── terraform/local/        # kind cluster you can apply and destroy
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
- **Terraform >= 1.5** and **kind**, only if you want the local cluster in `terraform/local`

---

## Local cluster

`terraform/local` creates a one-node kind cluster you can delete when you are done. It does not install the agent. From that directory:

```bash
terraform init
terraform apply
export KUBECONFIG="$PWD/kubeconfig"
```

`terraform output dashboard_url_from_pods` is the URL the agent must use for `dashboardUrl`. Stop the cluster with `docker stop kubepulse-control-plane` and start it again with `docker start`. `terraform destroy` removes it. Details are in `terraform/local/README.md`.

---

## Deployment Guide

The deploy script asks for the dashboard URL agents will call, the admin and viewer keys, and where to run the dashboard. From a checkout:

```bash
./scripts/deploy-dashboard.sh
```

The Helm release workflow has a separate job that commits that script to the `gh-pages` branch when the script changes. The chart is published by its own job.

```bash
curl -fsSL -o deploy-dashboard.sh https://dhruvmistry2000.github.io/kubepulse/deploy-dashboard.sh
chmod +x deploy-dashboard.sh
./deploy-dashboard.sh
```

The published copy can start the Docker image or install into Kubernetes. Docker Compose still needs a checkout of this repo, because it builds `dashboard/`.

Press Enter on a key prompt to generate a random key. Generated keys are printed once and saved in a mode `600` env file.

- **Docker Compose** builds `dashboard/` and writes `.env` in the repo. An existing `.env` is left alone unless you pass `--force`.
- **Docker** runs `dhruvmistry200/kubepulse-dashboard:latest`. Keys go in `~/.config/kubepulse/dashboard.env`.
- **Kubernetes** installs one replica and stores the registry on a volume. Keys go in a Secret, and the Deployment reads that Secret.

`DASHBOARD_URL` is the address pods use. The script warns when that URL is a loopback address or plain HTTP on a public host. The agent is installed afterward from the Add Cluster helm command.

Non-interactive:

```bash
DASHBOARD_URL=https://dashboard.example.com \
  ./scripts/deploy-dashboard.sh --yes --target docker
```

`--dry-run` writes the env file and prints the command without starting anything.

The sections below show the same steps as individual commands.  
The dashboard URL must be reachable by your cluster nodes. Use HTTPS anywhere that is not a private lab.

#### Option A – Google Cloud Run (recommended)

```bash
gcloud run deploy kubepulse-dashboard \
  --image dhruvmistry200/kubepulse-dashboard:latest \
  --platform managed \
  --region us-central1 \
  --max-instances=1 \
  --set-env-vars ADMIN_KEY=change-me-to-admin-key \
  --set-env-vars VIEWER_KEY=change-me-to-viewer-key \
  --set-env-vars SECRET_KEY=change-me-to-a-legacy-heartbeat-key \
  --port 8080
```

Cloud Run provides an HTTPS URL immediately.  
Note this URL; you will use it in Step 3. The service must stay reachable by the browser and the agent. API reads require `VIEWER_KEY` or `ADMIN_KEY`; do not rely on an open read API. Keep `--max-instances=1`: registration tokens and live metrics live in memory, and a second instance would miss tokens and overwrite the registry.

#### Option B – Docker Compose

```bash
cp .env.example .env
# edit ADMIN_KEY, VIEWER_KEY, and DASHBOARD_URL
docker compose up -d
```

The UI is at `http://127.0.0.1:8080`. `docker compose up` builds `dashboard/` into `kubepulse-dashboard:local`. Registry and history stay in the `dashboard-data` volume. Keep a single container: a second copy would miss registration tokens and overwrite the registry.

`DASHBOARD_URL` is the address agents use, not the browser address. On the local kind cluster that is the host gateway and published port, for example `http://172.19.0.1:8080`.

The same image can be started without Compose:

```bash
## Using Docker Hub
docker run -d \
  --name kubepulse-dashboard \
  -p 8080:8080 \
  -e ADMIN_KEY=change-me-to-admin-key \
  -e VIEWER_KEY=change-me-to-viewer-key \
  -e SECRET_KEY=change-me-to-a-legacy-heartbeat-key \
  dhruvmistry200/kubepulse-dashboard:latest

## Using GHCR
docker run -d \
  --name kubepulse-dashboard \
  -p 8080:8080 \
  -e ADMIN_KEY=change-me-to-admin-key \
  -e VIEWER_KEY=change-me-to-viewer-key \
  -e SECRET_KEY=change-me-to-a-legacy-heartbeat-key \
  ghcr.io/dhruvmistry200/kubepulse-dashboard:latest
```

Place the container behind nginx (or similar) with SSL/TLS termination.  
The dashboard **must** be accessible over HTTPS for agents to reach it.

#### Option C – Inside Kubernetes

```bash
kubectl create deployment kubepulse-dashboard \
  --image=dhruvmistry200/kubepulse-dashboard:latest
```

```bash
kubectl set env deployment/kubepulse-dashboard \
  ADMIN_KEY=change-me-to-admin-key \
  VIEWER_KEY=change-me-to-viewer-key \
  SECRET_KEY=change-me-to-a-legacy-heartbeat-key
```

```bash
kubectl expose deployment kubepulse-dashboard \
  --port=8080 --type=LoadBalancer
```

---

### Step 3 – Add your first cluster

1. Open your dashboard URL in a browser.
2. Click the **➕ Add Cluster** tab.
3. Enter the access key you set in Step 2 (`VIEWER_KEY` to view, `ADMIN_KEY` to add or delete).
4. Enter a name for your cluster (for example, `production`, `staging`, `dev`).
5. Click **Generate Install Token**.
6. Copy the generated Helm command and run it against your cluster. It installs `k8s-dashboard-agent` from the chart repo published to GitHub Pages (`https://dhruvmistry2000.github.io/kubepulse`). The chart stores the token, dashboard URL, and cluster name in a Secret.

> The agent image defaults to `dhruvmistry200/kubepulse-agent:latest`.  
> No need to set `image.repository` or `image.tag` unless using a custom build.

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
# Upgrade to latest image defaults (no new token needed)
helm upgrade kubepulse-agent ./helm --reuse-values
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
| ADMIN_KEY  | Yes      | — | Required to start. Token generation, cluster deletion, and reads |
| VIEWER_KEY | No       | — | Read-only key for `GET /api/*`. `ADMIN_KEY` can also read |
| SECRET_KEY | No       | — | Legacy heartbeat key only. Not printed. Not accepted as an admin or viewer key |
| PORT       | No       | 8080         | Port the dashboard listens on       |
| STORAGE_BACKEND | No  | `file`       | Cluster registry: `file` or `gcs`   |
| STORAGE_PATH | No     | `/data/kubepulse/registry.json` | Registry file path |
| GCS_BUCKET | For Cloud Run | —        | GCS bucket when `STORAGE_BACKEND=gcs` |
| GCS_OBJECT | No       | `kubepulse/registry.json` | GCS object for registry |
| HISTORY_PATH | No     | `/data/kubepulse/history.json` | Scaling + retired-node history |
| HISTORY_RETENTION_DAYS | No | `30`   | Days to retain history points       |
| PRICE_MAP_PATH | No | bundled `prices.json` | Static price map for cost estimation |
| PRICING_ENABLED | No | `true` | Set `false` to disable cost columns |
| PRICING_MODEL | No | `usage` | `usage` (CPU/RAM) or `instance` (VM list price) |
| PRICING_LIVE_ENABLED | No | `true` | Public list-price APIs (Azure + AWS) |
| PRICING_AZURE_LIVE_ENABLED | No | `true` | Azure Retail Prices API |
| PRICING_AWS_LIVE_ENABLED | No | `true` | AWS public offer index |
| PRICING_PREFER_LIVE | No | `false` | Prefer live API over static map |
| PRICING_REFRESH_INTERVAL | No | `43200` | Seconds between live price cache refreshes |
| PRICE_CACHE_DIR | No | system temp dir | Disk cache for public price lists. Heartbeats never download prices |
| MAX_CONTENT_LENGTH | No | `2097152` | Maximum request body size, in bytes |
| STORE_WRITE_DEBOUNCE_SECONDS | No | `2` | Delay between later registry and history writes. The first write is immediate |
| HELM_REPO_URL | No | `https://dhruvmistry2000.github.io/kubepulse` | Helm repo shown on Add Cluster. Published by the Helm release workflow |
| HELM_CHART_NAME | No | `k8s-dashboard-agent` | Chart name in that repo |

**Cloud Run:** use `STORAGE_BACKEND=gcs` with a GCS bucket — local disk is ephemeral.

### Agent (Helm values)

| Helm value        | Required | Description                             |
|-------------------|----------|-----------------------------------------|
| image.repository  | No       | Agent image repository (has sane default) |
| image.tag         | No       | Agent image tag (defaults to `latest`)  |
| registrationToken | First install only | One-time token. Helm install takes it; the chart stores it in a Secret |
| dashboardUrl      | Yes      | Full HTTPS URL of your dashboard. Stored in that same Secret |
| clusterName       | Yes      | DNS-1123 subdomain. `Production` is stored as `production` |
| existingSecret    | No       | Use an existing Secret instead of creating one from the values above |
| persistence.mode  | No       | `secret` (default), `file`, or `memory` |
| stateSecretName   | No       | Secret name for stored credential (`kubepulse-agent-state`) |
| podDisruptionBudget.enabled | No | Off by default. A single replica with `minAvailable: 1` cannot be drained |

After the first successful registration, `registrationToken` is optional — the agent resumes from its stored credential on `helm upgrade --reuse-values`.

---

## Durable Registration (Part A)

- **Agent** persists a per-cluster credential in a Kubernetes Secret (`persistence.mode=secret`, default). The chart creates that Secret empty and grants `get`/`update`/`patch` on it only. `helm upgrade` keeps stored data. Uninstall keeps the Secret; delete `kubepulse-agent-state` yourself to drop the credential.
- **Dashboard** persists the cluster registry to disk (`file`) or GCS (`gcs`).
- A cluster stays **pending** until its first authenticated heartbeat. If the agent cannot save the credential, it exits. Generate a new token for the same name and install again. The name is reserved only after that first heartbeat.
- Heartbeats authenticate with the per-cluster credential; credentials are stored hashed (SHA-256) at rest.
- Redeploying either side does not require a new registration token once the credential has been saved.
- Clusters registered before per-cluster credentials can still heartbeat with `SECRET_KEY`. That path is deprecated and cannot revive a deleted cluster or finish a pending registration.
- Cluster names are DNS-1123 subdomains (lowercase letters, digits, hyphens, and dots). The dashboard returns the canonical name from `/api/register`, and the agent heartbeats that exact string. A cluster stored before this rule is still found if the heartbeat name matches ignoring case.

---

## Node Monitoring, Cost & History (Part B)

### Per-node metrics
Each node reports capacity, allocatable, live CPU/RAM (from metrics-server), pod count, Ready status, cloud provider, instance type, and region/zone.

### Cloud provider detection
Derived from the Node object — no cloud credentials:
- `gce://` → GCP, `aws://` → AWS, `azure://` → Azure, else `onprem`
- Instance type from `node.kubernetes.io/instance-type`
- Region/zone from `topology.kubernetes.io/region` / `zone`

### Cost estimation (on the dashboard)

Agents stay lightweight: they send **resource data only**. The dashboard attaches `$/hr` and `$/mo` on each heartbeat.

No cloud account keys, IAM roles, or billing API access required.

| Source | Credentials | Description |
|--------|-------------|-------------|
| **Static price map** | None | Bundled `dashboard/prices.json` (override with `PRICE_MAP_PATH`) |
| **Azure public API** | None | Retail Prices API for missing instance types |
| **AWS public index** | None | Public offer JSON from `pricing.us-east-1.amazonaws.com` |
| **Usage model** | None | Live CPU/RAM from metrics-server × `$/vcpu-hr` and `$/GB-hr` in price map |
| **Capacity fallback** | None | Node capacity × provider rates when nothing else matches |

**Pricing is on by default** on the dashboard — no agent configuration required.

**Usage model** (default `PRICING_MODEL=usage`): meters live CPU/RAM × rates in `prices.json` → `fallback`. Tagged `(usage)` in the UI.

**Instance model** (`PRICING_MODEL=instance`): full VM list price from static map + public APIs.

To disable entirely on the dashboard:
```bash
-e PRICING_ENABLED=false
```

Opt out of live APIs (static + usage/capacity only):
```bash
-e PRICING_LIVE_ENABLED=false
```

GCP has no public per-instance API without credentials — use the static map or `PRICING_MODEL=usage` with GCP rates in `fallback.gcp`.

### History (scaling + retired nodes)
The dashboard records coarse node-count timelines and per-node lifetime stats (peak usage, accrued cost). Retired nodes stay in the **Retired Nodes** tab.

> **Caveat:** Built-in history is intentionally simple — fine for tens of clusters at ~5-minute resolution. For long-horizon, high-resolution fleet history, use an external TSDB.

### New UI tabs
- **Nodes** — provider filter, CPU/RAM bars (used/allocatable/capacity), `$/hr` and `$/mo`
- **Cost** — cluster roll-up by provider
- **Scaling** — node count over time
- **Retired Nodes** — lifetime, peak usage, accrued cost

---

## Building Custom Images

If you want to modify kubepulse or host images in your own registry,
follow these steps.

### Where to change the image name

There are 3 places to update when using a custom image:

**1. helm/values.yaml** — change the agent image default:

```yaml
image:
  repository: YOUR_REGISTRY/kubepulse-agent  # ← change this
  tag: latest                                # ← and this
  pullPolicy: Always
```

**2. Dashboard environment variable** — change the default shown
in the Add Cluster helm command:

```bash
# Add this env var when deploying the dashboard
AGENT_IMAGE=YOUR_REGISTRY/kubepulse-agent:latest
```

Example with Cloud Run:

```bash
gcloud run deploy kubepulse-dashboard \
  --image YOUR_REGISTRY/kubepulse-dashboard:latest \
  --max-instances=1 \
  --set-env-vars AGENT_IMAGE=YOUR_REGISTRY/kubepulse-agent:latest \
  --set-env-vars SECRET_KEY=your-secret \
  --set-env-vars ADMIN_KEY=your-admin-key
```

**3. helm install command** — override at install time:

The Add Cluster command already sets `image.repository` and `image.tag` from `AGENT_IMAGE`, and it installs the chart from `HELM_REPO_URL`. Change those `--set` values in the copied command to override one install.

### Build and push steps

```bash
# Login to your registry
docker login   # Docker Hub
# OR
gcloud auth configure-docker us-central1-docker.pkg.dev   # GCR
# OR
aws ecr get-login-password --region us-east-1 | docker login ...  # ECR

# Build dashboard
docker build -t YOUR_REGISTRY/kubepulse-dashboard:latest ./dashboard
docker push YOUR_REGISTRY/kubepulse-dashboard:latest

# Build agent
docker build -t YOUR_REGISTRY/kubepulse-agent:latest ./agent
docker push YOUR_REGISTRY/kubepulse-agent:latest
```

### GitHub Actions (automated builds)

This repo includes GitHub Actions workflows that lint every branch.
A merge to `main` publishes the dashboard image when `dashboard/` changes,
and the agent image when `agent/` changes. The Helm chart is published to GitHub Pages when `helm/` changes.
See `.github/workflows/` for details.

To use them with your own registry:
1. Fork this repo
2. Go to Settings → Secrets and variables → Actions
3. Add these secrets:
   - `DOCKERHUB_USERNAME` — your Docker Hub username
   - `DOCKERHUB_TOKEN` — Docker Hub access token
4. Update the image names in `.github/workflows/dashboard.yml`
   and `.github/workflows/agent.yml`

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
3. The agent calls `POST /api/register` with the token.
4. The dashboard validates the token and returns a **per-cluster credential**.
5. The agent persists the credential (default: Kubernetes Secret `kubepulse-agent-state`).
6. On redeploy, the agent loads the stored credential and **skips registration**.

### Heartbeat (every 30 seconds)

1. The agent queries the Kubernetes API (nodes, pods, metrics, provider labels) and posts resource data to `POST /api/heartbeat`.
2. The dashboard computes per-node cost (usage-based by default) and stores live metrics + scaling/retired-node history.
3. The browser polls every 10–15 seconds and refreshes the UI.

### Upgrade without re-registration

```bash
helm upgrade kubepulse-agent ./helm --reuse-values --set image.tag=NEW_TAG
```

No new `registrationToken` is required when credential persistence is enabled (default).

### Security

| Layer              | Protects           | Used by            |
|--------------------|-------------------|--------------------|
| ADMIN_KEY          | Token generation, deletion, and reads | Admin browser |
| VIEWER_KEY         | `GET /api/*`      | Browser, read-only |
| REGISTRATION_TOKEN | `POST /register`  | Agent (one-time)   |
| SECRET_KEY         | Legacy heartbeats | Already-active clusters only |
| cluster_credential | `POST /heartbeat` | Agent (per-cluster, persisted)  |

---

## Dashboard Features

| Feature                    | Description                                     |
|----------------------------|-------------------------------------------------|
| Multi-cluster sidebar      | Switch between clusters; collapsible navigation |
| Graphs tab                 | CPU, memory, and pod count by namespace         |
| Nodes tab                  | Per-node CPU/RAM, provider, instance type, cost |
| Cost tab                   | Cluster $/hr and $/mo roll-up by provider       |
| Scaling tab                | Node count timeline (coarse history)            |
| Retired Nodes tab          | Lifetime, peak usage, accrued cost              |
| System namespace filter    | One-click hide for `kube-system` and friends    |
| Namespace and label filter | Filter all tables and graphs consistently       |
| Expandable rows            | Drill into full resource details                |
| Cross-linking              | Navigate Pod → Node → Deployment                |
| CPU / Memory bars          | Live usage vs request vs limit per pod and node |
| Pod events                 | Last 5 Kubernetes events per pod                |
| SPA updates                | Single-page app with silent background refresh  |
| Durable registration       | Survives agent/dashboard redeploys without re-registering |

---

## Customizing the price map

Edit `dashboard/prices.json` and rebuild/redeploy the dashboard image, or mount a custom file and set `PRICE_MAP_PATH`:

```bash
-e PRICE_MAP_PATH=/data/kubepulse/prices.json
```

Format:

```json
{
  "prices": {
    "gcp/us-central1/e2-medium": 0.0335,
    "aws/*/m5.large": 0.096
  },
  "fallback": {
    "gcp": { "vcpuPerHour": 0.031611, "gbPerHour": 0.004237 },
    "aws": { "vcpuPerHour": 0.0464, "gbPerHour": 0.005 },
    "azure": { "vcpuPerHour": 0.0416, "gbPerHour": 0.005 },
    "onprem": { "vcpuPerHour": 0.02, "gbPerHour": 0.003 }
  }
}
```

Wildcard region (`aws/*/m5.large`) matches any region. The `fallback` section drives the default **usage** pricing model.

---

## Dashboard on Cloud Run (persistent storage)

Cloud Run has no persistent local disk. Use GCS for the cluster registry and history:

```bash
gcloud run deploy kubepulse-dashboard \
  --image dhruvmistry200/kubepulse-dashboard:latest \
  --max-instances=1 \
  --set-env-vars SECRET_KEY=your-secret \
  --set-env-vars ADMIN_KEY=your-admin-key \
  --set-env-vars DASHBOARD_URL=https://your-service.run.app \
  --set-env-vars STORAGE_BACKEND=gcs \
  --set-env-vars GCS_BUCKET=your-bucket \
  --set-env-vars GCS_OBJECT=kubepulse/registry.json \
  --set-env-vars GCS_HISTORY_OBJECT=kubepulse/history.json
```

The Cloud Run service account needs `roles/storage.objectAdmin` on the bucket.

---

## API reference (new endpoints)

| Endpoint | Description |
|----------|-------------|
| `GET /api/cluster/<name>/cost` | Cluster cost roll-up |
| `GET /api/cost` | Fleet cost across all clusters |
| `GET /api/cluster/<name>/scaling` | Node count timeline |
| `GET /api/scaling` | Fleet scaling timelines |
| `GET /api/retired-nodes` | All retired nodes |
| `GET /api/cluster/<name>/retired-nodes` | Retired nodes for one cluster |

---

## Troubleshooting

### Agent pod is Running but cluster is not visible

- **Check logs:**

  ```bash
  kubectl logs -f deployment/kubepulse-agent
  ```

- **Verify `dashboardUrl`:** Confirm it is correct and reachable from the cluster.
- **First install only:** Ensure the registration token has not expired (10 minute limit).
- **After redeploy:** Check agent logs for `resumed from stored credential` — no new token is needed.
- **Dashboard config:** `ADMIN_KEY` must be set or the process exits. `SECRET_KEY` is only the legacy heartbeat key.

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

