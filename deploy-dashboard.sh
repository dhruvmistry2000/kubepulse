#!/usr/bin/env bash
# Deploy the kubepulse dashboard. Prompts for the URL agents will call, the
# access keys, and where to run it (Docker Compose, Docker, or Kubernetes).
# Press Enter on a key prompt to generate one. Generated keys are printed once.
#
#   ./scripts/deploy-dashboard.sh
#   DASHBOARD_URL=https://dashboard.example.com ./scripts/deploy-dashboard.sh --yes --target docker
#   ./scripts/deploy-dashboard.sh --dry-run --yes --target compose
#
# Keys are written to a mode 600 env file. Docker Compose uses the repo .env.
# Docker and Kubernetes use ~/.config/kubepulse/dashboard.env unless
# KUBEPULSE_ENV_FILE is set. The Kubernetes manifest does not contain keys.

set -euo pipefail
umask 077

SOURCE="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

ASSUME_YES=0
DRY_RUN=0
FORCE=0
TARGET="${KUBEPULSE_DEPLOY_TARGET:-}"

usage() {
  cat <<'EOF'
Usage: deploy-dashboard.sh [--yes] [--dry-run] [--force] [--target compose|docker|kubernetes]

Prompts for the dashboard URL agents use, the admin and viewer keys, and the
place to run the dashboard. Press Enter on a key prompt to generate a key.

  --yes        Do not prompt. Use environment variables and defaults.
  --dry-run    Write the env file and print the command. Do not start anything.
  --force      Replace an existing env file or Docker container.
  --target     compose (build this repo), docker (published image), or kubernetes.

Environment:
  DASHBOARD_URL            Address agents call. Required. Not the browser loopback URL.
  ADMIN_KEY                Admin key. Generated when unset.
  VIEWER_KEY               Read-only key. Generated when unset. Set empty to skip.
  SECRET_KEY               Legacy heartbeat key. Leave unset when you have none.
  DASHBOARD_PORT           Host port for Compose and Docker. Default 8080.
  KUBEPULSE_DASHBOARD_IMAGE  Image for docker and kubernetes.
  KUBEPULSE_NAMESPACE      Kubernetes namespace. Default kubepulse.
  KUBEPULSE_SERVICE_TYPE   ClusterIP, NodePort, or LoadBalancer. Default ClusterIP.
  KUBEPULSE_STORAGE_CLASS  PVC storage class. Default is the cluster default.
  HELM_REPO_URL            Chart repo embedded in the Add Cluster command.
  AGENT_IMAGE              Agent image embedded in that command.
  KUBEPULSE_ENV_FILE       Where to write keys. Default depends on the target.
  KUBEPULSE_MANIFEST_FILE  Kubernetes manifest path. Contains no keys.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -y|--yes)
      ASSUME_YES=1
      ;;
    -n|--dry-run)
      DRY_RUN=1
      ;;
    -f|--force)
      FORCE=1
      ;;
    --target)
      [[ $# -ge 2 ]] || { echo "Missing value for --target" >&2; exit 2; }
      TARGET="$2"
      shift
      ;;
    --target=*)
      TARGET="${1#*=}"
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

die() {
  echo "$1" >&2
  exit 1
}

interactive() {
  [[ "$ASSUME_YES" == "0" && -t 0 ]]
}

require_python() {
  command -v python3 >/dev/null 2>&1 || die "python3 is required."
}

new_key() {
  python3 -c 'import secrets; print(secrets.token_hex(32), end="")'
}

prompt_line() {
  local __name="$1"
  local __label="$2"
  local __default="${3:-}"
  local __reply
  if [[ -n "${!__name+x}" ]]; then
    return 0
  fi
  if ! interactive; then
    printf -v "$__name" '%s' "$__default"
    return 0
  fi
  if [[ -n "$__default" ]]; then
    read -r -p "$__label [$__default]: " __reply
    printf -v "$__name" '%s' "${__reply:-$__default}"
  else
    read -r -p "$__label: " __reply
    printf -v "$__name" '%s' "$__reply"
  fi
}

prompt_secret() {
  local __name="$1"
  local __label="$2"
  local __reply
  if [[ -n "${!__name+x}" ]]; then
    return 0
  fi
  if ! interactive; then
    return 0
  fi
  read -r -s -p "$__label: " __reply
  echo
  printf -v "$__name" '%s' "$__reply"
}

choose_target() {
  local choice
  if [[ -n "$TARGET" ]]; then
    return 0
  fi
  if ! interactive; then
    die "Set --target or KUBEPULSE_DEPLOY_TARGET to compose, docker, or kubernetes."
  fi
  cat <<'EOF'
Where should the dashboard run?
  1) Docker Compose — build dashboard/ from this repo
  2) Docker — run the published image
  3) Kubernetes — one replica, registry stored on a volume
