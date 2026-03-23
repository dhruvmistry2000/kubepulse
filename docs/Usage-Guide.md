
# Usage Guide

This page covers the KubePulse dashboard UI, REST API endpoints, and common operational workflows.

---

## Dashboard UI

Open your dashboard URL in a browser. The interface is a dark-themed single-page application that refreshes silently every 10–15 seconds.

### Cluster Sidebar

- Lists all registered clusters.
- Click a cluster name to switch to it.
- A green indicator means the agent is actively sending heartbeats.
- A stale/grey indicator means no heartbeat received in the last 90+ seconds.
- Use the 🗑 icon to remove a cluster from the dashboard.

### Overview Tab

Displays a summary of the selected cluster:
- Node count and status (Ready / NotReady)
- Total pod count and phase breakdown (Running, Pending, Failed)
- Deployment health summary

### Graphs Tab

- CPU usage per namespace (as a bar or line chart)
- Memory usage per namespace
- Pod count over time per namespace
- Filter by namespace using the namespace dropdown

### Resources Tables

Each resource type (Pods, Deployments, Nodes, Services) has its own table with:
- **Expandable rows** — click any row to see full resource details
- **Cross-linking** — navigate from a Pod to its Node or Deployment
- **CPU / Memory bars** — visual usage vs request vs limit per pod and node
- **Pod events** — last 5 Kubernetes events per pod
- **System namespace filter** — toggle to show/hide `kube-system` and similar namespaces
- **Label filter** — filter resources by Kubernetes labels

---

## REST API

The dashboard exposes a simple HTTP API consumed by both agents and the browser.

### Agent Endpoints

#### Register a new cluster
```
POST /api/register
Content-Type: application/json

{
  "token": "<registration_token>",
  "cluster_name": "production"
}
```

**Response:**
```json
{
  "secret_key": "<secret_key>"
}
```

#### Send a heartbeat
```
POST /api/heartbeat
Content-Type: application/json
X-Secret-Key: <secret_key>

{
  "cluster_name": "production",
  "nodes": [...],
  "pods": [...],
  "deployments": [...],
  "services": [...]
}
```

### Admin Endpoints

#### Generate a registration token
```
POST /api/generate-token
Content-Type: application/json

{
  "admin_key": "<admin_key>",
  "cluster_name": "production"
}
```

#### Delete a cluster
```
DELETE /api/cluster/<cluster_name>
X-Admin-Key: <admin_key>
```

Example:
```bash
curl -X DELETE https://YOUR_DASHBOARD_URL/api/cluster/production \
  -H "X-Admin-Key: your-admin-key"
```

### Browser Endpoints

```
GET /api/data                   # All cluster data (latest heartbeat state)
GET /                           # Serves the single-page UI
```

---

## Common Workflows

### Add a new cluster

1. Open dashboard → **Add Cluster** tab.
2. Enter `ADMIN_KEY` and a cluster name.
3. Click **Generate Install Token**.
4. Run the Helm command against your target cluster.

### Remove a cluster

Option 1 — via the UI: Click the 🗑 icon next to the cluster name in the sidebar.

Option 2 — via the API:
```bash
curl -X DELETE https://YOUR_DASHBOARD_URL/api/cluster/staging \
  -H "X-Admin-Key: your-admin-key"
```

### Upgrade the agent

```bash
# Reuse existing values, pull latest image
helm upgrade kubepulse-agent ./helm --reuse-values
```

### Uninstall the agent

```bash
helm uninstall kubepulse-agent
```

### Re-register after token expiry

If the token expired before running `helm install`:

1. Generate a new token in the dashboard.
2. Use `helm upgrade` (not `helm install`):

```bash
helm upgrade kubepulse-agent ./helm \
  --set registrationToken=NEW_TOKEN \
  --reuse-values
```

### Filter by namespace

Use the namespace dropdown in the dashboard to scope all graphs and resource tables to a single namespace. The system namespace filter toggle hides `kube-system`, `kube-public`, and similar namespaces.