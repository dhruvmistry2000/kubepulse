
## PAGE: Getting Started

# Getting Started

This guide walks you through deploying the KubePulse dashboard and connecting your first Kubernetes cluster in under 10 minutes.

---

## Prerequisites

Before you begin, ensure you have the following installed and configured:

| Tool | Version | Notes |
|---|---|---|
| `kubectl` | Any recent | Configured and pointing at your target cluster |
| `helm` | 3.x | Required to install the agent chart |
| `docker` | Any recent | Only needed if building custom images |
| A container registry | — | Docker Hub, GCR, ECR, ACR, or any registry (only for custom builds) |
| An HTTPS host for the dashboard | — | Google Cloud Run, a VM with nginx+SSL, or `ngrok` for local testing |

> **No build step required for standard use.** Pre-built images are available:
> - `dhruvmistry200/kubepulse-dashboard:latest`
> - `dhruvmistry200/kubepulse-agent:latest`

---

## Step 1 — Deploy the Dashboard

Choose one of the following hosting options. The dashboard **must** be accessible over HTTPS because agents will push data to it from inside your cluster.

### Option A — Google Cloud Run (Recommended)

Cloud Run is the easiest option: it provides an HTTPS URL instantly with a generous free tier.

```bash
gcloud run deploy kubepulse-dashboard \
  --image dhruvmistry200/kubepulse-dashboard:latest \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars SECRET_KEY=change-me-to-a-strong-secret \
  --set-env-vars ADMIN_KEY=change-me-to-admin-key \
  --port 8080
```

Note the Cloud Run URL printed at the end (e.g., `https://kubepulse-dashboard-xxxxx.run.app`).

### Option B — Docker on Any VM

```bash
docker run -d \
  --name kubepulse-dashboard \
  -p 8080:8080 \
  -e SECRET_KEY=change-me-to-a-strong-secret \
  -e ADMIN_KEY=change-me-to-admin-key \
  dhruvmistry200/kubepulse-dashboard:latest
```

Place this behind an nginx reverse proxy with a valid TLS certificate. The agent will refuse to connect over plain HTTP.

### Option C — Inside Kubernetes

```bash
kubectl create deployment kubepulse-dashboard \
  --image=dhruvmistry200/kubepulse-dashboard:latest

kubectl set env deployment/kubepulse-dashboard \
  SECRET_KEY=change-me-to-a-strong-secret \
  ADMIN_KEY=change-me-to-admin-key

kubectl expose deployment kubepulse-dashboard \
  --port=8080 --type=LoadBalancer
```

Attach a TLS certificate via your cloud provider's load balancer or an ingress controller.

---

## Step 2 — Generate a Registration Token

1. Open your dashboard URL in a browser.
2. Navigate to the **➕ Add Cluster** tab.
3. Enter the `ADMIN_KEY` you set during deployment.
4. Enter a display name for your cluster (e.g., `production`, `staging`, `dev`).
5. Click **Generate Install Token**.
6. Copy the Helm command that is displayed.

> Tokens expire after **10 minutes**. Have your `helm install` ready before generating.

---

## Step 3 — Install the Agent

Run the Helm command you copied in Step 2 against your cluster:

```bash
helm install kubepulse-agent ./helm \
  --set registrationToken=PASTE_TOKEN_FROM_DASHBOARD \
  --set dashboardUrl=https://YOUR_DASHBOARD_URL \
  --set clusterName=production
```

Within a few seconds, your cluster should appear in the dashboard sidebar with a green indicator. ✅

---

## Step 4 — Add More Clusters (Optional)

Repeat Steps 2–3 for each additional cluster. Each cluster requires:
- A unique `clusterName`
- A fresh registration token
- The Helm chart installed in that cluster

---

## First Run Verification

After installing the agent, verify it is healthy:

```bash
# Check agent pod status
kubectl get pods -l app=kubepulse-agent

# Stream agent logs
kubectl logs -f deployment/kubepulse-agent
```

You should see log lines like:

```
Registered with dashboard successfully.
Heartbeat sent. Status: 200
```

Open your dashboard URL in a browser. The new cluster should appear in the left-hand sidebar.