EOF
  read -r -p "Choice [1]: " choice
  case "${choice:-1}" in
    1|compose) TARGET=compose ;;
    2|docker) TARGET=docker ;;
    3|kubernetes|k8s) TARGET=kubernetes ;;
    *) die "Unknown choice: ${choice}" ;;
  esac
}

normalize_target() {
  case "$TARGET" in
    compose|docker|kubernetes) ;;
    k8s) TARGET=kubernetes ;;
    *) die "Unknown target: $TARGET (use compose, docker, or kubernetes)." ;;
  esac
}

canonical_url() {
  URL_VALUE="$1" python3 -c '
import os, sys
from urllib.parse import urlparse
raw = os.environ.get("URL_VALUE", "").strip()
if not raw or any(ch.isspace() for ch in raw) or "#" in raw:
    sys.stderr.write("URL must not contain spaces or a fragment.\n")
    sys.exit(1)
parsed = urlparse(raw)
if parsed.scheme not in ("http", "https") or not parsed.hostname:
    sys.stderr.write("URL must start with http:// or https:// and include a host.\n")
    sys.exit(1)
if parsed.username or parsed.password:
    sys.stderr.write("URL must not include a username or password.\n")
    sys.exit(1)
if len(raw) > 2048:
    sys.stderr.write("URL is too long.\n")
    sys.exit(1)
sys.stdout.write(raw.rstrip("/"))
'
}

warn_dashboard_url() {
  DASHBOARD_URL="$DASHBOARD_URL" python3 -c '
import ipaddress, os, sys
from urllib.parse import urlparse
parsed = urlparse(os.environ["DASHBOARD_URL"])
host = parsed.hostname or ""
loopback = host == "localhost"
private = False
try:
    ip = ipaddress.ip_address(host)
except ValueError:
    ip = None
if ip is not None:
    loopback = loopback or ip.is_loopback
    private = ip.is_private or ip.is_loopback or ip.is_link_local
if loopback:
    sys.stderr.write("Warning: this URL is a loopback address. Pods cannot reach 127.0.0.1 or localhost. Use the address a pod would use, such as the host gateway.\n")
if parsed.scheme == "http" and not private and not loopback:
    sys.stderr.write("Warning: agents send their credential in a header. Use HTTPS for a dashboard that is not on a private network.\n")
'
}

validate_token() {
  local name="$1"
  local value="$2"
  local required="$3"
  if [[ -z "$value" ]]; then
    if [[ "$required" == "1" ]]; then
      die "$name is required."
    fi
    return 0
  fi
  if [[ "${#value}" -lt 8 ]]; then
    die "$name must be at least 8 characters."
  fi
  if [[ ! "$value" =~ ^[A-Za-z0-9._~+/-]+$ ]]; then
    die "$name may contain only letters, digits, and . _ ~ + / -"
  fi
}

validate_port() {
  if [[ ! "$DASHBOARD_PORT" =~ ^[0-9]+$ ]] || [[ "$DASHBOARD_PORT" -lt 1 ]] || [[ "$DASHBOARD_PORT" -gt 65535 ]]; then
    die "DASHBOARD_PORT must be a port from 1 to 65535."
  fi
}

validate_image() {
  local name="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[A-Za-z0-9][A-Za-z0-9._:/@-]*$ ]]; then
    die "$name must look like a container image reference."
  fi
}

validate_dns_label() {
  local name="$1"
  local value="$2"
  if [[ "${#value}" -gt 63 ]] || [[ ! "$value" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]]; then
    die "$name must be a DNS label (lowercase letters, digits, hyphens)."
  fi
}

