"""
K8s Dashboard - Central hub. Agents PUSH data TO this dashboard.

- On startup: read or generate SECRET_KEY; in-memory stores for clusters and pending tokens.
- POST /api/register: agent registers with one-time token, receives SECRET_KEY.
- POST /api/heartbeat: agent sends cluster data (X-Secret-Key).
- GET /api/clusters, GET /api/cluster/<name>/*: browser and UI.
- POST /api/generate-token: admin generates one-time registration token (ADMIN_KEY).
"""

import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone, timedelta

from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

# --- Config ---
SECRET_KEY = (os.environ.get("SECRET_KEY") or "").strip()
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    print("INFO: Generated SECRET_KEY: %s  — set this as SECRET_KEY env var to persist across restarts" % SECRET_KEY, flush=True)

ADMIN_KEY = (os.environ.get("ADMIN_KEY") or "").strip() or SECRET_KEY
DASHBOARD_URL = (os.environ.get("DASHBOARD_URL") or "").strip()
AGENT_IMAGE = os.environ.get(
    "AGENT_IMAGE",
    "dhruvmistry200/kubepulse-agent:latest",
)

# In-memory store: cluster_name -> { registered_at, last_seen, data, agent_version }
clusters = {}
_clusters_lock = threading.Lock()

# Pending one-time registration tokens: token_hex -> { cluster_name, created_at, expires_at, used }
pending_tokens = {}
_pending_tokens_lock = threading.Lock()
TOKEN_EXPIRY_SECONDS = 600  # 10 minutes
CLEANUP_INTERVAL = 60
HEALTHY_SECONDS = 90


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


def _require_admin(f):
    """Decorator: require X-Admin-Key header to match ADMIN_KEY."""
    from functools import wraps
    @wraps(f)
    def wrapped(*args, **kwargs):
        key = request.headers.get("X-Admin-Key")
        if not key or key != ADMIN_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapped


def _cluster_sanitized(name):
    """Validate cluster name: alphanumeric, hyphens, underscores, max 40 chars."""
    if not name or len(name) > 40:
        return False
    return bool(re.match(r"^[a-zA-Z0-9\-_]+$", name))


# --- Routes ---

@app.route("/")
def index():
    return render_template("index.html")


# --- Registration (agent sends one-time token) ---

@app.route("/api/register", methods=["POST"])
def api_register():
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
        cluster_name = entry.get("cluster_name", "").strip()
        if not _cluster_sanitized(cluster_name):
            return jsonify({"error": "Invalid cluster name"}), 400
        entry["used"] = True
        # Register cluster
        now = _now()
        with _clusters_lock:
            if cluster_name in clusters:
                return jsonify({"error": "Cluster name already registered"}), 409
            body = request.get_json(silent=True) or {}
            clusters[cluster_name] = {
                "registered_at": now,
                "last_seen": now,
                "data": None,
                "agent_version": body.get("agent_version", "?"),
            }
        print("INFO: Cluster '%s' successfully registered using registration token" % cluster_name, flush=True)
        return jsonify({
            "status": "registered",
            "cluster_name": cluster_name,
            "secret_key": SECRET_KEY,
        }), 200


@app.route("/api/heartbeat", methods=["POST"])
def api_heartbeat():
    key = request.headers.get("X-Secret-Key", "").strip()
    if not key or key != SECRET_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    cluster_name = (body.get("cluster_name") or "").strip()
    if not cluster_name:
        return jsonify({"error": "Missing cluster_name"}), 400
    now = _now()
    with _clusters_lock:
        if cluster_name not in clusters:
            # Allow heartbeat for not-yet-registered (e.g. agent had SECRET_KEY from previous run)
            clusters[cluster_name] = {
                "registered_at": now,
                "last_seen": now,
                "data": None,
                "agent_version": body.get("agent_version", "?"),
            }
        clusters[cluster_name]["last_seen"] = now
        clusters[cluster_name]["data"] = body.get("data")
    return jsonify({"status": "ok"}), 200


