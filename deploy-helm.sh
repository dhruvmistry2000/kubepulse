#!/usr/bin/env bash
#
# Install the K8s Dashboard Agent via Helm.
# The agent registers with the dashboard (one-time token) and pushes data every 30s.
#
# Usage:
#   First, open your dashboard → Add Cluster tab → enter cluster name → Generate Install Token.
#   Then run: REGISTRATION_TOKEN=<token> DASHBOARD_URL=<url> CLUSTER_NAME=<name> ./deploy-helm.sh
#
# Or pass values on the command line:
#   ./deploy-helm.sh <registration-token> <dashboard-url> [cluster-name]
#
# Prerequisites:
#   - kubectl configured for your cluster
#   - helm installed
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELM_DIR="${SCRIPT_DIR}/helm"

# --- Helm release and cluster ---
RELEASE_NAME="${RELEASE_NAME:-k8s-agent}"
NAMESPACE="${NAMESPACE:-default}"

# --- Values (from env or positional args) ---
# Get token from dashboard Add Cluster tab (valid 10 minutes)
REGISTRATION_TOKEN="${REGISTRATION_TOKEN:-}"
# Your dashboard URL (e.g. https://k8s-dashboard-xxx.run.app)
DASHBOARD_URL="${DASHBOARD_URL:-}"
# Friendly name for this cluster (e.g. production, staging)
CLUSTER_NAME="${CLUSTER_NAME:-default}"

IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-us-central1-docker.pkg.dev/consumption-442810/micro-service-iam/k8s-agent}"
IMAGE_TAG="${IMAGE_TAG:-v2}"

# Optional: positional args (token, dashboard_url, [cluster_name])
if [[ -n "$1" ]]; then
  REGISTRATION_TOKEN="$1"
fi
if [[ -n "$2" ]]; then
  DASHBOARD_URL="$2"
fi
if [[ -n "$3" ]]; then
  CLUSTER_NAME="$3"
fi

echo "=== Deploying K8s Dashboard Agent (Helm) ==="
echo "  RELEASE_NAME:        $RELEASE_NAME"
echo "  NAMESPACE:           $NAMESPACE"
echo "  IMAGE:               $IMAGE_REPOSITORY:$IMAGE_TAG"
echo "  DASHBOARD_URL:       ${DASHBOARD_URL:-(not set)}"
echo "  CLUSTER_NAME:        $CLUSTER_NAME"
echo "  REGISTRATION_TOKEN:  ${REGISTRATION_TOKEN:+<set>}${REGISTRATION_TOKEN:-<not set>}"
echo ""

if [[ -z "$REGISTRATION_TOKEN" ]]; then
  echo "ERROR: REGISTRATION_TOKEN is required."
  echo "  Get it from the dashboard: Add Cluster tab → enter cluster name → Generate Install Token."
  echo "  Then run: REGISTRATION_TOKEN=<token> DASHBOARD_URL=<url> CLUSTER_NAME=$CLUSTER_NAME $0"
  exit 1
fi

if [[ -z "$DASHBOARD_URL" ]]; then
  echo "ERROR: DASHBOARD_URL is required (e.g. https://your-dashboard.run.app)."
  exit 1
fi

# Create namespace if it doesn't exist (optional; skip for default)
if [[ "$NAMESPACE" != "default" ]]; then
  kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
fi

echo "Running: helm install $RELEASE_NAME $HELM_DIR -n $NAMESPACE ..."
helm install "$RELEASE_NAME" "$HELM_DIR" \
  -n "$NAMESPACE" \
  --set "registrationToken=${REGISTRATION_TOKEN}" \
  --set "dashboardUrl=${DASHBOARD_URL}" \
  --set "clusterName=${CLUSTER_NAME}" \
  --set "image.repository=${IMAGE_REPOSITORY}" \
  --set "image.tag=${IMAGE_TAG}"

echo ""
echo "Done. The agent will register with the dashboard and appear in the sidebar within ~30 seconds."
echo "No LoadBalancer needed — the agent pushes data to the dashboard (outbound only)."
echo ""
