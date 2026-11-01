# AGENT.md — kubepulse

Guidance for AI coding agents (and humans) working in this repo. Read it fully before changing code. Section 6 lists known defects; fix those before adding features.

> Status note: the issue list in section 6 comes from a design-level review. Items marked **[verify]** were inferred from the architecture and have not been reproduced against the code. Reproduce first (a failing test or `helm template` output), then fix.

---

## 1. What this project is

**kubepulse** is a self-hosted, push-based Kubernetes monitor.

- A **read-only agent** inside each cluster collects snapshots and POSTs them to a central **Flask dashboard**.
- The browser only talks to the dashboard, never to a cluster.

```
Browser --poll 10-15s--> Dashboard (Flask :8080)
                           | live metrics: in memory (live_data dict)
                           | registry + history: disk or GCS
                           ^
Agent (in-cluster) --POST /api/heartbeat every 30s--+
   read-only ServiceAccount against the Kubernetes API
```

Two images: `dashboard/` and `agent/`. The Helm chart in `helm/` installs **only the agent**. The dashboard is deployed separately (Cloud Run, VM, or a plain Deployment).

## 2. Repo map

| Path | Role |
|---|---|
| `agent/app.py` | Collector and heartbeat sender (~1,300 lines). No pricing. Only HTTP route: `GET /healthz`. |
| `agent/persistence.py` | Stores the per-cluster credential (`secret` default, `file`, `memory`). |
| `agent/provider.py` | Cloud detection from the Node `providerID` (`gce://`, `aws://`, `azure://`, else on-prem). |
| `dashboard/` | Flask app: auth, heartbeat ingest, read APIs. |
| `dashboard/storage.py` | `RegistryStore`: durable registry (file `/data/kubepulse/registry.json` or GCS). |
| `dashboard/history.py` | Node-count timeline and per-node lifetime stats. Retention 30 days. |
| `dashboard/pricing.py` + `prices.json` | Cost models (`usage` default, `instance`). |
| `dashboard/templates/index.html` | Single-file dark UI (~3,800 lines). No build step. |
| `helm/` | `k8s-dashboard-agent` chart (appVersion 1.0). |
| `scripts/deploy-dashboard.sh` | Interactive dashboard deploy (Compose, Docker, or Kubernetes). |
| `tests/` | `test_part_a_*` (registration persistence), `test_part_b.py` (pricing, history, node cost). |

## 3. Commands

```bash
# tests (must pass before any PR)
python -m unittest discover -s tests

# compile check (CI does this)
python -m compileall dashboard agent

# chart must render with default values AND with a minimal override
helm lint helm/<chart-dir>
helm template test helm/<chart-dir> > /dev/null

# image builds (CI checks both)
docker build -t kubepulse-dashboard dashboard/
docker build -t kubepulse-agent agent/
```

Replace `<chart-dir>` with the actual chart directory. Chart rendering is not covered by tests today; add it (see section 7).

## 4. Auth model (do not weaken)

| Secret | Used by | Protects |
|---|---|---|
| `ADMIN_KEY` | Browser, `X-Admin-Key` | Token generation, pending-token listing, cluster deletion |
| One-time registration token | Agent, once, 10-min TTL | `POST /api/register` |
| Per-cluster credential | Agent, `X-Secret-Key` | Every heartbeat |

Flow: admin generates a token on Add Cluster, the agent calls `/api/register`, receives a random credential and stores it. The dashboard stores only the **SHA-256 hash** in `RegistryStore`. A **legacy path** still accepts the global `SECRET_KEY` for clusters registered before per-cluster credentials.

Rules:

1. Never log, return, or persist a raw credential or `ADMIN_KEY` on the dashboard.
2. Compare secrets with `hmac.compare_digest`, never `==`. **[verify]**
3. New endpoints are **authenticated by default**. Adding an unauthenticated route needs an explicit justification in the PR.
4. Treat everything in a heartbeat payload as **untrusted input** (see XSS, section 6).

## 5. Architecture invariants

- **Live data is in memory only** (`live_data`). A restart loses snapshots until the next heartbeat. A cluster is healthy if its last heartbeat is within 90 s. Use the dashboard's clock, not agent timestamps.
- **Registry is durable**, **history is a coarse JSON timeline** (a TSDB is the stated answer for high-resolution history; do not bolt one on silently).
- **Pricing runs on the dashboard** during heartbeat handling. No cloud credentials are involved; keep it that way. GCP has no public per-instance price API, so it uses the static map or usage rates.
- **Agent is read-only**. The ClusterRole is get/list/watch only. The only write is the namespaced Role for the state Secret. Do not add write verbs to the ClusterRole.
- **UI is one file, no frontend build**. Do not introduce a bundler or framework without a design discussion.
- **Single agent replica per cluster.** Two agents would double-post.

