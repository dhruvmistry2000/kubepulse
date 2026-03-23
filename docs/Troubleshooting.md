
# Troubleshooting

This page covers the most common issues encountered when deploying or operating KubePulse, along with diagnostic steps and fixes.

---

## Agent pod is Running but cluster is not visible in the dashboard

**Symptoms:** `kubectl get pods` shows the agent as `Running`, but the cluster does not appear in the dashboard sidebar.

**Diagnostic steps:**

```bash
# Stream agent logs
kubectl logs -f deployment/kubepulse-agent
```

Look for errors related to registration or heartbeat.

**Common causes and fixes:**

| Cause | Fix |
|---|---|
| `dashboardUrl` is wrong or unreachable | Verify the URL is correct and reachable from inside the cluster: `kubectl exec -it <agent-pod> -- curl https://YOUR_DASHBOARD_URL` |
| Registration token expired (10-min TTL) | Generate a new token and run `helm upgrade` (see below) |
| `SECRET_KEY` not set on dashboard | Ensure the `SECRET_KEY` environment variable is set on your dashboard deployment |
| TLS certificate is invalid | The dashboard must have a valid TLS cert. Self-signed certs may be rejected by the agent |

---

## Token expired before running `helm install`

**Symptoms:** Agent logs show a `401` or `token invalid/expired` error.

**Fix:** Generate a new token in the dashboard, then upgrade the Helm release:

```bash
helm upgrade kubepulse-agent ./helm \
  --set registrationToken=NEW_TOKEN_FROM_DASHBOARD \
  --reuse-values
```

Do **not** run `helm install` again — the release already exists.

---

## Metrics show `—` instead of values

**Symptoms:** CPU and memory columns in the dashboard show dashes instead of numbers.

**Cause:** The Kubernetes `metrics-server` is not installed in the cluster.

**Fixes by provider:**

- **GKE:** Metrics server is pre-installed. If it's missing, enable it via the GKE console.
- **EKS:**
  ```bash
  kubectl apply -f \
    https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
  ```
- **AKS:** Metrics server is pre-installed. If it's missing, enable the Metrics Add-on via Azure portal.
- **Other clusters:**
  ```bash
  kubectl apply -f \
    https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
  ```

After installation, allow 1–2 minutes for metrics to populate, then refresh the dashboard.

---

## Dashboard shows a "stale data" banner

**Symptoms:** A banner appears in the UI indicating that cluster data is stale.

**Cause:** No heartbeat received from the agent in 90+ seconds.

**Diagnostic steps:**

```bash
# Check agent pod status
kubectl get pods -l app=kubepulse-agent

# Check logs for heartbeat failures
kubectl logs -f deployment/kubepulse-agent
```

**Common causes:**

| Cause | Fix |
|---|---|
| Agent pod is crash-looping | Check logs for errors; verify `dashboardUrl` and `SECRET_KEY` are correct |
| Cluster has no outbound internet | Ensure nodes can reach port 443 on the dashboard URL (NAT Gateway, firewall rules) |
| Dashboard restarted and lost state | Agents will re-register automatically on the next heartbeat cycle |

---

## Agent can't reach the dashboard (network error)

**Test connectivity from inside the cluster:**

```bash
kubectl run curl-test --image=curlimages/curl --rm -it --restart=Never -- \
  curl -v https://YOUR_DASHBOARD_URL/api/register
```

If this fails, the issue is network-level (firewall, DNS, or TLS), not KubePulse-specific.

---

## Dashboard returns 500 errors

**Check dashboard logs:**

- **Cloud Run:** `gcloud run services logs read kubepulse-dashboard`
- **Docker:** `docker logs kubepulse-dashboard`
- **Kubernetes:** `kubectl logs deployment/kubepulse-dashboard`

The most common cause is a missing or empty `SECRET_KEY` environment variable.

---

## Helm install fails with "already exists" error

```
Error: INSTALLATION FAILED: cannot re-use a name that is still in use
```

**Fix:** The release already exists. Use `helm upgrade` instead:

```bash
helm upgrade kubepulse-agent ./helm \
  --set registrationToken=TOKEN \
  --set dashboardUrl=https://YOUR_DASHBOARD_URL \
  --set clusterName=production
```

---

## Cluster disappeared from the dashboard after a restart

**Cause:** The dashboard stores cluster state in memory. A restart clears all registered clusters.

**Fix:** The agents will automatically re-register on their next heartbeat cycle (within 30 seconds). No manual action is needed. The cluster will reappear in the sidebar once the agent sends its first heartbeat after the dashboard comes back online.

---

## Debugging Commands Reference

```bash
# Agent pod status
kubectl get pods -l app=kubepulse-agent

# Agent logs (live)
kubectl logs -f deployment/kubepulse-agent

# Agent environment variables
kubectl exec deployment/kubepulse-agent -- env | grep -E 'DASHBOARD|CLUSTER'

# Test outbound connectivity from cluster
kubectl run curl-test --image=curlimages/curl --rm -it --restart=Never -- \
  curl -v https://YOUR_DASHBOARD_URL

# List all kubepulse resources
kubectl get all -l app=kubepulse-agent

# Check ClusterRole and binding
kubectl get clusterrole kubepulse-agent
kubectl get clusterrolebinding kubepulse-agent
```