validate_storage_class() {
  if [[ -z "$KUBEPULSE_STORAGE_CLASS" ]]; then
    return 0
  fi
  if [[ "${#KUBEPULSE_STORAGE_CLASS}" -gt 253 ]] || [[ ! "$KUBEPULSE_STORAGE_CLASS" =~ ^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$ ]]; then
    die "KUBEPULSE_STORAGE_CLASS must be a DNS subdomain."
  fi
}

write_env_file() {
  local dest="$1"
  shift
  local dir
  dir="$(dirname "$dest")"
  mkdir -p "$dir"
  if [[ -e "$dest" && "$FORCE" != "1" ]]; then
    die "Refusing to overwrite $dest. Re-run with --force."
  fi
  ENV_DEST="$dest" ENV_KEYS="$*" python3 - <<'PY'
import os, tempfile
dest = os.environ["ENV_DEST"]
keys = [key for key in os.environ["ENV_KEYS"].split(",") if key]
lines = ["# Generated by scripts/deploy-dashboard.sh. Do not commit.\n"]
for key in keys:
    value = os.environ.get(key, "")
    if value == "":
        continue
    if any(ch in value for ch in "\r\n\x00"):
        raise SystemExit("%s contains a newline." % key)
    lines.append("%s=%s\n" % (key, value))
directory = os.path.dirname(dest) or "."
fd, tmp = tempfile.mkstemp(dir=directory, prefix=".dashboard-env.")
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.writelines(lines)
    os.replace(tmp, dest)
except Exception:
    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise
os.chmod(dest, 0o600)
PY
}

write_manifest() {
  local dest="$1"
  local storage_class_yaml=""
  if [[ -n "$KUBEPULSE_STORAGE_CLASS" ]]; then
    storage_class_yaml="  storageClassName: ${KUBEPULSE_STORAGE_CLASS}"$'\n'
  fi
  local pull_policy="IfNotPresent"
  case "$KUBEPULSE_DASHBOARD_IMAGE" in
    *:latest|*:LATEST) pull_policy="Always" ;;
  esac
  mkdir -p "$(dirname "$dest")"
  cat >"$dest" <<EOF
# Generated by scripts/deploy-dashboard.sh. Keys live in the kubepulse-dashboard Secret.
# Live metrics and registration tokens are in memory. Do not scale past one replica.
apiVersion: v1
kind: Namespace
metadata:
  name: ${KUBEPULSE_NAMESPACE}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: kubepulse-dashboard-data
  namespace: ${KUBEPULSE_NAMESPACE}
spec:
  accessModes:
    - ReadWriteOnce
${storage_class_yaml}  resources:
    requests:
      storage: 1Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubepulse-dashboard
  namespace: ${KUBEPULSE_NAMESPACE}
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app.kubernetes.io/name: kubepulse-dashboard
  template:
    metadata:
      labels:
        app.kubernetes.io/name: kubepulse-dashboard
        app.kubernetes.io/part-of: kubepulse
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: dashboard
          image: ${KUBEPULSE_DASHBOARD_IMAGE}
          imagePullPolicy: ${pull_policy}
          ports:
            - name: http
              containerPort: 8080
          envFrom:
            - secretRef:
                name: kubepulse-dashboard
          readinessProbe:
            httpGet:
              path: /
              port: http
          livenessProbe:
            httpGet:
              path: /
              port: http
            initialDelaySeconds: 15
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: "1"
              memory: 512Mi
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop:
                - ALL
          volumeMounts:
            - name: data
              mountPath: /data/kubepulse
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: kubepulse-dashboard-data
---
apiVersion: v1
kind: Service
metadata:
  name: kubepulse-dashboard
  namespace: ${KUBEPULSE_NAMESPACE}
spec:
  type: ${KUBEPULSE_SERVICE_TYPE}
  selector:
    app.kubernetes.io/name: kubepulse-dashboard
  ports:
    - name: http
      port: 8080
      targetPort: http
EOF
  chmod 600 "$dest"
}

default_env_file() {
  case "$TARGET" in
    compose) printf '%s\n' "$ROOT/.env" ;;
    *) printf '%s\n' "${HOME}/.config/kubepulse/dashboard.env" ;;
  esac
}