## 6. Known issues (fix in this order)

### P0: Install fails

1. **Helm chart does not render.** `templates/poddisruptionbudget.yaml` reads `podDisruptionBudget.minAvailable`, which `values.yaml` never sets, so `helm template` fails with a nil pointer.
   *Fix:* add defaults to `values.yaml` and guard the template with `{{- if .Values.podDisruptionBudget.enabled }}`. Because the agent is a single replica, a PDB with `minAvailable: 1` blocks node drains, so default it to **disabled**.

2. **Agent cannot create its state Secret.** The Role grants `create` with `resourceNames: [kubepulse-agent-state]`, but Kubernetes never authorizes `create` against `resourceNames` (the name is not known at authorization time). Registration succeeds, saving the credential returns 403, and the pod exits.
   *Fix (pick one):*
   - Pre-create an empty `kubepulse-agent-state` Secret in the chart (annotate `helm.sh/resource-policy: keep`; use `lookup` or avoid templating `data` so upgrades do not wipe the stored credential), and grant `get/update/patch` with `resourceNames`; or
   - Grant `create` on `secrets` without `resourceNames` (broader, less preferred).

3. **A failed credential save burns the token and the name.** The retry then gets "token already used", and a new token for the same name gets "already registered".
   *Fix:* make registration two-phase. Keep the cluster `pending` until the first successful authenticated heartbeat, and let a pending cluster be re-registered with a fresh token. Alternatively let the agent generate the credential locally, persist it, then register with it. Also make the agent fail loudly on a persistence error **before** claiming success.

4. **Cluster names are not canonical.** The dashboard stores `Production`; the agent lowercases and heartbeats as `production`, so every heartbeat is 401.
   *Fix:* normalize once in one shared function (lowercase, trim, validate against DNS-1123), apply it at token generation, registration, heartbeat, and delete, and return the canonical name in the `/api/register` response so the agent uses exactly that.

### P0: Exposure

5. **Read APIs are public** (`GET /api/clusters`, resource lists, cost, scaling). The payload includes ConfigMap `.data`, pod annotations, and node internal/external IPs. The UI truncates long ConfigMap values but the API does not. The README deploys with `--allow-unauthenticated`.
   *Fix:* require auth on all `/api/*` reads (a viewer key or session), stop sending ConfigMap `.data` (send key names and sizes only, or an opt-in allowlist), and redact annotations by default. Update the README deploy command.

6. **`SECRET_KEY` doubles as the admin key and is auto-generated and printed** when unset.
   *Fix:* require `ADMIN_KEY` explicitly (refuse to start without it in non-debug mode), never print secrets, and separate the legacy heartbeat key from the admin key. Plan removal of the legacy path with a deprecation warning.

### P1: Cloud Run and history

7. **Not horizontally safe.** Registration tokens and live metrics are in memory; only registry and history are persisted. A second instance will not see the token, and two instances overwrite each other's GCS objects.
   *Fix:* document and enforce `--max-instances=1` for now, or move tokens to the registry store and use GCS generation preconditions (`if_generation_match`) for writes.

8. **Empty node list marks every node retired** and records a scaling point of 0 when the API list fails.
   *Fix:* the agent must distinguish "list failed" from "list empty" and skip or flag the heartbeat; the dashboard should ignore a zero-node snapshot unless the agent marks it authoritative.

9. **Accrued cost adds a fixed 30 s per heartbeat** instead of time since the node was last seen, so gaps and restarts under- or over-count.
   *Fix:* accrue `min(now - last_seen, cap)` per node, with a sane cap (for example 2 x heartbeat interval).

10. **Full rewrite of registry and history on every heartbeat.**
    *Fix:* debounce writes, write only on change, use atomic write (temp file + `os.replace`), and add a lock.

### P1: Collection and runtime

11. **ReplicaSet-to-Deployment mapping is by ReplicaSet name only**, so identical names in different namespaces map to the wrong Deployment.
    *Fix:* key by `(namespace, name)`, or follow `ownerReferences` UID.

12. **Events are listed once per namespace each heartbeat**, which will not scale.
    *Fix:* one cluster-wide events list, filtered by recency and type (Warning), or use a watch with a bounded cache.

13. **Dashboard runs Flask's dev server as root, single-threaded.** Instance-mode pricing may download a large AWS offer file inside the heartbeat request.
    *Fix:* run under gunicorn with a non-root user, and move price downloads to a background refresh with a disk cache and a timeout. Heartbeat handling must never block on the network.

