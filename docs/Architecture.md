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
                     │ HTTPS (GET /api/...)
                     ▼
  ┌─────────────────────────────────────────────────┐
  │  Dashboard  (Cloud Run / VM / Kubernetes)       │
  │  ─────────────────────────────────────────────  │
  │  • Serves the single-page web UI               │
  │  • Persistent cluster registry (file or GCS)   │
  │  • Live metrics in memory; history on disk/GCS │
  │  • Computes node cost from agent resource data │
  │  • Issues one-time registration tokens         │
  └──────────────────▲──────────────────────────────┘
                     │ POST /api/heartbeat every 30s
                     │ (outbound HTTPS only)
  ┌──────────────────┴──────────────────────────────┐
  │  Agent  (Helm chart, runs inside your cluster)  │
  │  ─────────────────────────────────────────────  │
  │  • Reads K8s API via read-only ClusterRole     │
  │  • Collects nodes, pods, metrics, provider info│
  │  • Persists per-cluster credential (Secret)    │
  │  • Pushes resource data only (no cost math)    │
  └─────────────────────────────────────────────────┘
```

---

## Components

### Dashboard (`dashboard/`)

| File | Role |
|---|---|
| `dashboard/app.py` | Flask API: registration, heartbeats, UI APIs |
| `dashboard/storage.py` | Persistent cluster registry (`file` / `gcs`) |
| `dashboard/history.py` | Scaling timelines + retired-node history |
| `dashboard/pricing.py` | Cost estimation from node resource data |
| `dashboard/prices.json` | Static price map + per-provider usage rates |
| `dashboard/templates/index.html` | SPA: sidebar, graphs, nodes, cost, scaling |

Live metrics stay in memory for fast UI refresh. The **registry** and **history** are durable so dashboard restarts do not force re-registration.

### Agent (`agent/`)

| File | Role |
|---|---|
| `agent/app.py` | Collect cluster data and push heartbeats |
| `agent/persistence.py` | Credential load/save (secret / file / memory) |
| `agent/provider.py` | Cloud provider detection from Node objects |

The agent is intentionally lightweight: it does **not** look up prices or compute cost. Cost is attached on the dashboard at heartbeat time.

### Helm Chart (`helm/`)

| File | Role |
|---|---|
| `helm/values.yaml` | Defaults (image, persistence, etc.) |
| `helm/templates/deployment.yaml` | Agent Deployment |
| `helm/templates/role.yaml` / `rolebinding.yaml` | Namespaced Secret access for credential persistence |
| `helm/templates/clusterrole.yaml` | Read-only cluster API access |
| `helm/templates/pvc.yaml` | Optional PVC for file persistence mode |

---

## Data Flow

### Registration (one-time per cluster)

```
Admin browser  →  POST /api/generate-token  →  Dashboard
                  (ADMIN_KEY required)

Dashboard      →  Returns one-time token (10-min TTL)

helm install   →  Embeds token in Kubernetes Secret (first install)

Agent starts   →  Load stored credential if present
               →  Else POST /api/register (token) → Dashboard
Dashboard      →  Returns per-cluster credential
Agent          →  Persists credential (default: Secret kubepulse-agent-state)
```

### Heartbeat (every 30 seconds)

```
Agent  →  Queries Kubernetes API
       →  Collects: nodes (incl. provider/usage), pods, deployments, …
       →  POST /api/heartbeat (per-cluster credential) → Dashboard
Dashboard  →  Prices nodes, stores live data + history
Browser    →  Polls /api/cluster/... and /api/cost every 10–15s
```

### Upgrade without re-registration

```
helm upgrade … --reuse-values
Agent  →  Loads credential from Secret → skips register → heartbeats resume
```

---

## Security Model

| Layer | Credential | Used by | Purpose |
|---|---|---|---|
| Admin | `ADMIN_KEY` | Admin browser | Generate registration tokens |
| Bootstrap | `REGISTRATION_TOKEN` | Agent (one-time) | Initial registration |
| Bootstrap | `SECRET_KEY` | Dashboard | Admin/bootstrap (not sent to agents after register) |
| Operational | `cluster_credential` | Agent (persisted) | Authenticate every heartbeat |

- Tokens are single-use and expire in 10 minutes.
- Credentials are stored **hashed (SHA-256)** in the dashboard registry.
- The agent only requires **outbound** HTTPS (port 443).
- ClusterRole is read-only; a namespaced Role allows reading/writing the credential Secret only.

---

## Cost estimation

Cost runs on the **dashboard** using:
1. Static `prices.json` (instance types + usage fallback rates)
2. Optional public Azure/AWS list-price APIs (no cloud account keys)
3. Usage model (default): live CPU/RAM × `$/vcpu-hr` / `$/GB-hr`

Agents only send capacity, usage, provider, and instance type.

---

## Repository Structure

```
kubepulse/
├── .github/workflows/      # CI: lint, unit tests, image build checks
├── agent/
│   ├── app.py
│   ├── persistence.py
│   ├── provider.py
│   └── Dockerfile
├── dashboard/
│   ├── app.py
│   ├── storage.py
│   ├── history.py
│   ├── pricing.py
│   ├── prices.json
│   ├── templates/index.html
│   └── Dockerfile
├── helm/
├── tests/
├── docs/
└── README.md
```