key_status() {
  local label="$1"
  local generated_flag="$2"
  local value="$3"
  if [[ "$generated_flag" == "1" ]]; then
    printf '%s: %s\n' "$label" "$value"
  elif [[ -n "$value" ]]; then
    printf '%s: saved in the env file\n' "$label"
  else
    printf '%s: unset\n' "$label"
  fi
}

wait_for_http() {
  local url="$1"
  local name="$2"
  local attempt running
  if ! command -v curl >/dev/null 2>&1; then
    echo "curl is not installed; not checking $url"
    return 0
  fi
  for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      echo "Dashboard is responding at $url"
      return 0
    fi
    if [[ -n "$name" ]]; then
      running="$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null || echo false)"
      if [[ "$running" != "true" ]]; then
        docker logs "$name" >&2 || true
        die "Dashboard container stopped before it accepted requests."
      fi
    fi
    sleep 1
  done
  echo "The dashboard did not respond at $url yet. Check the container logs if the UI does not load."
}

require_python
choose_target
normalize_target

if [[ -z "${DASHBOARD_URL+x}" ]]; then
  if ! interactive; then
    die "DASHBOARD_URL is required."
  fi
  cat <<'EOF'

Dashboard URL
Agents call this address. It is stored as dashboardUrl and filled into the
Add Cluster helm command. A pod cannot use 127.0.0.1. Examples:
  https://dashboard.example.com
  http://172.19.0.1:8080
EOF
  prompt_line DASHBOARD_URL "Dashboard URL"
fi
[[ -n "${DASHBOARD_URL}" ]] || die "DASHBOARD_URL is required."
normalized="$(canonical_url "$DASHBOARD_URL")" || exit 1
DASHBOARD_URL="$normalized"
warn_dashboard_url

if [[ "$TARGET" == "compose" || "$TARGET" == "docker" ]]; then
  prompt_line DASHBOARD_PORT "Host port" "8080"
else
  DASHBOARD_PORT="${DASHBOARD_PORT:-8080}"
fi
validate_port

if [[ "$TARGET" == "docker" || "$TARGET" == "kubernetes" ]]; then
  prompt_line KUBEPULSE_DASHBOARD_IMAGE "Dashboard image" "dhruvmistry200/kubepulse-dashboard:latest"
else
  KUBEPULSE_DASHBOARD_IMAGE="${KUBEPULSE_DASHBOARD_IMAGE:-dhruvmistry200/kubepulse-dashboard:latest}"
fi
validate_image "Dashboard image" "$KUBEPULSE_DASHBOARD_IMAGE"

if [[ "$TARGET" == "kubernetes" ]]; then
  prompt_line KUBEPULSE_NAMESPACE "Kubernetes namespace" "kubepulse"
  prompt_line KUBEPULSE_SERVICE_TYPE "Service type (ClusterIP, NodePort, LoadBalancer)" "ClusterIP"
  prompt_line KUBEPULSE_STORAGE_CLASS "Storage class (Enter for the cluster default)" ""
  validate_dns_label "Namespace" "$KUBEPULSE_NAMESPACE"
  case "$KUBEPULSE_SERVICE_TYPE" in
    ClusterIP|NodePort|LoadBalancer) ;;
    *) die "Service type must be ClusterIP, NodePort, or LoadBalancer." ;;
  esac
  validate_storage_class
else
  KUBEPULSE_NAMESPACE="${KUBEPULSE_NAMESPACE:-kubepulse}"
  KUBEPULSE_SERVICE_TYPE="${KUBEPULSE_SERVICE_TYPE:-ClusterIP}"
  KUBEPULSE_STORAGE_CLASS="${KUBEPULSE_STORAGE_CLASS:-}"
fi

GENERATED_ADMIN=0
GENERATED_VIEWER=0
if [[ -z "${ADMIN_KEY+x}" ]]; then
  if interactive; then
    prompt_secret ADMIN_KEY "Admin key (Enter to generate one)"
  fi
  if [[ -z "${ADMIN_KEY:-}" ]]; then
    ADMIN_KEY="$(new_key)"
    GENERATED_ADMIN=1
  fi
fi
validate_token "ADMIN_KEY" "$ADMIN_KEY" 1