14. **Memory suffix parser is wrong for decimal `k` and `m`** (about 1000x too small). `Ki/Mi/Gi` are fine.
    *Fix:* table-driven parser for `k, M, G, T, P, E, Ki..Ei, m` (note that lowercase `m` means milli, not mega), with unit tests for each.

### P2: Inferred, not yet verified **[verify]**

15. **Stored XSS through cluster data.** The UI is a single file rendering agent-supplied strings (pod names, labels, annotations, ConfigMap values). Any `innerHTML` use lets a compromised cluster run script in the admin's browser and read the `ADMIN_KEY` held there.
    *Fix:* use `textContent` or an escape helper everywhere, add a CSP header, and keep the admin key out of `localStorage` (prefer in-memory or a short-lived session).
16. **No rate limiting or body-size limit** on `/api/register` and `/api/heartbeat`. Set `MAX_CONTENT_LENGTH`, add per-IP throttling on register, and add a payload schema check.
17. **Thread safety.** Moving off the dev server exposes unsynchronized access to `live_data`, history, and the registry. Guard with locks.
18. **Deleted clusters.** Confirm deletion also removes live data, history, and credential hash, and that the legacy global key cannot silently re-create a deleted cluster.
19. **Node name reuse** (common on-prem) merges separate node lifetimes in history. Key by node UID plus name.
20. **Cluster-wide ConfigMap read** in the ClusterRole is broader than needed once ConfigMap data stops being sent; reduce to what the dashboard actually displays.
21. **Agent health.** `/healthz` should reflect heartbeat success (for example "last heartbeat within 2 intervals"), not only process liveness. Handle metrics-server being absent without failing the whole heartbeat.
22. **Payload growth.** Everything is sent every 30 s with no compression or size cap. Add gzip and a size ceiling, and consider delta or hash-based skipping for slow-changing lists (configmaps, ingresses).
23. **Chart hardening.** Add `securityContext` (non-root, read-only root FS, drop all capabilities), pinned image tags (not appVersion drift), optional NetworkPolicy, and `replicas: 1` enforced in the template.
24. **Spot, preemptible, and reserved pricing** are not modeled, and `prices.json` can go stale. Surface the price source and date in the UI.
25. **TLS.** The agent should verify the dashboard certificate by default, and the README should say plain HTTP is unsafe because secrets travel in headers.

## 7. Required tests (add alongside fixes)

The current suite covers registry persistence and pricing math. It does **not** cover:

- **Chart render:** `helm template` and `helm lint` with default values and with each toggle on/off (PDB, persistence modes). Run in CI.
- **State-Secret RBAC:** assert the rendered Role permits what the agent actually does (create or update/patch of the state Secret). A static check on the rendered YAML is enough.
- **Name round-trip:** register with `Production`, heartbeat as the agent would send it, expect 200.
- **Failed persistence:** simulate a save failure after register, then verify retry succeeds with the same or a fresh token.
- **Empty vs failed node list:** no retirements and no zero scaling point.
- **Cost accrual:** gap of N seconds accrues N seconds (capped), not 30.
- **Memory parser:** all suffixes.
- **ReplicaSet mapping:** two namespaces, same ReplicaSet name.
- **Auth matrix:** every route against {no key, wrong key, viewer, admin, legacy key}.

## 8. Conventions

- Python 3, standard library `unittest` (no new test framework without discussion).
- Keep the agent dependency-light; it runs with 100m CPU / 128Mi (limit 500m / 512Mi).
- Keep the dashboard free of cloud credentials and cloud SDKs, except GCS for storage.
- Configuration via environment variables; document each new variable in the README.
- Small, focused PRs. One defect per PR for P0/P1 items, each with a test.
- Never commit secrets, kubeconfigs, or real cluster output as fixtures; scrub IPs and names.

## 9. Definition of done

- [ ] `python -m unittest discover -s tests` passes and includes a test that failed before your change
- [ ] `helm lint` and `helm template` succeed with default values
- [ ] Both Docker images build
- [ ] No new unauthenticated route, no new secret in logs
- [ ] README and `values.yaml` comments updated if behavior or config changed
- [ ] Backward compatibility considered for already-registered clusters (the legacy key path, stored hashes)

## 10. Do not

- Do not add write permissions to the agent's ClusterRole.
- Do not make read APIs "temporarily" public to ease a demo.
- Do not store raw credentials on the dashboard.
- Do not run network downloads inside a request handler.
- Do not introduce a frontend build step or a second UI file without discussion.
- Do not rewrite stored registry or history formats without a migration path.