"""
K8s Dashboard - Central hub. Agents PUSH data TO this dashboard.

- On startup: require ADMIN_KEY (unless debug). SECRET_KEY is legacy heartbeats only.
- POST /api/register: agent registers with one-time token, receives per-cluster credential.
- POST /api/heartbeat: agent sends cluster data (X-Secret-Key = per-cluster credential).
- GET /api/*: browser and UI, with the viewer key or ADMIN_KEY.
- POST /api/generate-token: admin generates one-time registration token (ADMIN_KEY).
"""

import gzip
import os
import secrets
import threading
import time
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Flask, render_template, jsonify, request, make_response

from storage import RegistryStore, secrets_equal
from common.cluster_name import normalize_cluster_name
from history import HistoryStore
from pricing import PriceLookup, enrich_nodes_with_cost, compute_cost_summary

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH") or (2 * 1024 * 1024))

_register_hits = {}
_register_lock = threading.Lock()
REGISTER_LIMIT = 10
REGISTER_WINDOW_SECONDS = 60


@app.before_request
def _gunzip_request():
    if (request.headers.get("Content-Encoding") or "").lower() != "gzip":
        return None
    try:
        request._cached_data = gzip.decompress(request.get_data(cache=False))
    except OSError:
        return jsonify({"error": "invalid gzip body"}), 400
    request.environ["CONTENT_LENGTH"] = len(request._cached_data)
    return None


def _register_allowed():
    ip = request.remote_addr or "unknown"
    now = time.time()
    with _register_lock:
        hits = [stamp for stamp in _register_hits.get(ip, []) if now - stamp < REGISTER_WINDOW_SECONDS]
        if len(hits) >= REGISTER_LIMIT:
            _register_hits[ip] = hits
            return False
        hits.append(now)
        _register_hits[ip] = hits
        return True

def _env_flag(name):
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes")


# --- Config ---
# SECRET_KEY is the deprecated heartbeat credential for clusters registered
# before per-cluster credentials. It is never generated, printed, or used as ADMIN_KEY.
SECRET_KEY = (os.environ.get("SECRET_KEY") or "").strip()
ADMIN_KEY = (os.environ.get("ADMIN_KEY") or "").strip()
VIEWER_KEY = (os.environ.get("VIEWER_KEY") or "").strip()
DEBUG_MODE = _env_flag("FLASK_DEBUG") or _env_flag("DEBUG")
if not ADMIN_KEY and not DEBUG_MODE:
    raise SystemExit("ADMIN_KEY is required. Refusing to start.")
DASHBOARD_URL = (os.environ.get("DASHBOARD_URL") or "").strip()
AGENT_IMAGE = os.environ.get(
    "AGENT_IMAGE",
    "dhruvmistry200/kubepulse-agent:latest",
)

# Persistent cluster registry (credentials hashed at rest)
registry = RegistryStore()
history = HistoryStore()
_price_lookup = PriceLookup()

# In-memory live metrics: cluster_name -> latest heartbeat data payload
live_data = {}
_clusters_lock = threading.Lock()

# Pending one-time registration tokens: token_hex -> { cluster_name, created_at, expires_at, used }
pending_tokens = {}
_pending_tokens_lock = threading.Lock()
TOKEN_EXPIRY_SECONDS = 600  # 10 minutes
CLEANUP_INTERVAL = 60
HEALTHY_SECONDS = 90
_legacy_key_warned = False


def _now():
    return datetime.now(timezone.utc)


def _cleanup_pending_tokens():
    """Remove expired and used tokens."""
    with _pending_tokens_lock:
        to_remove = [
            t for t, v in pending_tokens.items()
            if v.get("used") or (v.get("expires_at") and v["expires_at"] <= _now())
        ]
        for t in to_remove:
            del pending_tokens[t]


def _cleanup_loop():
    """Background: cleanup pending tokens every CLEANUP_INTERVAL seconds."""
    while True:
        time.sleep(CLEANUP_INTERVAL)
        _cleanup_pending_tokens()


