#!/usr/bin/env bash
#
# Deploy the K8s Dashboard to Google Cloud Run.
# Agents in clusters push data TO this dashboard (no agent URL needed here).
#
# Set variables below, then run: ./deploy-cloudrun.sh
#
# Prerequisites:
#   - gcloud CLI installed and logged in (gcloud auth login)
#   - Docker installed (if building in script)
#

set -e

# --- GCP & app config (set these or export before running) ---
PROJECT_ID="${GCP_PROJECT_ID:-consumption-442810}"
REGION="${GCP_REGION:-us-central1}"
APP_NAME="${APP_NAME:-k8s-dashboard}"

# Full Docker image name for the dashboard
DOCKER_IMAGE="us-central1-docker.pkg.dev/consumption-442810/micro-service-iam/k8s-dashboard:v7"

# Set to 1 to build and push the dashboard image before deploying
BUILD_IMAGE="${BUILD_IMAGE:-1}"

# --- Runtime env for the dashboard ---
# SECRET_KEY: required. Set a strong secret; agents receive this after registration. If unset, dashboard generates one and prints it (copy to persist).
SECRET_KEY="${SECRET_KEY:-4111cce2d18b36786b3425346ce902d0dfc2b24b524a90ab24c75a7ec7626aeb}"
# DASHBOARD_URL: your public dashboard URL. Used to pre-fill helm commands in Add Cluster tab.
DASHBOARD_URL="${DASHBOARD_URL:-https://k8s-dashboard-777078318627.us-central1.run.app}"
# ADMIN_KEY: optional; defaults to SECRET_KEY. Used to protect token generation (Add Cluster tab).
# ADMIN_KEY="${ADMIN_KEY:-}"

echo "=== Deploying K8s Dashboard to Cloud Run ==="
echo "  PROJECT_ID:    $PROJECT_ID"
echo "  REGION:       $REGION"
echo "  APP_NAME:     $APP_NAME"
echo "  DOCKER_IMAGE: $DOCKER_IMAGE"
echo "  BUILD_IMAGE:  $BUILD_IMAGE"
echo ""

if [[ -z "$SECRET_KEY" ]]; then
  echo "NOTE: SECRET_KEY is not set. Dashboard will generate one at startup and print it."
  echo "      Copy it from logs and set it as env var to persist across restarts."
  echo ""
fi

if [[ "$BUILD_IMAGE" == "1" ]]; then
  echo "Building dashboard image: $DOCKER_IMAGE"
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  docker build --no-cache -t "$DOCKER_IMAGE" "$SCRIPT_DIR/dashboard"
  echo "Pushing image..."
  docker push "$DOCKER_IMAGE"
  echo ""
fi

echo "Deploying to Cloud Run..."
ENV_VARS=""
[[ -n "$SECRET_KEY" ]] && ENV_VARS="SECRET_KEY=${SECRET_KEY}"
[[ -n "$DASHBOARD_URL" ]] && ENV_VARS="${ENV_VARS},DASHBOARD_URL=${DASHBOARD_URL}"
[[ -n "$ADMIN_KEY" ]] && ENV_VARS="${ENV_VARS},ADMIN_KEY=${ADMIN_KEY}"
# Remove leading comma if SECRET_KEY was empty
ENV_VARS="${ENV_VARS#,}"

EXTRA=""
# Uncomment to use a specific service account:
# EXTRA="--service-account=${SERVICE_ACCOUNT}"

if [[ -z "$ENV_VARS" ]]; then
  gcloud run deploy "$APP_NAME" \
    --image="$DOCKER_IMAGE" \
    --platform=managed \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --allow-unauthenticated \
    $EXTRA
else
  gcloud run deploy "$APP_NAME" \
    --image="$DOCKER_IMAGE" \
    --platform=managed \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --set-env-vars="$ENV_VARS" \
    --allow-unauthenticated \
    $EXTRA
fi

echo ""
SVC_URL=$(gcloud run services describe "$APP_NAME" --platform=managed --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)' 2>/dev/null || true)
echo "Done. Dashboard URL: $SVC_URL"
echo ""
if [[ -z "$DASHBOARD_URL" && -n "$SVC_URL" ]]; then
  echo "Tip: Set DASHBOARD_URL so the Add Cluster tab shows pre-filled helm commands:"
  echo "  gcloud run services update $APP_NAME --region=$REGION --project=$PROJECT_ID --set-env-vars=DASHBOARD_URL=$SVC_URL"
  echo ""
fi
echo "Next: Open $SVC_URL → Add Cluster tab → generate a token → run the helm command on your cluster(s)."
echo "      No LoadBalancer or agent URL needed — agents push data to this dashboard."
echo ""
