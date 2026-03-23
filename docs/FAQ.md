
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

The dashboard stores cluster state in memory. On restart, all cluster data is cleared. However, agents will automatically re-register on their next heartbeat cycle (within 30 seconds), and the cluster will reappear in the sidebar without any manual intervention.

---

**Q: What happens if a registration token expires?**

Tokens are valid for 10 minutes. If a token expires before running `helm install`, generate a new token in the dashboard and use `helm upgrade --reuse-values` instead of a fresh `helm install`.

---

**Q: Does the agent write anything to the Kubernetes API?**

No. The agent only reads from the Kubernetes API using a read-only `ClusterRole` with `get`, `list`, and `watch` verbs. It never creates, updates, or deletes any Kubernetes resources.

---

**Q: How often does data refresh?**

- **Agent → Dashboard:** Every 30 seconds (heartbeat).
- **Browser → Dashboard:** Every 10–15 seconds (polling).
- **Stale data threshold:** If no heartbeat is received for 90+ seconds, the UI shows a stale data warning.

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

Note that the old cluster name entry will remain in the dashboard until removed manually via the UI or the delete API endpoint.

---

**Q: Where can I get help or report a bug?**

Open an issue on the [GitHub repository](https://github.com/dhruvmistry2000/kubepulse/issues). Please include your Kubernetes version, cloud provider, dashboard hosting method, and relevant log output.