# Start cleanup daemon
_cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True)
_cleanup_thread.start()


def _presented_secrets():
    values = []
    for header in ("X-Api-Key", "X-Admin-Key", "X-Viewer-Key"):
        value = (request.headers.get(header) or "").strip()
        if value:
            values.append(value)
    return values


def _has_admin():
    return any(secrets_equal(value, ADMIN_KEY) for value in _presented_secrets())


def _has_reader():
    if _has_admin():
        return True
    return any(secrets_equal(value, VIEWER_KEY) for value in _presented_secrets())


def _require_admin(f):
    """Decorator: require the admin key. The viewer key is not enough."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not _has_admin():
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapped


def _require_reader(f):
    """Decorator: require the viewer key or the admin key."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not _has_reader():
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapped


def _set_token_used(token, used):
    with _pending_tokens_lock:
        entry = pending_tokens.get(token)
        if entry is not None:
            entry["used"] = used


def _legacy_key_authorizes(cluster_name, key):
    """Deprecated path for clusters registered before per-cluster credentials.

    Pending clusters are excluded so the global key cannot finish registration.
    """
    global _legacy_key_warned
    if not secrets_equal(key, SECRET_KEY) or not registry.is_active(cluster_name):
        return False
    if not _legacy_key_warned:
        _legacy_key_warned = True
        print(
            "WARNING: accepted a heartbeat with the legacy SECRET_KEY. "
            "This path is deprecated; use the per-cluster credential.",
            flush=True,
        )
    return True


# --- Routes ---

def _sanitize_live_payload(data):
    """Drop ConfigMap values and annotation values before they are stored."""
    if not isinstance(data, dict):
        return {}
    for pod in data.get("pods") or []:
        if isinstance(pod, dict) and isinstance(pod.get("annotations"), dict):
            pod["annotations"] = {str(key): "[redacted]" for key in pod["annotations"]}
    for configmap in data.get("configmaps") or []:
        if not isinstance(configmap, dict):
            continue
        configmap.pop("data", None)
        configmap.pop("binaryData", None)
    return data