if [[ -z "${VIEWER_KEY+x}" ]]; then
  if interactive; then
    prompt_secret VIEWER_KEY "Viewer key (Enter to generate one, or - to skip)"
  fi
  if [[ "${VIEWER_KEY:-}" == "-" ]]; then
    VIEWER_KEY=""
  elif [[ -z "${VIEWER_KEY:-}" ]]; then
    VIEWER_KEY="$(new_key)"
    GENERATED_VIEWER=1
  fi
fi
validate_token "VIEWER_KEY" "${VIEWER_KEY:-}" 0

if [[ -z "${SECRET_KEY+x}" ]]; then
  if interactive; then
    prompt_secret SECRET_KEY "Legacy heartbeat key (Enter to skip)"
  fi
  SECRET_KEY="${SECRET_KEY:-}"
fi
validate_token "SECRET_KEY" "${SECRET_KEY:-}" 0

prompt_line HELM_REPO_URL "Helm repo URL" "https://dhruvmistry2000.github.io/kubepulse"
normalized="$(canonical_url "$HELM_REPO_URL")" || exit 1
HELM_REPO_URL="$normalized"
prompt_line HELM_CHART_NAME "Helm chart name" "k8s-dashboard-agent"
prompt_line AGENT_IMAGE "Agent image for the Add Cluster command" "dhruvmistry200/kubepulse-agent:latest"
validate_image "Agent image" "$AGENT_IMAGE"
if [[ ! "$HELM_CHART_NAME" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]]; then
  die "Helm chart name must be a DNS label."
fi

STORAGE_BACKEND=file
STORAGE_PATH=/data/kubepulse/registry.json
HISTORY_PATH=/data/kubepulse/history.json
CONTAINER_NAME="${KUBEPULSE_CONTAINER_NAME:-kubepulse-dashboard}"
if [[ ! "$CONTAINER_NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]]; then
  die "KUBEPULSE_CONTAINER_NAME must be a container name."
fi

export ADMIN_KEY VIEWER_KEY SECRET_KEY DASHBOARD_URL DASHBOARD_PORT
export HELM_REPO_URL HELM_CHART_NAME AGENT_IMAGE
export STORAGE_BACKEND STORAGE_PATH HISTORY_PATH

ENV_FILE="${KUBEPULSE_ENV_FILE:-$(default_env_file)}"
MANIFEST_FILE="${KUBEPULSE_MANIFEST_FILE:-${HOME}/.config/kubepulse/dashboard.yaml}"

if [[ "$TARGET" == "compose" && ! -f "$ROOT/compose.yaml" ]]; then
  die "compose.yaml was not found at $ROOT. Run this script from a kubepulse checkout, or use --target docker."
fi

echo
echo "Dashboard URL (agents): $DASHBOARD_URL"
echo "Target: $TARGET"
case "$TARGET" in
  compose)
    echo "Browser on this machine: http://127.0.0.1:${DASHBOARD_PORT}"
    echo "Build: dashboard/ via Docker Compose"
    ;;
  docker)
    echo "Browser on this machine: http://127.0.0.1:${DASHBOARD_PORT}"
    echo "Image: $KUBEPULSE_DASHBOARD_IMAGE"
    ;;
  kubernetes)
    echo "Namespace: $KUBEPULSE_NAMESPACE"
    echo "Service type: $KUBEPULSE_SERVICE_TYPE"
    echo "Image: $KUBEPULSE_DASHBOARD_IMAGE"
    if command -v kubectl >/dev/null 2>&1; then
      echo "Kubernetes context: $(kubectl config current-context 2>/dev/null || echo unknown)"
    fi
    ;;
esac
echo "Env file: $ENV_FILE"
echo "One replica. A second copy would miss registration tokens and overwrite the registry."

if interactive; then
  local_ok=""
  read -r -p "Deploy now? [Y/n] " local_ok
  case "$local_ok" in
    ""|y|Y|yes|YES) ;;
    *)
      echo "Cancelled. No files were written."
      exit 0
      ;;
  esac
fi

