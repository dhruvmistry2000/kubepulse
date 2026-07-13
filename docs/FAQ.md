
# FAQ

Frequently asked questions about KubePulse.

---

**Q: Does the dashboard need to be inside the same cluster as the agent?**

No. The dashboard can be hosted anywhere — Cloud Run, a VM, or a separate cluster — as long as it is reachable over HTTPS. Agents communicate outbound to the dashboard URL; no inbound network access to the clusters is needed.

---

**Q: Can I monitor multiple clusters from one dashboard?**

Yes. KubePulse is designed for multi-cluster use. Each cluster runs its own agent Helm chart, and all agents push to the same dashboard URL. The dashboard sidebar lets you switch between clusters.

---

**Q: Does KubePulse work with private clusters that have no public IP?**

Yes. The agent only needs **outbound** HTTPS access (port 443) to reach the dashboard. The cluster does not need to accept any inbound connections.

---

**Q: What happens if the dashboard restarts?**

With persistence enabled (default `STORAGE_BACKEND=file`, or `gcs` on Cloud Run), the **cluster registry** and scaling history survive restarts. Live metrics refill on the next agent heartbeat (~30s). Agents do **not** need to re-register.

Without durable storage, the registry is wiped and heartbeats fail auth until you restore the registry or re-register each agent with a new token. Always use file (with a volume) or GCS in production.

---

**Q: What happens if a registration token expires?**

Tokens are valid for 10 minutes. If a token expires before the first `helm install`, generate a new token and install again. After a successful registration, upgrades use `helm upgrade --reuse-values` with **no new token** (credential is persisted in-cluster).

---

**Q: Does the agent write anything to the Kubernetes API?**

Almost nothing. The ClusterRole is read-only (`get`, `list`, `watch`). With default `persistence.mode=secret`, the agent also creates/updates a single namespaced Secret (`kubepulse-agent-state`) to store its credential across restarts.

---

**Q: How often does data refresh?**

- **Agent → Dashboard:** Every 30 seconds (heartbeat).
- **Browser → Dashboard:** Every 10–15 seconds (polling).
- **Stale data threshold:** If no heartbeat is received for 90+ seconds, the UI shows a stale data warning.

---

**Q: Where is cost calculated?**

On the **dashboard**. Agents send CPU/RAM and node metadata only. The dashboard applies the price map (and optional public list-price APIs) and exposes Cost / Nodes tabs plus `/api/cost`.

---

**Q: Do I need cloud billing credentials for cost?**

No. Pricing uses a static map, optional public Azure/AWS price APIs, and usage-based rates. No GCP/AWS/Azure billing account keys are required.

---

**Q: Do I need to build the Docker images myself?**

No. Pre-built images are published to Docker Hub and GHCR on every push to `main` and daily at midnight UTC. You can use them directly:

```
dhruvmistry200/kubepulse-dashboard:latest
dhruvmistry200/kubepulse-agent:latest
```

Build your own images only if you want to customise the source code or host images in a private registry.

---

**Q: Does KubePulse require Prometheus or any external monitoring system?**

No. KubePulse is self-contained. It reads metrics directly from the Kubernetes Metrics Server (`metrics.k8s.io` API). No Prometheus, Grafana, or other third-party monitoring stack is required.

---

**Q: Is there a Helm chart for the dashboard?**

Not currently. The Helm chart in the `helm/` directory is for the agent only. The dashboard is deployed via Cloud Run, Docker, or raw `kubectl` commands. A dashboard Helm chart is a potential future contribution — see [[Contributing]].

---

**Q: Can I use KubePulse with a self-hosted Kubernetes cluster (kubeadm, k3s, etc.)?**

Yes, as long as the agent pod can reach the dashboard URL over HTTPS. The agent uses standard in-cluster Kubernetes API access, which works on any conformant Kubernetes distribution.

---

**Q: How do I change the `clusterName` after installation?**

```bash
helm upgrade kubepulse-agent ./helm \
  --set clusterName=new-name \
  --reuse-values
```

Note that the old cluster name entry will remain in the dashboard until removed manually via the UI or the delete API endpoint. You may also need a new registration if the stored credential is bound to the previous name.

---

**Q: Where can I get help or report a bug?**

Open an issue on the [GitHub repository](https://github.com/dhruvmistry2000/kubepulse/issues). Please include your Kubernetes version, cloud provider, dashboard hosting method, and relevant log output.
