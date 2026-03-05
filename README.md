# K8s Dashboard

A lightweight, read-only Kubernetes monitoring dashboard with two parts:

1. **Dashboard** – runs once (laptop, server, or Cloud Run). Agents from multiple clusters push data to it.
2. **Agent** – runs inside each cluster, registers with the dashboard, and pushes cluster data every 30 seconds.

**Architecture:** The agent connects **to** the dashboard (outbound only). No LoadBalancer, NodePort, or firewall rules needed on the cluster.

---

## Quick start

### Step 1 — Start the dashboard (once, anywhere)

```bash
cd k8s-dashboard/dashboard
pip install -r requirements.txt
SECRET_KEY=my-strong-secret-key python app.py
```

Or on Cloud Run: set the `SECRET_KEY` env var in Cloud Run settings. If you don’t set it, the dashboard generates one at startup and prints it — copy it and set it as an env var to persist across restarts.

### Step 2 — Add a cluster via the dashboard

1. Open your dashboard in the browser.
2. Click the **Add Cluster** tab.
3. Type a name for your cluster (e.g. `production`).
4. Enter the **Admin key** (same as your `SECRET_KEY` or `ADMIN_KEY` env var).
5. Click **Generate Install Token**.
6. Copy the helm command shown and run it on your cluster.
7. The cluster will appear in the sidebar within about 30 seconds.
8. If the token expires before you run the command, click **Generate New Token**.

### Step 3 — Open the dashboard

Open the dashboard URL in your browser. Select a cluster in the sidebar to view its nodes, pods, deployments, services, and more.

---

## Why no LoadBalancer?

The agent **pushes** data to the dashboard. It only needs outbound internet access. No firewall rules, no external IPs, and no NodePorts are needed on the cluster.

---

## What if I restart the dashboard?

If `SECRET_KEY` is set as an env var, the key stays the same across restarts. Agents will automatically re-register on their next heartbeat cycle (within 30 seconds). No manual steps needed. The in-memory cluster store is repopulated as heartbeats arrive.

---

## What if my agent pod restarts?

The agent re-registers automatically using the `REGISTRATION_TOKEN` stored in its Kubernetes Secret. If that token is still valid (within 10 minutes of generation), it works automatically. If the token has expired, generate a new one from the **Add Cluster** tab and run:

```bash
helm upgrade k8s-agent ./helm --set registrationToken=<new-token>
```

---

## Security model

- **ADMIN_KEY** – Protects token generation. Only someone with this key can generate registration tokens (add clusters). If not set, it defaults to `SECRET_KEY`.
- **REGISTRATION_TOKEN** – One-time, 10-minute token. Used only once to join a cluster. Generated in the dashboard **Add Cluster** tab.
- **SECRET_KEY** – Permanent shared key. Agents receive it from the dashboard after successful registration and use it for all heartbeats. It is never shown in the UI or in helm commands.

---

## Build and push the agent image

```bash
cd k8s-dashboard/agent
docker build -t YOUR_REGISTRY/k8s-dashboard-agent:latest .
docker push YOUR_REGISTRY/k8s-dashboard-agent:latest
```

---

## Install the agent on a cluster (Helm)

You don’t set values manually for a first-time install — use the **Add Cluster** tab to generate a token and get the exact `helm install` command with all values pre-filled.

To add a **second** cluster (or reinstall with a new token):

```bash
helm install k8s-agent ./helm \
  --set registrationToken=<token-from-add-cluster-tab> \
  --set dashboardUrl=https://your-dashboard-url.run.app \
  --set clusterName=staging \
  --set image.repository=YOUR_REGISTRY/k8s-agent
```

`SECRET_KEY` in the Kubernetes secret starts empty. The agent receives it from the dashboard after successful registration and holds it in memory. If the agent pod restarts, it re-registers using `REGISTRATION_TOKEN`.

---

## Run the dashboard locally

```bash
cd k8s-dashboard/dashboard
pip install -r requirements.txt
SECRET_KEY=my-secret DASHBOARD_URL=http://localhost:8080 python app.py
```

Then open http://localhost:8080. Use the Add Cluster tab to get the helm command; when testing locally, use a tool like ngrok or your public URL for `DASHBOARD_URL` so the agent in the cluster can reach the dashboard.

---

## Deploy the dashboard to Cloud Run

Use the script in the repo:

```bash
./deploy-cloudrun.sh
```

Set `SECRET_KEY` and `DASHBOARD_URL` in Cloud Run env vars. Use the Add Cluster tab to add clusters.

---

## Optional: metrics-server

For CPU and memory usage on nodes, install [metrics-server](https://github.com/kubernetes-sigs/metrics-server) in the cluster. If it is not available, the agent still works and returns `null` for usage fields.

---

## Project layout

```
k8s-dashboard/
├── agent/       # In-cluster app: registers with dashboard, pushes heartbeat every 30s; /healthz for probes
├── dashboard/   # Central hub: receives registration and heartbeats; serves UI and /api/cluster/*
└── helm/        # Helm chart for the agent (ClusterIP service, no LoadBalancer)
```
# kubepulse
