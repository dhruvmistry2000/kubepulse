# Deployment

This page covers all supported deployment options for the KubePulse dashboard and the agent Helm chart, including production considerations.

---

## Dashboard Deployment Options

### Option A — Google Cloud Run (Recommended)

Cloud Run is the simplest option: instant HTTPS, automatic scaling, and a generous free tier.

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

Or use the included helper script:

```bash
chmod +x deploy-cloudrun.sh
./deploy-cloudrun.sh
```

**Production tip:** Set `--min-instances=1` to avoid cold-start latency:

```bash
gcloud run services update kubepulse-dashboard --min-instances=1
```

---

### Option B — Docker on a VM

Deploy the dashboard container on any Linux VM, then terminate TLS with nginx.

```bash
docker run -d \
  --name kubepulse-dashboard \
  --restart unless-stopped \
  -p 127.0.0.1:8080:8080 \
  -e SECRET_KEY=change-me-to-a-strong-secret \
  -e ADMIN_KEY=change-me-to-admin-key \
  dhruvmistry200/kubepulse-dashboard:latest
```

Sample nginx reverse proxy configuration:

```nginx
server {
    listen 443 ssl;
    server_name kubepulse.example.com;

    ssl_certificate     /etc/letsencrypt/live/kubepulse.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/kubepulse.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Or use the helper script:

```bash
chmod +x deploy.sh
./deploy.sh
```

---

### Option C — Inside Kubernetes

Run the dashboard as a Deployment in any Kubernetes cluster. Expose it via an Ingress with TLS.

```bash
kubectl create deployment kubepulse-dashboard \
  --image=dhruvmistry200/kubepulse-dashboard:latest

kubectl set env deployment/kubepulse-dashboard \
  SECRET_KEY=change-me-to-a-strong-secret \
  ADMIN_KEY=change-me-to-admin-key

kubectl expose deployment kubepulse-dashboard \
  --port=8080 --type=LoadBalancer
```

For production, use an Ingress controller (e.g., nginx-ingress or GKE Ingress) with cert-manager for automated TLS.

---

## Agent Deployment (Helm)

The agent is distributed as a Helm chart located in the `helm/` directory.

```bash
helm install kubepulse-agent ./helm \
  --set registrationToken=TOKEN_FROM_DASHBOARD \
  --set dashboardUrl=https://YOUR_DASHBOARD_URL \
  --set clusterName=production
```

Or use the helper script:

```bash
chmod +x deploy-helm.sh
./deploy-helm.sh
```

### Helm Chart Resources Created

| Resource | Name | Purpose |
|---|---|---|
| `Deployment` | `kubepulse-agent` | Runs the agent pod |
| `ServiceAccount` | `kubepulse-agent` | Dedicated identity for the agent |
| `ClusterRole` | `kubepulse-agent` | Read-only access to K8s API resources |
| `ClusterRoleBinding` | `kubepulse-agent` | Binds ClusterRole to ServiceAccount |
| `Secret` | `kubepulse-agent` | Stores registration token |
| `Service` | `kubepulse-agent` | (Optional) Internal service for agent pod |

---

## Cloud-Specific Notes

### GKE (Google Kubernetes Engine)

- Only outbound port 443 is needed. No inbound firewall rules required.
- Private clusters are fully supported.
- Use **Google Artifact Registry** for image storage in GCP projects.
- Metrics server is enabled by default on GKE.

### EKS (Amazon Elastic Kubernetes Service)

- Ensure node groups have outbound internet access (via NAT Gateway or public subnets).
- Use **Amazon ECR** for image storage in AWS environments.
- The agent's ServiceAccount requires **no AWS IAM permissions**.
- Metrics server must be installed manually if not already present:

```bash
kubectl apply -f \
  https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

### AKS (Azure Kubernetes Service)

- Works with both kubenet and Azure CNI networking.
- Use **Azure Container Registry (ACR)** for image storage.
- Private clusters are supported; only outbound HTTPS is required.
- Metrics server is enabled by default on AKS.

---

## Production Considerations

- **Use separate `SECRET_KEY` and `ADMIN_KEY` values.** Don't rely on the default of `ADMIN_KEY = SECRET_KEY`.
- **Use strong, randomly generated secrets.** At least 32 random characters for `SECRET_KEY`.
- **Pin image tags in production.** Avoid `latest` in production Helm values. Use a specific version tag.
- **Enable dashboard persistence** (if needed): The current release stores cluster data in memory. A dashboard restart clears all state; agents will re-register automatically on their next heartbeat.
- **Monitor the dashboard itself** using your existing observability stack (uptime checks, Cloud Run metrics, etc.).
- **Restrict `ADMIN_KEY` access.** The admin key can generate tokens that give agents permanent access to push data. Protect it accordingly.

---

## Upgrading

### Upgrade the agent

```bash
# Pull latest image defaults, retain existing Helm values
helm upgrade kubepulse-agent ./helm --reuse-values
```

### Upgrade the dashboard (Cloud Run)

```bash
gcloud run deploy kubepulse-dashboard \
  --image dhruvmistry200/kubepulse-dashboard:latest
```

### Upgrade the dashboard (Docker)

```bash
docker pull dhruvmistry200/kubepulse-dashboard:latest
docker stop kubepulse-dashboard && docker rm kubepulse-dashboard
# Re-run the docker run command from Option B above
```

---

## Uninstalling

```bash
# Remove the agent from a cluster
helm uninstall kubepulse-agent

# Remove a cluster entry from the dashboard (API)
curl -X DELETE https://YOUR_DASHBOARD_URL/api/cluster/production \
  -H "X-Admin-Key: your-admin-key"
```
