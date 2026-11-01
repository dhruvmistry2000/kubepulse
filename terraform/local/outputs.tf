output "context" {
  description = "kubectl context inside the generated kubeconfig."
  value       = kind_cluster.this.context_name
}

output "kubeconfig" {
  description = "Set KUBECONFIG to this file before kubectl or helm."
  value       = abspath("${path.module}/kubeconfig")
}

output "dashboard_url_from_pods" {
  description = "Dashboard address a pod in this cluster can reach on the host. Use it as dashboardUrl in the agent Helm install. The browser still uses http://127.0.0.1:8765."
  value       = data.external.network.result.gateway == "" ? null : "http://${data.external.network.result.gateway}:8765"
}

output "node_ip" {
  description = "kind node address on the Docker network. Cilium uses this as the API server."
  value       = data.external.network.result.node_ip
}