def _content_security_policy(nonce):
    return (
        "default-src 'self'; "
        f"script-src 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )


@app.route("/")
def index():
    nonce = secrets.token_urlsafe(16)
    response = make_response(render_template("index.html", csp_nonce=nonce))
    response.headers["Content-Security-Policy"] = _content_security_policy(nonce)
    return response


# --- Registration (agent sends one-time token) ---

@app.route("/api/register", methods=["POST"])
def api_register():
    if not _register_allowed():
        return jsonify({"error": "Too many registration attempts"}), 429
    token = request.headers.get("X-Registration-Token", "").strip()
    if not token:
        return jsonify({"error": "Missing X-Registration-Token header"}), 401
    with _pending_tokens_lock:
        entry = pending_tokens.get(token)
        if not entry:
            return jsonify({"error": "Invalid registration token"}), 401
        if entry.get("expires_at") and entry["expires_at"] <= _now():
            return jsonify({"error": "Token expired. Generate a new token from the dashboard."}), 410
        if entry.get("used"):
            return jsonify({"error": "Token already used. Generate a new token from the dashboard."}), 401
        raw_name = entry.get("cluster_name", "")
        cluster_name = normalize_cluster_name(raw_name)
        if not cluster_name:
            return jsonify({"error": "Invalid cluster name"}), 400
    stored = registry.stored_name(cluster_name)
    if stored and registry.is_active(stored):
        return jsonify({"error": "Cluster name already registered"}), 409
    target = stored or cluster_name
    body = request.get_json(silent=True) or {}
    with _pending_tokens_lock:
        entry = pending_tokens.get(token)
        if not entry or entry.get("used"):
            return jsonify({"error": "Token already used. Generate a new token from the dashboard."}), 401
        if entry.get("expires_at") and entry["expires_at"] <= _now():
            return jsonify({"error": "Token expired. Generate a new token from the dashboard."}), 410
        entry["used"] = True
    try:
        _, credential = registry.register_cluster(
            target,
            agent_version=body.get("agent_version", "?"),
        )
    except ValueError:
        return jsonify({"error": "Cluster name already registered"}), 409
    except Exception:
        _set_token_used(token, False)
        print(
            "ERROR: failed to persist registration for cluster '%s'" % target,
            flush=True,
        )
        return jsonify({"error": "Failed to store registration"}), 500
    print(
        "INFO: Cluster '%s' registered pending first heartbeat" % target,
        flush=True,
    )
    return jsonify({
        "status": "registered",
        "cluster_name": target,
        "cluster_credential": credential,
        # Backward-compatible alias for older agents during rollout.
        "secret_key": credential,
    }), 200


@app.route("/api/heartbeat", methods=["POST"])
def api_heartbeat():
    key = request.headers.get("X-Secret-Key", "").strip()
    if not key:
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get("cluster_name"), str):
        return jsonify({"error": "Missing cluster_name"}), 400
    if body.get("data") is not None and not isinstance(body.get("data"), dict):
        return jsonify({"error": "Invalid payload"}), 400
    raw_name = body.get("cluster_name").strip()
    if not raw_name:
        return jsonify({"error": "Missing cluster_name"}), 400
    cluster_name = registry.resolve_name(raw_name)
    if not cluster_name:
        return jsonify({"error": "Invalid cluster name"}), 400
    if not registry.cluster_exists(cluster_name):
        return jsonify({"error": "Unauthorized"}), 401

    authorized = registry.verify_credential(cluster_name, key)
    # Legacy fallback: accept global SECRET_KEY for clusters registered before per-cluster creds.
    if not authorized and _legacy_key_authorizes(cluster_name, key):
        authorized = True
    if not authorized:
        return jsonify({"error": "Unauthorized"}), 401

    agent_version = body.get("agent_version")
    registry.touch_cluster(cluster_name, agent_version=agent_version)
    data = _sanitize_live_payload(body.get("data") or {})
    authoritative = data.get("nodesAuthoritative", True) is not False
    if authoritative:
        nodes = data.get("nodes") or []
        enrich_nodes_with_cost(nodes, _price_lookup)
        data["nodes"] = nodes
        data["cost"] = compute_cost_summary(nodes)
        with _clusters_lock:
            live_data[cluster_name] = data
        history.record_heartbeat(
            cluster_name, nodes, data.get("cost"), nodes_authoritative=True
        )
    else:
        with _clusters_lock:
            previous = live_data.get(cluster_name) or {}
            data["nodes"] = previous.get("nodes") or []
            if previous.get("cost") is not None:
                data["cost"] = previous.get("cost")
            live_data[cluster_name] = data
    return jsonify({"status": "ok"}), 200


# --- Token generation (admin only) ---

@app.route("/api/generate-token", methods=["POST"])
@_require_admin
def api_generate_token():
    body = request.get_json(silent=True) or {}
    cluster_name = normalize_cluster_name(body.get("cluster_name"))
    if not cluster_name:
        return jsonify({"error": "Invalid cluster name. Use a DNS-1123 subdomain: lowercase letters, digits, hyphens, and dots, at most 253 characters."}), 400
    stored = registry.stored_name(cluster_name)
    if stored and registry.is_active(stored):
        return jsonify({"error": "Cluster name already registered. Choose a different name."}), 409
    _cleanup_pending_tokens()
    token = secrets.token_hex(32)
    now = _now()
    expires_at = now + timedelta(seconds=TOKEN_EXPIRY_SECONDS)
    with _pending_tokens_lock:
        pending_tokens[token] = {
            "cluster_name": cluster_name,
            "created_at": now,
            "expires_at": expires_at,
            "used": False,
        }
    # Multi-line helm command with each --set on its own line.
    dashboard_url = DASHBOARD_URL or "DASHBOARD_URL_HERE"

    # Split AGENT_IMAGE into repository and tag
    agent_image = AGENT_IMAGE  # e.g. "dhruvmistry200/kubepulse-agent:latest"
    if ":" in agent_image:
        img_repo, img_tag = agent_image.rsplit(":", 1)
    else:
        img_repo = agent_image
        img_tag = "latest"

    # Chart templates copy these values into a Secret. The pod reads that Secret.
    helm_command = (
        "helm install kubepulse-agent ./helm \\\n"
        f"  --set image.repository={img_repo} \\\n"
        f"  --set image.tag={img_tag} \\\n"
        f"  --set registrationToken={token} \\\n"
        f"  --set dashboardUrl={dashboard_url} \\\n"
        f"  --set clusterName={cluster_name}"
    )

    print("INFO: Registration token generated for cluster '%s', expires in 10 minutes" % cluster_name, flush=True)
    return jsonify({
        "token": token,
        "cluster_name": cluster_name,
        "expires_at": expires_at.isoformat(),
        "expires_in_seconds": TOKEN_EXPIRY_SECONDS,
        "helm_command": helm_command,
    }), 200