# --- Token generation (admin only) ---

@app.route("/api/generate-token", methods=["POST"])
@_require_admin
def api_generate_token():
    body = request.get_json(silent=True) or {}
    cluster_name = (body.get("cluster_name") or "").strip()
    if not _cluster_sanitized(cluster_name):
        return jsonify({"error": "Invalid cluster name. Use only letters, numbers, hyphens and underscores, max 40 characters."}), 400
    with _clusters_lock:
        if cluster_name in clusters:
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
    cluster_name = (cluster_name or "").strip()
    with _clusters_lock:
        if cluster_name not in clusters:
            return jsonify({"error": "Cluster not found"}), 404
        del clusters[cluster_name]

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
def api_config():
    """Public config for UI (e.g. whether DASHBOARD_URL is set for helm command)."""
    return jsonify({"dashboardUrl": DASHBOARD_URL or ""}), 200


@app.route("/api/clusters")
def api_clusters():
    with _clusters_lock:
        now = _now()
        result = []
        for name, c in clusters.items():
            last_seen = c.get("last_seen")
            last_seen_ts = last_seen.timestamp() if last_seen else 0
            age_seconds = (now - last_seen).total_seconds() if last_seen else 999999
            result.append({
                "name": name,
                "last_seen": last_seen.isoformat() if last_seen else None,
                "agent_version": c.get("agent_version"),
                "registered_at": c["registered_at"].isoformat() if c.get("registered_at") else None,
                "is_healthy": age_seconds <= HEALTHY_SECONDS,
                "summary": (c.get("data") or {}).get("summary"),
            })
    return jsonify(result), 200


def _cluster_data(cluster_name, key):
    """Get a section of cluster data; return 404 if cluster missing."""
    with _clusters_lock:
        if cluster_name not in clusters:
            return None, 404
        c = clusters[cluster_name]
        data = c.get("data")
        if data is None:
            return {"items": [], "message": "Waiting for first heartbeat..."}, 200
        if key == "summary":
            return data.get("summary") or {}, 200
        items = data.get(key)
        if items is None:
            return {"items": [], "message": "No data"}, 200
        return {"items": items}, 200


@app.route("/api/cluster/<cluster_name>/nodes")
def api_cluster_nodes(cluster_name):
    out, status = _cluster_data(cluster_name, "nodes")
    if out is None:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify(out), status


@app.route("/api/cluster/<cluster_name>/pods")
def api_cluster_pods(cluster_name):
    out, status = _cluster_data(cluster_name, "pods")
    if out is None:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify(out), status


@app.route("/api/cluster/<cluster_name>/deployments")
def api_cluster_deployments(cluster_name):
    out, status = _cluster_data(cluster_name, "deployments")
    if out is None:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify(out), status


@app.route("/api/cluster/<cluster_name>/services")
def api_cluster_services(cluster_name):
    out, status = _cluster_data(cluster_name, "services")
    if out is None:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify(out), status


@app.route("/api/cluster/<cluster_name>/ingresses")
def api_cluster_ingresses(cluster_name):
    out, status = _cluster_data(cluster_name, "ingresses")
    if out is None:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify(out), status


@app.route("/api/cluster/<cluster_name>/namespaces")
def api_cluster_namespaces(cluster_name):
    out, status = _cluster_data(cluster_name, "namespaces")
    if out is None:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify(out), status


@app.route("/api/cluster/<cluster_name>/configmaps")
def api_cluster_configmaps(cluster_name):
    out, status = _cluster_data(cluster_name, "configmaps")
    if out is None:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify(out), status


@app.route("/api/cluster/<cluster_name>/summary")
def api_cluster_summary(cluster_name):
    out, status = _cluster_data(cluster_name, "summary")
    if out is None:
        return jsonify({"error": "Cluster not found"}), 404
    return jsonify(out), status


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