case "$TARGET" in
  compose)
    write_env_file "$ENV_FILE" \
      ADMIN_KEY,VIEWER_KEY,SECRET_KEY,DASHBOARD_URL,DASHBOARD_PORT,HELM_REPO_URL,HELM_CHART_NAME,AGENT_IMAGE
    ;;
  docker|kubernetes)
    write_env_file "$ENV_FILE" \
      ADMIN_KEY,VIEWER_KEY,SECRET_KEY,DASHBOARD_URL,HELM_REPO_URL,HELM_CHART_NAME,AGENT_IMAGE,STORAGE_BACKEND,STORAGE_PATH,HISTORY_PATH
    ;;
esac

if [[ "$TARGET" == "kubernetes" ]]; then
  write_manifest "$MANIFEST_FILE"
fi

echo
key_status "Admin key" "$GENERATED_ADMIN" "$ADMIN_KEY"
key_status "Viewer key" "$GENERATED_VIEWER" "$VIEWER_KEY"
if [[ -n "$SECRET_KEY" ]]; then
  echo "Legacy heartbeat key: saved in the env file"
fi

COMPOSE_CMD=(docker compose -f "$ROOT/compose.yaml" --project-directory "$ROOT" --env-file "$ENV_FILE" up -d --build)
DOCKER_CMD=(docker run -d --name "$CONTAINER_NAME" --restart unless-stopped -p "${DASHBOARD_PORT}:8080" --env-file "$ENV_FILE" -v kubepulse-dashboard-data:/data/kubepulse "$KUBEPULSE_DASHBOARD_IMAGE")
KUBECTL_SECRET=(kubectl -n "$KUBEPULSE_NAMESPACE" create secret generic kubepulse-dashboard --from-env-file="$ENV_FILE" --dry-run=client -o yaml)

if [[ "$DRY_RUN" == "1" ]]; then
  echo
  echo "Dry run only. Nothing was started."
  case "$TARGET" in
    compose) echo "Would run: ${COMPOSE_CMD[*]}" ;;
    docker) echo "Would run: ${DOCKER_CMD[*]}" ;;
    kubernetes)
      echo "Manifest: $MANIFEST_FILE"
      echo "Would run: kubectl apply -f $MANIFEST_FILE"
      echo "Would run: ${KUBECTL_SECRET[*]} | kubectl apply -f -"
      ;;
  esac
  exit 0
fi

case "$TARGET" in
  compose)
    command -v docker >/dev/null 2>&1 || die "docker is required."
    docker compose version >/dev/null 2>&1 || die "docker compose is required."
    "${COMPOSE_CMD[@]}"
    wait_for_http "http://127.0.0.1:${DASHBOARD_PORT}/" ""
    ;;
  docker)
    command -v docker >/dev/null 2>&1 || die "docker is required."
    if docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
      if [[ "$FORCE" != "1" ]]; then
        die "Container $CONTAINER_NAME already exists. Re-run with --force to replace it."
      fi
      docker rm -f "$CONTAINER_NAME" >/dev/null
    fi
    "${DOCKER_CMD[@]}"
    wait_for_http "http://127.0.0.1:${DASHBOARD_PORT}/" "$CONTAINER_NAME"
    ;;
  kubernetes)
    command -v kubectl >/dev/null 2>&1 || die "kubectl is required."
    kubectl create namespace "$KUBEPULSE_NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
    "${KUBECTL_SECRET[@]}" | kubectl apply -f -
    kubectl apply -f "$MANIFEST_FILE"
    kubectl -n "$KUBEPULSE_NAMESPACE" rollout status deployment/kubepulse-dashboard --timeout=180s
    echo "Service:"
    kubectl -n "$KUBEPULSE_NAMESPACE" get service kubepulse-dashboard
    case "$KUBEPULSE_SERVICE_TYPE" in
      ClusterIP)
        echo "On this machine: kubectl -n $KUBEPULSE_NAMESPACE port-forward svc/kubepulse-dashboard 8080:8080"
        ;;
      LoadBalancer)
        echo "Wait until the service has an external address. Agents must use the dashboard URL you entered."
        ;;
      NodePort)
        echo "Open the node address on the service nodePort. Agents must use the dashboard URL you entered."
        ;;
    esac
    ;;
esac

echo
echo "Open the UI and enter the admin key. Add Cluster copies a helm command that uses $DASHBOARD_URL."
