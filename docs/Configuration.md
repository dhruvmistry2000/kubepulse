
# Configuration

KubePulse is configured via environment variables (dashboard) and Helm values (agent).

---

## Dashboard — Environment Variables

Set these when deploying the dashboard container.

| Variable | Required | Default | Description |
|---|---|---|---|
| `SECRET_KEY` | ✅ Yes | — | Bootstrap/admin secret. Used for token generation and legacy auth. Not sent to agents after registration. |
| `ADMIN_KEY` | No | Equals `SECRET_KEY` | Key required to generate cluster registration tokens. |
| `DASHBOARD_URL` | No | — | Public HTTPS URL of the dashboard (used in generated Helm commands). |
| `PORT` | No | `8080` | TCP port the dashboard Flask server listens on. |
| `AGENT_IMAGE` | No | `dhruvmistry200/kubepulse-agent:latest` | Agent image shown in generated `helm install` commands. |
| `STORAGE_BACKEND` | No | `file` | Cluster registry backend: `file` or `gcs`. |
| `STORAGE_PATH` | No | `/data/kubepulse/registry.json` | Registry file path when `STORAGE_BACKEND=file`. |
| `GCS_BUCKET` | When using GCS | — | GCS bucket for registry (and history). |
| `GCS_OBJECT` | No | `kubepulse/registry.json` | GCS object key for cluster registry. |
| `GCS_HISTORY_OBJECT` | No | `kubepulse/history.json` | GCS object key for scaling/retired-node history. |
| `HISTORY_PATH` | No | `/data/kubepulse/history.json` | History file path when `STORAGE_BACKEND=file`. |
| `HISTORY_RETENTION_DAYS` | No | `30` | Days of scaling timeline to retain. |
| `SCALING_INTERVAL_SECONDS` | No | `300` | Minimum interval between scaling history samples. |
| `HEARTBEAT_INTERVAL_SECONDS` | No | `30` | Expected agent heartbeat interval (used for cost accrual). |
| `PRICE_MAP_PATH` | No | bundled `prices.json` | Path to static price map used for cost estimation. |
| `PRICING_ENABLED` | No | `true` | Enable per-node cost estimation on the dashboard. |
| `PRICING_MODEL` | No | `usage` | `usage` (CPU/RAM from agent metrics) or `instance` (VM list price). |
| `PRICING_LIVE_ENABLED` | No | `true` | Master switch for public live price APIs. |
| `PRICING_AZURE_LIVE_ENABLED` | No | `true` | Azure Retail Prices API (public HTTPS). |
| `PRICING_AWS_LIVE_ENABLED` | No | `true` | AWS public offer index (public HTTPS). |
| `PRICING_PREFER_LIVE` | No | `false` | Prefer live API price over static map when both exist. |
| `PRICING_REFRESH_INTERVAL` | No | `43200` | Seconds between live price cache refreshes. |

### Example — Cloud Run with GCS persistence

```bash
gcloud run deploy kubepulse-dashboard \
  --image dhruvmistry200/kubepulse-dashboard:latest \
  --set-env-vars SECRET_KEY=a-very-long-random-string-here \
  --set-env-vars ADMIN_KEY=separate-admin-key \
  --set-env-vars DASHBOARD_URL=https://your-service.run.app \
  --set-env-vars STORAGE_BACKEND=gcs \
  --set-env-vars GCS_BUCKET=your-bucket \
  --set-env-vars GCS_OBJECT=kubepulse/registry.json \
  --set-env-vars GCS_HISTORY_OBJECT=kubepulse/history.json
```

Grant the Cloud Run service account `roles/storage.objectAdmin` on the bucket.

### Example — Docker with local disk

```bash
docker run -d \
  -e SECRET_KEY=a-very-long-random-string-here \
  -e ADMIN_KEY=separate-admin-key \
  -v kubepulse-data:/data/kubepulse \
  -p 8080:8080 \
  dhruvmistry200/kubepulse-dashboard:latest
```

### Example — Disable pricing

```bash
-e PRICING_ENABLED=false
```

### Example — Custom price map

Edit `dashboard/prices.json` and rebuild the image, or mount a file and set `PRICE_MAP_PATH=/path/to/prices.json`.

---

## Agent — Helm Values

Set these via `--set` flags or by editing `helm/values.yaml`.

The agent sends **resource metrics only** (CPU, RAM, provider, instance type). Cost is calculated on the dashboard.

| Helm Value | Required | Default | Description |
|---|---|---|---|
| `registrationToken` | First install | — | One-time token from the dashboard. Optional on upgrade when credential is persisted. |
| `dashboardUrl` | ✅ Yes | — | Full HTTPS URL of your dashboard. |
| `clusterName` | ✅ Yes | — | Display name for this cluster in the sidebar. |
| `image.repository` | No | `dhruvmistry200/kubepulse-agent` | Agent container image repository. |
| `image.tag` | No | `latest` | Agent container image tag. |
| `image.pullPolicy` | No | `Always` | Kubernetes image pull policy. |
| `persistence.mode` | No | `secret` | Credential storage: `secret`, `file`, or `memory`. |
| `persistence.filePath` | No | `/var/lib/kubepulse/credential.json` | Path when `persistence.mode=file`. |
| `persistence.fileVolume.enabled` | No | `false` | Mount a PVC for file persistence. |
| `stateSecretName` | No | `kubepulse-agent-state` | Secret name for credential persistence. |

### Example — Minimal install

```bash
helm install kubepulse-agent ./helm \
  --set registrationToken=TOKEN_FROM_DASHBOARD \
  --set dashboardUrl=https://your-dashboard-url.example.com \
  --set clusterName=production
```

### Example — Upgrade without re-registration

```bash
helm upgrade kubepulse-agent ./helm \
  --reuse-values \
  --set image.tag=v1.2.0
```

---

## Customization Options

### Using a custom container registry

1. Build and push your images:

```bash
docker build -t YOUR_REGISTRY/kubepulse-dashboard:latest ./dashboard
docker push YOUR_REGISTRY/kubepulse-dashboard:latest

docker build -t YOUR_REGISTRY/kubepulse-agent:latest ./agent
docker push YOUR_REGISTRY/kubepulse-agent:latest
```

2. Set `AGENT_IMAGE` on the dashboard so the generated Helm command uses your registry:

```bash
-e AGENT_IMAGE=YOUR_REGISTRY/kubepulse-agent:latest
```

3. Override the image at Helm install time:

```bash
helm install kubepulse-agent ./helm \
  --set image.repository=YOUR_REGISTRY/kubepulse-agent \
  --set image.tag=latest \
  ...
```

### Heartbeat and polling intervals

By default:
- Agent sends a heartbeat every **30 seconds**
- Browser polls the dashboard every **10–15 seconds**
- Data is considered stale after **90 seconds** without a heartbeat

These values are baked into the current release. To change them, modify `agent/app.py` (heartbeat loop) and `dashboard/templates/index.html` (polling interval) and rebuild your images.

---

## GitHub Actions — CI/CD Secrets

If using the bundled GitHub Actions workflows to auto-build images:

| Secret | Description |
|---|---|
| `DOCKERHUB_USERNAME` | Your Docker Hub username |
| `DOCKERHUB_TOKEN` | Docker Hub access token (not your password) |

Add these under **Settings → Secrets and variables → Actions** in your fork.
