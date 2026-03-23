
# Architecture

This page describes KubePulse's system design, its two core components, and how data flows from your clusters to your browser.

---

## Design Philosophy

KubePulse is built around a **push-based model**. Instead of the dashboard reaching into clusters (which would require network access, credentials, and firewall rules), each cluster runs a lightweight agent that pushes data outward. This design makes KubePulse work in virtually any network topology, including private clusters, clusters behind NAT, and clusters with strict egress policies.

---

## System Architecture Diagram

```
  ┌─────────────────────────────────────────────────┐
  │  Browser                                        │
  │  Polls dashboard every 10–15 seconds            │
  └──────────────────┬──────────────────────────────┘
                     │ HTTPS (GET /api/data)
                     ▼
  ┌─────────────────────────────────────────────────┐
  │  Dashboard  (Cloud Run / VM / Kubernetes)       │
  │  ─────────────────────────────────────────────  │
  │  • Serves the single-page web UI               │
  │  • Stores latest cluster state in memory       │
  │  • Issues one-time registration tokens         │
  │  • Exposes REST API for agents and browser     │
  └──────────────────▲──────────────────────────────┘
                     │ POST /api/heartbeat every 30s
                     │ (outbound HTTPS only)
  ┌──────────────────┴──────────────────────────────┐
  │  Agent  (Helm chart, runs inside your cluster)  │
  │  ─────────────────────────────────────────────  │
  │  • Reads K8s API via read-only ServiceAccount  │
  │  • Collects nodes, pods, deployments, services │
  │  • Pushes data to dashboard every 30 seconds   │
  │  • Supports GKE, EKS, AKS                      │
  └─────────────────────────────────────────────────┘
```

---

## Components

### Dashboard (`dashboard/`)

| File | Role |
|---|---|
| `dashboard/app.py` | Flask application: REST API, token issuance, in-memory state store |
| `dashboard/templates/index.html` | Single-page UI: cluster sidebar, graphs, resource tables |
| `dashboard/Dockerfile` | Container build definition for the dashboard image |

The dashboard is **stateless** except for the in-memory cluster data. It does not use a database. If the dashboard restarts, agents will re-register on their next heartbeat cycle.

### Agent (`agent/`)

| File | Role |
|---|---|
| `agent/app.py` | Flask application: Kubernetes API client, registration, heartbeat sender |
| `agent/Dockerfile` | Container build definition for the agent image |

The agent runs inside your cluster as a `Deployment` with a read-only `ClusterRole`. It never writes anything to the Kubernetes API.

### Helm Chart (`helm/`)

| File | Role |
|---|---|
| `helm/Chart.yaml` | Chart metadata (name, version, description) |
| `helm/values.yaml` | Default configuration values |
| `helm/templates/deployment.yaml` | Agent `Deployment` manifest |
| `helm/templates/serviceaccount.yaml` | Dedicated `ServiceAccount` for the agent |
| `helm/templates/clusterrole.yaml` | Read-only `ClusterRole` for K8s API access |
| `helm/templates/clusterrolebinding.yaml` | Binds `ClusterRole` to agent's `ServiceAccount` |
| `helm/templates/secret.yaml` | Kubernetes `Secret` holding the registration token |
| `helm/templates/service.yaml` | (Optional) `Service` for the agent pod |

---

## Data Flow

### Registration (one-time per cluster)

```
Admin browser  →  POST /api/generate-token  →  Dashboard
                  (ADMIN_KEY required)

Dashboard      →  Returns one-time token (10-min TTL)

helm install   →  Embeds token in Kubernetes Secret

Agent starts   →  POST /api/register (token)   →  Dashboard
Dashboard      →  Validates token, marks as used, returns SECRET_KEY
Agent          →  Stores SECRET_KEY in memory (never on disk)
```

### Heartbeat (every 30 seconds)

```
Agent  →  Queries Kubernetes API (in-cluster, ServiceAccount credentials)
       →  Collects: nodes, pods, deployments, services, resource usage
       →  POST /api/heartbeat (SECRET_KEY in header)  →  Dashboard
Dashboard  →  Stores latest data per cluster name in memory
Browser    →  GET /api/data (every 10–15s)  →  Dashboard
           →  Renders updated UI
```

---

## Security Model

| Layer | Credential | Used by | Purpose |
|---|---|---|---|
| Admin | `ADMIN_KEY` | Admin browser only | Generate registration tokens |
| Bootstrap | `REGISTRATION_TOKEN` | Agent (one-time) | Authenticate initial registration |
| Operational | `SECRET_KEY` | Agent (permanent) | Authenticate every heartbeat |

- Tokens are single-use and expire in 10 minutes.
- `SECRET_KEY` is never written to disk by the agent.
- The agent only requires **outbound** HTTPS (port 443). No inbound ports are opened.
- The Helm chart creates a minimal `ClusterRole` with read-only verbs (`get`, `list`, `watch`).

---

## Repository Structure

```
kubepulse/
├── .github/
│   └── workflows/          # CI/CD: auto-build & push images on main push
├── agent/
│   ├── app.py              # Agent application
│   └── Dockerfile
├── dashboard/
│   ├── app.py              # Dashboard application
│   ├── Dockerfile
│   └── templates/
│       └── index.html      # Single-page UI
├── helm/
│   ├── Chart.yaml
│   ├── values.yaml
│   └── templates/          # Kubernetes manifests
├── deploy.sh               # Quick deploy helper (Docker)
├── deploy-cloudrun.sh      # Cloud Run deploy helper
├── deploy-helm.sh          # Helm install helper
└── README.md
```