@app.route("/api/cluster/<cluster_name>", methods=["DELETE"])
@_require_admin
def api_delete_cluster(cluster_name):
    """
    Admin-only: delete a cluster from the in-memory store.

    - Removes the cluster from `clusters`
    - Removes any pending tokens created for that cluster name
    """
    requested = (cluster_name or "").strip()
    cluster_name = registry.stored_name(requested)
    if not cluster_name or not registry.delete_cluster(cluster_name):
        return jsonify({"error": "Cluster not found"}), 404

    with _clusters_lock:
        live_data.pop(cluster_name, None)
    history.delete_cluster(cluster_name)

    # Also remove any pending tokens that were generated for this cluster name.
    # (We clean up by cluster_name, not token string, per requirements.)
    with _pending_tokens_lock:
        to_remove = [t for t, v in pending_tokens.items() if (v.get("cluster_name") or "").strip() == cluster_name]
        for t in to_remove:
            del pending_tokens[t]

    print("INFO: Cluster '%s' deleted by admin" % cluster_name, flush=True)
    return jsonify({"status": "deleted", "cluster_name": cluster_name}), 200


@app.route("/api/pending-tokens", methods=["GET"])
@_require_admin
def api_pending_tokens():
    with _pending_tokens_lock:
        now = _now()
        active = []
        for t, v in pending_tokens.items():
            if v.get("used"):
                continue
            if v.get("expires_at") and v["expires_at"] <= now:
                continue
            active.append({
                "cluster_name": v.get("cluster_name"),
                "expires_at": v["expires_at"].isoformat() if v.get("expires_at") else None,
                "expires_in_seconds": int((v["expires_at"] - now).total_seconds()) if v.get("expires_at") else None,
            })
    return jsonify(active), 200


# --- Browser / UI API ---

@app.route("/api/config")
@_require_reader
def api_config():
    """Public config for UI (e.g. whether DASHBOARD_URL is set for helm command)."""
    return jsonify({"dashboardUrl": DASHBOARD_URL or ""}), 200


@app.route("/api/clusters")
@_require_reader
def api_clusters():
    now = _now()
    result = []
    for name in registry.list_cluster_names():
        entry = registry.get(name)
        last_seen = _parse_registry_time(entry.get("lastSeen")) if entry else None
        age_seconds = (now - last_seen).total_seconds() if last_seen else 999999
        with _clusters_lock:
            data = live_data.get(name)
        result.append({
            "name": name,
            "last_seen": last_seen.isoformat() if last_seen else None,
            "agent_version": entry.get("agent_version") if entry else None,
            "registered_at": entry.get("createdAt") if entry else None,
            "status": (entry.get("status") if entry else None) or "active",
            "is_healthy": age_seconds <= HEALTHY_SECONDS and (
                (entry.get("status") if entry else None) or "active"
            ) != "pending",
            "summary": (data or {}).get("summary"),
        })
    return jsonify(result), 200


