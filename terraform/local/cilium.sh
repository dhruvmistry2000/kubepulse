#!/usr/bin/env bash
# Install Cilium after the kind cluster exists. kube-proxy is disabled because
# this host's running kernel has no matching netfilter modules.
set -euo pipefail

: "${KUBECONFIG:?}"
: "${CLUSTER_NAME:?}"
: "${CILIUM_VERSION:?}"
: "${POD_SUBNET:?}"
: "${NETWORK_ENV:?}"

node="${CLUSTER_NAME}-control-plane"

for _ in $(seq 1 60); do
  if kubectl get --raw=/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
kubectl get --raw=/healthz >/dev/null

node_ip="$(docker inspect "$node" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')"
gateway="$(docker inspect "$node" --format '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}')"
if [[ -z "$node_ip" || -z "$gateway" ]]; then
  echo "could not read Docker addresses for $node" >&2
  exit 1
fi

helm repo add cilium https://helm.cilium.io/ --force-update
helm repo update cilium
helm upgrade --install cilium cilium/cilium \
  --version "$CILIUM_VERSION" \
  --namespace kube-system \
  --set kubeProxyReplacement=true \
  --set k8sServiceHost="$node_ip" \
  --set k8sServicePort=6443 \
  --set bpf.masquerade=true \
  --set ipam.mode=kubernetes \
  --set routingMode=native \
  --set autoDirectNodeRoutes=true \
  --set ipv4NativeRoutingCIDR="$POD_SUBNET"

kubectl -n kube-system rollout status daemonset/cilium --timeout=300s
kubectl -n kube-system rollout status deployment/coredns --timeout=300s

printf 'node_ip=%s\ngateway=%s\n' "$node_ip" "$gateway" >"$NETWORK_ENV"
