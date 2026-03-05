#!/usr/bin/env bash
#
# Fix 403 Forbidden when GKE pulls the agent image from Artifact Registry.
# Run once per cluster (or when the node pool service account changes).
#
# Usage:
#   ./fix-image-pull.sh
#   # Or with env:
#   GCP_PROJECT=cleanstart-portal GCP_REGION=us-central1 ./fix-image-pull.sh
#

set -e

GCP_PROJECT="${GCP_PROJECT:-cleanstart-portal}"
GCP_REGION="${GCP_REGION:-us-central1}"
CLUSTER_NAME="${CLUSTER_NAME:-cdp-cluster}"

echo "=== Granting GKE node SA permission to pull from Artifact Registry ==="
echo "  Project: $GCP_PROJECT"
echo "  Cluster: $CLUSTER_NAME"
echo "  Region:  $GCP_REGION"
echo ""

# Get project number (used by default node SA)
PROJECT_NUMBER=$(gcloud projects describe "$GCP_PROJECT" --format='value(projectNumber)')
NODE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

echo "Node service account: $NODE_SA"
echo ""

# Grant Artifact Registry Reader so nodes can pull images
echo "Adding roles/artifactregistry.reader to $NODE_SA in project $GCP_PROJECT ..."
gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
  --member="serviceAccount:${NODE_SA}" \
  --role="roles/artifactregistry.reader" \
  --condition=None \
  --quiet

echo ""
echo "Done. Wait a minute, then the pod should be able to pull the image."
echo "If the pod is still in ImagePullBackOff, delete it to force a new pull:"
echo "  kubectl delete pod -l app.kubernetes.io/name=k8s-dashboard-agent"
echo ""
