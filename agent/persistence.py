"""
Agent credential persistence for durable registration across pod restarts.

Modes (PERSISTENCE_MODE env):
  - secret (default): read/write a namespaced Kubernetes Secret
  - file: read/write JSON at PERSISTENCE_FILE_PATH (requires a mounted volume to survive redeploys)
  - memory: no persistence (testing only)
"""

import base64
import json
import logging
import os

logger = logging.getLogger(__name__)

PERSISTENCE_MODE = (os.environ.get("PERSISTENCE_MODE") or "secret").strip().lower()
STATE_SECRET_NAME = (os.environ.get("STATE_SECRET_NAME") or "kubepulse-agent-state").strip()
PERSISTENCE_FILE_PATH = (
    os.environ.get("PERSISTENCE_FILE_PATH") or "/var/lib/kubepulse/credential.json"
).strip()

CREDENTIAL_KEY = "clusterCredential"
CLUSTER_NAME_KEY = "clusterName"


def _read_file_state():
    """Load credential from a JSON file."""
    try:
        with open(PERSISTENCE_FILE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        credential = (data.get(CREDENTIAL_KEY) or "").strip()
        cluster_name = (data.get(CLUSTER_NAME_KEY) or "").strip()
        if credential:
            return credential, cluster_name
    except FileNotFoundError:
        return None, None
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read credential file %s: %s", PERSISTENCE_FILE_PATH, exc)
    return None, None


def _write_file_state(credential, cluster_name):
    """Persist credential to a JSON file."""
    directory = os.path.dirname(PERSISTENCE_FILE_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    payload = {CREDENTIAL_KEY: credential, CLUSTER_NAME_KEY: cluster_name}
    tmp_path = PERSISTENCE_FILE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp_path, PERSISTENCE_FILE_PATH)


def _read_secret_state(core_v1, namespace):
    """Load credential from a Kubernetes Secret."""
    if core_v1 is None:
        return None, None
    try:
        secret = core_v1.read_namespaced_secret(STATE_SECRET_NAME, namespace)
        data = secret.data or {}
        cred_b64 = data.get(CREDENTIAL_KEY)
        name_b64 = data.get(CLUSTER_NAME_KEY)
        if not cred_b64:
            return None, None
        credential = base64.b64decode(cred_b64).decode("utf-8").strip()
        cluster_name = ""
        if name_b64:
            cluster_name = base64.b64decode(name_b64).decode("utf-8").strip()
        if credential:
            return credential, cluster_name
    except Exception as exc:
        # Secret not found is expected on first boot.
        if getattr(exc, "status", None) != 404:
            logger.warning("Could not read state secret %s: %s", STATE_SECRET_NAME, exc)
    return None, None


def _write_secret_state(core_v1, namespace, credential, cluster_name):
    """Create or update the state Secret with the cluster credential."""
    if core_v1 is None:
        raise RuntimeError("Kubernetes client not available for secret persistence")

    encoded = {
        CREDENTIAL_KEY: base64.b64encode(credential.encode("utf-8")).decode("ascii"),
        CLUSTER_NAME_KEY: base64.b64encode(cluster_name.encode("utf-8")).decode("ascii"),
    }
    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": STATE_SECRET_NAME},
        "type": "Opaque",
        "data": encoded,
    }
    try:
        core_v1.read_namespaced_secret(STATE_SECRET_NAME, namespace)
        core_v1.patch_namespaced_secret(STATE_SECRET_NAME, namespace, body)
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            core_v1.create_namespaced_secret(namespace, body)
        else:
            raise


def load_credential(core_v1=None, namespace=None, expected_cluster_name=None):
    """
    Attempt to load a stored per-cluster credential.

    Returns (credential, cluster_name) or (None, None) if not found.
    """
    if PERSISTENCE_MODE == "memory":
        return None, None

    credential = None
    stored_cluster_name = None

    if PERSISTENCE_MODE == "file":
        credential, stored_cluster_name = _read_file_state()
    elif PERSISTENCE_MODE == "secret":
        if not namespace:
            namespace = os.environ.get("POD_NAMESPACE")
            if not namespace:
                try:
                    with open(
                        "/var/run/secrets/kubernetes.io/serviceaccount/namespace",
                        encoding="utf-8",
                    ) as fh:
                        namespace = fh.read().strip()
                except OSError:
                    logger.warning("Could not determine pod namespace for secret persistence")
                    return None, None
        credential, stored_cluster_name = _read_secret_state(core_v1, namespace)
    else:
        logger.warning("Unknown PERSISTENCE_MODE=%s; treating as memory", PERSISTENCE_MODE)
        return None, None

    if not credential:
        return None, None

    if expected_cluster_name and stored_cluster_name and stored_cluster_name != expected_cluster_name:
        logger.warning(
            "Stored cluster name '%s' does not match CLUSTER_NAME '%s'; ignoring stored credential",
            stored_cluster_name,
            expected_cluster_name,
        )
        return None, None

    return credential, stored_cluster_name or expected_cluster_name


def save_credential(credential, cluster_name, core_v1=None, namespace=None):
    """Persist the per-cluster credential after successful registration."""
    if PERSISTENCE_MODE == "memory":
        return
    if not credential:
        return

    if PERSISTENCE_MODE == "file":
        _write_file_state(credential, cluster_name)
        logger.info("Saved cluster credential to file %s", PERSISTENCE_FILE_PATH)
        return

    if PERSISTENCE_MODE == "secret":
        if not namespace:
            namespace = os.environ.get("POD_NAMESPACE")
            if not namespace:
                with open(
                    "/var/run/secrets/kubernetes.io/serviceaccount/namespace",
                    encoding="utf-8",
                ) as fh:
                    namespace = fh.read().strip()
        _write_secret_state(core_v1, namespace, credential, cluster_name)
        logger.info("Saved cluster credential to secret %s", STATE_SECRET_NAME)
        return

    logger.warning("Unknown PERSISTENCE_MODE=%s; credential not persisted", PERSISTENCE_MODE)