def _parse_registry_time(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _existing_cluster(cluster_name):
    """Resolve a request name to a stored registry key, or None."""
    return registry.stored_name(cluster_name)


def _cluster_data(cluster_name, key):
    """Get a section of cluster data; return 404 if cluster missing."""
    cluster_name = _existing_cluster(cluster_name)
    if not cluster_name:
        return None, 404
    with _clusters_lock:
        data = live_data.get(cluster_name)
    if data is None:
        return {"items": [], "message": "Waiting for first heartbeat..."}, 200
    if key == "summary":
        return data.get("summary") or {}, 200
    items = data.get(key)
    if items is None:
        return {"items": [], "message": "No data"}, 200
    return {"items": items}, 200


@app.route("/api/cluster/<cluster_name>/nodes")
@_require_reader
def api_cluster_nodes(cluster_name):
    out, status = _cluster_data(cluster_name, "nodes")
    if out is None:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify(out), status


@app.route("/api/cluster/<cluster_name>/pods")
@_require_reader
def api_cluster_pods(cluster_name):
    out, status = _cluster_data(cluster_name, "pods")
    if out is None:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify(out), status


@app.route("/api/cluster/<cluster_name>/deployments")
@_require_reader
def api_cluster_deployments(cluster_name):
    out, status = _cluster_data(cluster_name, "deployments")
    if out is None:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify(out), status


@app.route("/api/cluster/<cluster_name>/services")
@_require_reader
def api_cluster_services(cluster_name):
    out, status = _cluster_data(cluster_name, "services")
    if out is None:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify(out), status


@app.route("/api/cluster/<cluster_name>/ingresses")
@_require_reader
def api_cluster_ingresses(cluster_name):
    out, status = _cluster_data(cluster_name, "ingresses")
    if out is None:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify(out), status


@app.route("/api/cluster/<cluster_name>/namespaces")
@_require_reader
def api_cluster_namespaces(cluster_name):
    out, status = _cluster_data(cluster_name, "namespaces")
    if out is None:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify(out), status


@app.route("/api/cluster/<cluster_name>/configmaps")
@_require_reader
def api_cluster_configmaps(cluster_name):
    out, status = _cluster_data(cluster_name, "configmaps")
    if out is None:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify(out), status


@app.route("/api/cluster/<cluster_name>/summary")
@_require_reader
def api_cluster_summary(cluster_name):
    out, status = _cluster_data(cluster_name, "summary")
    if out is None:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify(out), status


@app.route("/api/cluster/<cluster_name>/cost")
@_require_reader
def api_cluster_cost(cluster_name):
    cluster_name = _existing_cluster(cluster_name)
    if not cluster_name:
        return jsonify({"error": "Cluster not found"}), 404
    with _clusters_lock:
        data = live_data.get(cluster_name)
    if not data:
        return jsonify({"message": "Waiting for first heartbeat...", "cost": {}}), 200
    return jsonify({"cost": data.get("cost") or {}}), 200


@app.route("/api/cost")
@_require_reader
def api_fleet_cost():
    with _clusters_lock:
        snapshot = dict(live_data)
    return jsonify(history.get_fleet_cost_from_live(snapshot)), 200


@app.route("/api/cluster/<cluster_name>/scaling")
@_require_reader
def api_cluster_scaling(cluster_name):
    cluster_name = _existing_cluster(cluster_name)
    if not cluster_name:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify({"scaling": history.get_scaling(cluster_name)}), 200


@app.route("/api/scaling")
@_require_reader
def api_fleet_scaling():
    return jsonify({"scaling": history.get_scaling()}), 200


@app.route("/api/retired-nodes")
@_require_reader
def api_retired_nodes():
    cluster = (request.args.get("cluster") or "").strip() or None
    return jsonify({"items": history.get_retired_nodes(cluster)}), 200


@app.route("/api/cluster/<cluster_name>/retired-nodes")
@_require_reader
def api_cluster_retired_nodes(cluster_name):
    cluster_name = _existing_cluster(cluster_name)
    if not cluster_name:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify({"items": history.get_retired_nodes(cluster_name)}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
