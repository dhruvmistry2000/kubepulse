"""
Persistent cluster registry storage for durable registration.

Backends (STORAGE_BACKEND env):
  - file (default): JSON at STORAGE_PATH
  - gcs: object at gs://$GCS_BUCKET/$GCS_OBJECT (required for Cloud Run)
"""

import hashlib
import json
import logging
import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STORAGE_BACKEND = (os.environ.get("STORAGE_BACKEND") or "file").strip().lower()
STORAGE_PATH = (os.environ.get("STORAGE_PATH") or "/data/kubepulse/registry.json").strip()
GCS_BUCKET = (os.environ.get("GCS_BUCKET") or "").strip()
GCS_OBJECT = (os.environ.get("GCS_OBJECT") or "kubepulse/registry.json").strip()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def hash_credential(credential):
    """Return SHA-256 hex digest of a per-cluster credential."""
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


def generate_cluster_credential():
    """Generate a random per-cluster credential (32-byte urlsafe token)."""
    return secrets.token_urlsafe(32)


# Clusters registered before two-phase registration have no status field.
# Treat that as active so existing registries keep accepting heartbeats.
STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"


def cluster_status(entry):
    """Return pending or active. Missing status means an already-active cluster."""
    if not entry:
        return None
    return entry.get("status") or STATUS_ACTIVE


def _parse_iso(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class RegistryStore:
    """Thread-safe persistent cluster registry."""

    def __init__(self):
        self._lock = threading.Lock()
        self._clusters = {}
        self._load()

    def _empty_registry(self):
        return {"version": 1, "clusters": {}}

    def _load_file(self):
        try:
            with open(STORAGE_PATH, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return self._empty_registry()
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load registry from %s: %s", STORAGE_PATH, exc)
            return self._empty_registry()

    def _save_file(self, data):
        directory = os.path.dirname(STORAGE_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=directory or None, prefix=".registry-", suffix=".json"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp_path, STORAGE_PATH)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _load_gcs(self):
        try:
            from google.cloud import storage
        except ImportError as exc:
            raise RuntimeError(
                "google-cloud-storage is required for STORAGE_BACKEND=gcs"
            ) from exc
        if not GCS_BUCKET:
            raise RuntimeError("GCS_BUCKET is required for STORAGE_BACKEND=gcs")
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(GCS_OBJECT)
        if not blob.exists():
            return self._empty_registry()
        raw = blob.download_as_text()
        return json.loads(raw)

    def _save_gcs(self, data):
        try:
            from google.cloud import storage
        except ImportError as exc:
            raise RuntimeError(
                "google-cloud-storage is required for STORAGE_BACKEND=gcs"
            ) from exc
        if not GCS_BUCKET:
            raise RuntimeError("GCS_BUCKET is required for STORAGE_BACKEND=gcs")
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(GCS_OBJECT)
        blob.upload_from_string(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            content_type="application/json",
        )

    def _load(self):
        if STORAGE_BACKEND == "gcs":
            raw = self._load_gcs()
        else:
            raw = self._load_file()
        clusters = raw.get("clusters") or {}
        with self._lock:
            self._clusters = dict(clusters)

    def _persist(self):
        data = {"version": 1, "clusters": self._clusters}
        if STORAGE_BACKEND == "gcs":
            self._save_gcs(data)
        else:
            self._save_file(data)

    def list_cluster_names(self):
        with self._lock:
            return list(self._clusters.keys())

    def get(self, cluster_name):
        with self._lock:
            entry = self._clusters.get(cluster_name)
            return dict(entry) if entry else None

    def cluster_exists(self, cluster_name):
        with self._lock:
            return cluster_name in self._clusters

    def is_active(self, cluster_name):
        """True only after the first successful heartbeat (or a legacy entry)."""
        with self._lock:
            entry = self._clusters.get(cluster_name)
            return cluster_status(entry) == STATUS_ACTIVE

    def register_cluster(self, cluster_name, agent_version="?"):
        """
        Register a cluster and return (entry_dict, plaintext_credential).

        The cluster stays pending until the first authenticated heartbeat.
        Re-registering a pending cluster rotates the credential hash so a
        failed agent save can be retried with a new token. The plaintext
        credential is returned once; only the hash is stored.
        """
        credential = generate_cluster_credential()
        now = _now_iso()
        with self._lock:
            existing = self._clusters.get(cluster_name)
            if existing and cluster_status(existing) == STATUS_ACTIVE:
                raise ValueError("cluster already registered")
            if existing:
                existing["credentialHash"] = hash_credential(credential)
                existing["agent_version"] = agent_version
                existing["status"] = STATUS_PENDING
                self._persist()
                return dict(existing), credential
            entry = {
                "clusterName": cluster_name,
                "credentialHash": hash_credential(credential),
                "createdAt": now,
                "lastSeen": None,
                "agent_version": agent_version,
                "status": STATUS_PENDING,
            }
            self._clusters[cluster_name] = entry
            self._persist()
        return dict(entry), credential

    def verify_credential(self, cluster_name, credential):
        """Return True if credential matches the stored hash for cluster_name."""
        if not credential:
            return False
        with self._lock:
            entry = self._clusters.get(cluster_name)
            if not entry:
                return False
            return entry.get("credentialHash") == hash_credential(credential)

    def touch_cluster(self, cluster_name, agent_version=None):
        """Update lastSeen (and optionally agent_version) for a known cluster."""
        with self._lock:
            entry = self._clusters.get(cluster_name)
            if not entry:
                return False
            entry["lastSeen"] = _now_iso()
            entry["status"] = STATUS_ACTIVE
            if agent_version:
                entry["agent_version"] = agent_version
            self._persist()
            return True

    def delete_cluster(self, cluster_name):
        with self._lock:
            if cluster_name not in self._clusters:
                return False
            del self._clusters[cluster_name]
            self._persist()
            return True

    def get_all_entries(self):
        """Return all registry entries (without credential hashes exposed differently)."""
        with self._lock:
            return {name: dict(entry) for name, entry in self._clusters.items()}

    def to_runtime_cluster(self, cluster_name, live_data=None):
        """Build an in-memory cluster view from registry + optional live metrics."""
        entry = self.get(cluster_name)
        if not entry:
            return None
        return {
            "registered_at": _parse_iso(entry.get("createdAt")),
            "last_seen": _parse_iso(entry.get("lastSeen")),
            "data": live_data,
            "agent_version": entry.get("agent_version", "?"),
        }
