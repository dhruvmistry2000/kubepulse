"""
K8s Dashboard Agent - Runs inside a Kubernetes cluster.

Architecture: Agent PUSHES data TO the dashboard (no inbound LoadBalancer needed).
- On startup: register with dashboard using REGISTRATION_TOKEN (one-time), receive SECRET_KEY.
- Background thread: every 30s collect cluster data and POST to dashboard /api/heartbeat.
- Minimal Flask app only for GET /healthz (liveness/readiness probes).
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify
from kubernetes import client, config
from kubernetes.client.rest import ApiException

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Config from environment ---
DASHBOARD_URL = (os.environ.get("DASHBOARD_URL") or "").rstrip("/")
CLUSTER_NAME_RAW = os.environ.get("CLUSTER_NAME", "default")
# Sanitize cluster name for URL safety: lowercase, replace spaces/special with hyphen
CLUSTER_NAME = re.sub(r"[^a-z0-9\-_]", "-", CLUSTER_NAME_RAW.lower().strip()).strip("-") or "default"
REGISTRATION_TOKEN = (os.environ.get("REGISTRATION_TOKEN") or "").strip()
SECRET_KEY = (os.environ.get("SECRET_KEY") or "").strip()

# After successful registration, we store the dashboard's SECRET_KEY here (in memory only)
_agent_secret_key = SECRET_KEY if SECRET_KEY else None

REQUEST_TIMEOUT = 10
HEARTBEAT_INTERVAL = 30
REGISTER_RETRY_INTERVAL = 15
REGISTER_MAX_ATTEMPTS = 5

# --- Kubernetes client ---
K8S_CONFIG_ERROR = None
core_v1 = apps_v1 = networking_v1 = custom_objects = None
METRICS_AVAILABLE = False
try:
    config.load_incluster_config()
    core_v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    networking_v1 = client.NetworkingV1Api()
    custom_objects = client.CustomObjectsApi()
    METRICS_AVAILABLE = True
except Exception as e:
    K8S_CONFIG_ERROR = str(e)
    logger.warning("Kubernetes in-cluster config failed: %s", e)


def _session():
    """HTTP session with timeout for dashboard calls."""
    s = requests.Session()
    s.request = lambda *args, **kwargs: requests.Session.request(s, *args, timeout=REQUEST_TIMEOUT, **kwargs)
    return s


def get_node_metrics():
    """Fetch node metrics from metrics-server. Returns dict node_name -> {cpu, memory} or {}."""
    if not METRICS_AVAILABLE or custom_objects is None:
        return {}
    try:
        result = custom_objects.list_cluster_custom_object(
            group="metrics.k8s.io", version="v1beta1", plural="nodes"
        )
        out = {}
        for item in result.get("items", []):
            name = item.get("metadata", {}).get("name")
            usage = item.get("usage", {})
            out[name] = {"cpu": usage.get("cpu"), "memory": usage.get("memory")}
        return out
    except Exception:
        return {}


def get_pod_metrics(custom_objects):
    # Returns dict: (namespace, pod_name) -> {cpu: str, memory: str}
    # Uses metrics.k8s.io/v1beta1 pods endpoint
    out = {}
    try:
        result = custom_objects.list_cluster_custom_object(
            group="metrics.k8s.io",
            version="v1beta1",
            plural="pods",
        )
        for item in result.get("items", []):
            ns = item.get("metadata", {}).get("namespace", "")
            name = item.get("metadata", {}).get("name", "")
            containers = item.get("containers", [])
            # Sum CPU and memory across all containers in the pod
            total_cpu_nano = 0
            total_mem_ki = 0
            for c in containers:
                usage = c.get("usage", {})
                cpu = usage.get("cpu", "0n")
                mem = usage.get("memory", "0Ki")
                # Parse nanocores
                cpu_s = str(cpu).strip()
                if cpu_s.endswith("n"):
                    total_cpu_nano += float(cpu_s[:-1])
                elif cpu_s.endswith("m"):
                    total_cpu_nano += float(cpu_s[:-1]) * 1e6
                else:
                    try:
                        total_cpu_nano += float(cpu_s) * 1e9
                    except Exception:
                        pass
                # Parse memory Ki
                mem_s = str(mem).strip()
                if mem_s.endswith("Ki"):
                    total_mem_ki += float(mem_s[:-2])
                elif mem_s.endswith("Mi"):
                    total_mem_ki += float(mem_s[:-2]) * 1024
                elif mem_s.endswith("Gi"):
                    total_mem_ki += float(mem_s[:-2]) * 1024 * 1024
                else:
                    try:
                        total_mem_ki += float(mem_s) / 1024
                    except Exception:
                        pass
            out[(ns, name)] = {
                "cpu": f"{total_cpu_nano:.0f}n",  # nanocores string
                "memory": f"{total_mem_ki:.0f}Ki",  # Ki string
            }
    except Exception as e:
        print(f"WARNING: Could not collect pod metrics: {e}")
    return out


def parse_quantity(s):
    """Return Kubernetes quantity string as-is for display."""
    return s


def safe_iso(dt):
    """Convert datetime to ISO string, returning None on failure."""
    try:
        if not dt:
            return None
        return dt.replace(tzinfo=dt.tzinfo or timezone.utc).isoformat()
    except Exception:
        return None


def container_state_to_dict(state):
    """Convert V1ContainerState to a small serializable dict."""
    if not state:
        return {}
    try:
        if getattr(state, "running", None):
            return {
                "type": "Running",
                "startedAt": safe_iso(getattr(state.running, "started_at", None)),
            }
        if getattr(state, "waiting", None):
            return {
                "type": "Waiting",
                "reason": getattr(state.waiting, "reason", None),
                "message": getattr(state.waiting, "message", None),
            }
        if getattr(state, "terminated", None):
            return {
                "type": "Terminated",
                "reason": getattr(state.terminated, "reason", None),
                "message": getattr(state.terminated, "message", None),
                "exitCode": getattr(state.terminated, "exit_code", None),
                "finishedAt": safe_iso(
                    getattr(state.terminated, "finished_at", None)
                ),
            }
    except Exception:
        # If anything goes wrong while introspecting, return an empty dict.
        return {}
    return {}


def probe_type(probe):
    """Return a short string describing the probe type."""
    try:
        if not probe:
            return None
        if getattr(probe, "http_get", None):
            return "HTTPGet"
        if getattr(probe, "tcp_socket", None):
            return "TCPSocket"
        if getattr(probe, "exec", None):
            return "Exec"
    except Exception:
        return None
    return None


def container_spec_to_dict(c):
    """Convert V1Container spec to a serializable dict with resource info."""
    try:
        resources = getattr(c, "resources", None) or {}
        requests = getattr(resources, "requests", None) or {}
        limits = getattr(resources, "limits", None) or {}
        ports = []
        for p in getattr(c, "ports", None) or []:
            try:
                ports.append(
                    {
                        "containerPort": getattr(p, "container_port", None),
                        "protocol": getattr(p, "protocol", None) or "TCP",
                    }
                )
            except Exception:
                continue
        env_names = []
        for e in getattr(c, "env", None) or []:
            try:
                name = getattr(e, "name", None)
                if name:
                    env_names.append(name)
            except Exception:
                continue
        return {
            "name": getattr(c, "name", None),
            "image": getattr(c, "image", None),
            "imagePullPolicy": getattr(c, "image_pull_policy", None),
            "resources": {
                "requests": {
                    "cpu": parse_quantity(requests.get("cpu")),
                    "memory": parse_quantity(requests.get("memory")),
                },
                "limits": {
                    "cpu": parse_quantity(limits.get("cpu")),
                    "memory": parse_quantity(limits.get("memory")),
                },
            },
            "ports": ports,
            "envNames": env_names,
            "readinessProbeType": probe_type(
                getattr(c, "readiness_probe", None)
            ),
            "livenessProbeType": probe_type(getattr(c, "liveness_probe", None)),
        }
    except Exception:
        # If any field fails, return at least the container name.
        return {"name": getattr(c, "name", None)}


def volume_to_dict(v):
    """Convert V1Volume to a small dict describing its type and ref name."""
    info = {"name": getattr(v, "name", None), "type": None}
    try:
        if getattr(v, "config_map", None):
            info["type"] = "ConfigMap"
            info["configMapName"] = getattr(v.config_map, "name", None)
        elif getattr(v, "secret", None):
            info["type"] = "Secret"
            info["secretName"] = getattr(v.secret, "secret_name", None)
        elif getattr(v, "persistent_volume_claim", None):
            info["type"] = "PersistentVolumeClaim"
            info["claimName"] = getattr(
                v.persistent_volume_claim, "claim_name", None
            )
        elif getattr(v, "empty_dir", None):
            info["type"] = "EmptyDir"
        elif getattr(v, "host_path", None):
            info["type"] = "HostPath"
        elif getattr(v, "downward_api", None):
            info["type"] = "DownwardAPI"
        else:
            info["type"] = "Other"
    except Exception:
        # If any attribute access fails, leave best-effort info.
        pass
    return info


def collect_nodes():
    """Collect nodes list with optional metrics and extended details."""
    if core_v1 is None:
        return {"items": [], "error": K8S_CONFIG_ERROR or "Kubernetes client not configured"}
    try:
        node_list = core_v1.list_node()
        node_metrics = get_node_metrics()
        items = []
        for n in node_list.items:
            try:
                status = "NotReady"
                conditions = []
                for c in n.status.conditions or []:
                    if c.type == "Ready" and c.status == "True":
                        status = "Ready"
                    conditions.append(
                        {
                            "type": c.type,
                            "status": c.status,
                            "reason": getattr(c, "reason", None),
                            "message": getattr(c, "message", None),
                            "lastTransitionTime": safe_iso(
                                getattr(c, "last_transition_time", None)
                            ),
                        }
                    )
                roles = [
                    r.replace("node-role.kubernetes.io/", "")
                    for r in (n.metadata.labels or {})
                    if r.startswith("node-role.kubernetes.io/")
                ] or ["worker"]
                cap = n.status.capacity or {}
                alloc = n.status.allocatable or {}
                usage = node_metrics.get(n.metadata.name, {})
                addresses = {}
                for a in n.status.addresses or []:
                    try:
                        if a.type and a.address:
                            addresses[a.type] = a.address
                    except Exception:
                        continue
                node_info = getattr(n.status, "node_info", None) or {}
                taints = []
                for t in (getattr(n.spec, "taints", None) or []):
                    try:
                        taints.append(
                            {
                                "key": t.key,
                                "value": t.value,
                                "effect": t.effect,
                            }
                        )
                    except Exception:
                        continue
                items.append(
                    {
                        "name": n.metadata.name,
                        "status": status,
                        "roles": roles,
                        "cpuCapacity": parse_quantity(cap.get("cpu")),
                        "memoryCapacity": parse_quantity(cap.get("memory")),
                        "podsCapacity": parse_quantity(cap.get("pods")),
                        "ephemeralStorageCapacity": parse_quantity(
                            cap.get("ephemeral-storage")
                        ),
                        "allocatableCPU": parse_quantity(alloc.get("cpu")),
                        "allocatableMemory": parse_quantity(alloc.get("memory")),
                        "allocatablePods": parse_quantity(alloc.get("pods")),
                        "allocatableEphemeralStorage": parse_quantity(
                            alloc.get("ephemeral-storage")
                        ),
                        "cpuUsage": usage.get("cpu"),
                        "memoryUsage": usage.get("memory"),
                        "podCIDR": getattr(getattr(n, "spec", None), "pod_cidr", None),
                        "age": safe_iso(n.metadata.creation_timestamp),
                        "labels": dict(n.metadata.labels or {}),
                        "taints": taints,
                        "addresses": {
                            "internalIP": addresses.get("InternalIP"),
                            "externalIP": addresses.get("ExternalIP"),
                        },
                        "nodeInfo": {
                            "osImage": getattr(node_info, "os_image", None),
                            "kernelVersion": getattr(node_info, "kernel_version", None),
                            "containerRuntime": getattr(
                                node_info, "container_runtime_version", None
                            ),
                            "kubeletVersion": getattr(
                                node_info, "kubelet_version", None
                            ),
                            "kubeProxyVersion": getattr(
                                node_info, "kube_proxy_version", None
                            ),
                            "architecture": getattr(node_info, "architecture", None),
                            "operatingSystem": getattr(
                                node_info, "operating_system", None
                            ),
                        },
                        "conditions": conditions,
                    }
                )
            except Exception:
                # If a single node fails to serialize, skip it rather than fail the whole payload.
                continue
        return {"items": items}
    except Exception as e:
        return {"items": [], "error": str(e)}


def get_replicaset_owner_map(apps_v1):
    """
    Collect ReplicaSets to resolve pod owner -> deployment.

    Returns a dict: replicaset_name -> deployment_name
    by reading each RS's ownerReferences.
    """
    rs_to_deployment = {}
    if apps_v1 is None:
        return rs_to_deployment
    try:
        rs_list = apps_v1.list_replicaset_for_all_namespaces()
        for rs in rs_list.items:
            try:
                owners = getattr(rs.metadata, "owner_references", None) or []
            except Exception:
                owners = []
            for owner in owners:
                try:
                    kind = getattr(owner, "kind", None)
                    name = getattr(owner, "name", None)
                except Exception:
                    continue
                if kind == "Deployment" and name:
                    rs_name = getattr(rs.metadata, "name", None)
                    if rs_name:
                        rs_to_deployment[rs_name] = name
    except Exception as e:
        logger.warning("Could not collect ReplicaSets for owner map: %s", e)
    return rs_to_deployment


def collect_pods():
    """Collect pods with rich detail for the dashboard."""
    if core_v1 is None:
        return {"items": [], "error": K8S_CONFIG_ERROR or "Kubernetes client not configured"}
    try:
        pod_list = core_v1.list_pod_for_all_namespaces()

        # Build ReplicaSet -> Deployment lookup so we can resolve pod owners more accurately.
        rs_to_deployment = get_replicaset_owner_map(apps_v1)

        # Preload live pod metrics once per collection cycle.
        pod_metrics = get_pod_metrics(custom_objects) if METRICS_AVAILABLE and custom_objects else {}

        # Preload pod events by namespace so the dashboard can show recent events per pod.
        pod_events_index = {}
        try:
            ns_set = set()
            for p in pod_list.items:
                if p.metadata and p.metadata.namespace:
                    ns_set.add(p.metadata.namespace)
            for ns in ns_set:
                try:
                    ev_list = core_v1.list_namespaced_event(ns)
                except Exception:
                    continue
                for ev in ev_list.items:
                    try:
                        involved = getattr(ev, "involved_object", None)
                        if not involved or involved.kind != "Pod":
                            continue
                        pod_name = involved.name
                        if not pod_name:
                            continue
                        key = (ns, pod_name)
                        pod_events_index.setdefault(key, []).append(ev)
                    except Exception:
                        continue
            # Trim and sort events per pod: last 5 by lastTimestamp.
            for key, evs in list(pod_events_index.items()):
                try:
                    evs.sort(
                        key=lambda e: safe_iso(
                            getattr(e, "last_timestamp", None)
                            or getattr(e, "event_time", None)
                            or getattr(e.metadata, "creation_timestamp", None)
                        )
                        or "",
                        reverse=True,
                    )
                    pod_events_index[key] = evs[:5]
                except Exception:
                    pod_events_index[key] = []
        except Exception:
            pod_events_index = {}

        items = []
        for p in pod_list.items:
            try:
                status = p.status.phase or "Unknown"
                restart = sum(
                    (cs.restart_count or 0)
                    for cs in (p.status.container_statuses or [])
                )
                labels = dict(p.metadata.labels or {})
                annotations = dict(p.metadata.annotations or {})

                spec_containers = []
                for c in p.spec.containers or []:
                    spec_containers.append(container_spec_to_dict(c))

                status_by_name = {
                    cs.name: cs for cs in (p.status.container_statuses or [])
                }
                for c in spec_containers:
                    name = c.get("name")
                    cs = status_by_name.get(name)
                    if not cs:
                        continue
                    c["ready"] = bool(getattr(cs, "ready", False))
                    c["restartCount"] = getattr(cs, "restart_count", None)
                    c["state"] = container_state_to_dict(getattr(cs, "state", None))
                    c["lastState"] = container_state_to_dict(
                        getattr(cs, "last_state", None)
                    )

                volumes = []
                for v in p.spec.volumes or []:
                    volumes.append(volume_to_dict(v))

                pod_conditions = []
                for c in p.status.conditions or []:
                    pod_conditions.append(
                        {
                            "type": c.type,
                            "status": c.status,
                            "reason": getattr(c, "reason", None),
                            "message": getattr(c, "message", None),
                            "lastTransitionTime": safe_iso(
                                getattr(c, "last_transition_time", None)
                            ),
                        }
                    )

                # Aggregate pod-level CPU/memory requests/limits and live usage.
                cpu_usage = None
                memory_usage = None
                cpu_request = None
                cpu_limit = None
                memory_request = None
                memory_limit = None
                try:
                    import re as _re

                    def _parse_mem_ki(s):
                        s_str = str(s or "").strip()
                        m = _re.match(r"^([\d.]+)\s*([KMGTPkmgtp]i?)?$", s_str)
                        if not m:
                            return 0
                        n = float(m.group(1))
                        u = (m.group(2) or "").lower()
                        table = {
                            "ki": 1,
                            "k": 1,
                            "mi": 1024,
                            "m": 1024,
                            "gi": 1024 ** 2,
                            "g": 1024 ** 2,
                            "ti": 1024 ** 3,
                        }
                        return n * table.get(u, 1 / 1024)

                    cpu_request_total = 0.0
                    cpu_limit_total = 0.0
                    mem_request_total = 0.0
                    mem_limit_total = 0.0
                    for c in p.spec.containers or []:
                        try:
                            res = c.resources
                            if res and res.requests:
                                cpu_req = res.requests.get("cpu", "0")
                                mem_req = res.requests.get("memory", "0")
                                v = str(cpu_req).strip()
                                if v.endswith("m"):
                                    cpu_request_total += float(v[:-1])
                                else:
                                    try:
                                        cpu_request_total += float(v) * 1000
                                    except Exception:
                                        pass
                                mem_request_total += _parse_mem_ki(mem_req)
                        except Exception:
                            pass
                        try:
                            res = c.resources
                            if res and res.limits:
                                cpu_lim = res.limits.get("cpu", None)
                                mem_lim = res.limits.get("memory", None)
                                if cpu_lim:
                                    v = str(cpu_lim).strip()
                                    if v.endswith("m"):
                                        cpu_limit_total += float(v[:-1])
                                    else:
                                        try:
                                            cpu_limit_total += float(v) * 1000
                                        except Exception:
                                            pass
                                if mem_lim:
                                    mem_limit_total += _parse_mem_ki(mem_lim)
                        except Exception:
                            pass

                    pod_usage = pod_metrics.get(
                        (p.metadata.namespace, p.metadata.name), {}
                    )
                    cpu_usage = pod_usage.get("cpu")
                    memory_usage = pod_usage.get("memory")
                    cpu_request = (
                        f"{cpu_request_total:.0f}m" if cpu_request_total else None
                    )
                    cpu_limit = f"{cpu_limit_total:.0f}m" if cpu_limit_total else None
                    memory_request = (
                        f"{mem_request_total:.0f}Ki" if mem_request_total else None
                    )
                    memory_limit = (
                        f"{mem_limit_total:.0f}Ki" if mem_limit_total else None
                    )
                except Exception:
                    cpu_usage = None
                    memory_usage = None
                    cpu_request = None
                    cpu_limit = None
                    memory_request = None
                    memory_limit = None

                events_raw = pod_events_index.get(
                    (p.metadata.namespace, p.metadata.name), []
                )
                events = []
                for ev in events_raw:
                    try:
                        events.append(
                            {
                                "type": getattr(ev, "type", None),
                                "reason": getattr(ev, "reason", None),
                                "message": getattr(ev, "message", None),
                                "lastTimestamp": safe_iso(
                                    getattr(ev, "last_timestamp", None)
                                    or getattr(ev, "event_time", None)
                                    or getattr(ev.metadata, "creation_timestamp", None)
                                ),
                            }
                        )
                    except Exception:
                        continue

                owner_refs = []
                try:
                    raw_owner_refs = getattr(p.metadata, "owner_references", None) or []
                except Exception:
                    raw_owner_refs = []
                workload = None
                workload_kind = None
                for o in raw_owner_refs:
                    try:
                        kind = getattr(o, "kind", None)
                        name = getattr(o, "name", None)
                    except Exception:
                        continue
                    if not kind or not name:
                        continue
                    owner_refs.append(
                        {
                            "kind": kind,
                            "name": name,
                            "controller": getattr(o, "controller", None),
                        }
                    )
                    if kind == "ReplicaSet":
                        deployment = rs_to_deployment.get(name)
                        workload = deployment or name
                        workload_kind = "Deployment" if deployment else "ReplicaSet"
                    elif kind in ("DaemonSet", "StatefulSet", "Job", "CronJob"):
                        workload = name
                        workload_kind = kind
                    if workload and workload_kind:
                        break

                items.append(
                    {
                        "name": p.metadata.name,
                        "namespace": p.metadata.namespace,
                        "status": status,
                        "restartCount": restart,
                        "cpuUsage": cpu_usage,
                        "memoryUsage": memory_usage,
                        "cpuRequest": cpu_request,
                        "cpuLimit": cpu_limit,
                        "memoryRequest": memory_request,
                        "memoryLimit": memory_limit,
                        "nodeName": p.spec.node_name or "",
                        "age": safe_iso(p.metadata.creation_timestamp),
                        "podIP": getattr(p.status, "pod_ip", None),
                        "hostIP": getattr(p.status, "host_ip", None),
                        "qosClass": getattr(p.status, "qos_class", None),
                        "startTime": safe_iso(getattr(p.status, "start_time", None)),
                        "nominatedNode": getattr(
                            p.status, "nominated_node_name", None
                        ),
                        "labels": labels,
                        "annotations": annotations,
                        "containers": spec_containers,
                        "volumes": volumes,
                        "conditions": pod_conditions,
                        "containerStatuses": [
                            {
                                "name": cs.name,
                                "ready": bool(getattr(cs, "ready", False)),
                                "restartCount": getattr(cs, "restart_count", None),
                                "state": container_state_to_dict(
                                    getattr(cs, "state", None)
                                ),
                                "lastState": container_state_to_dict(
                                    getattr(cs, "last_state", None)
                                ),
                            }
                            for cs in (p.status.container_statuses or [])
                        ],
                        "ownerReferences": owner_refs,
                        "events": events,
                        "workload": workload,
                        "workloadKind": workload_kind,
                    }
                )
            except Exception:
                # If a single pod fails to serialize, skip it.
                continue
        return {"items": items}
    except Exception as e:
        return {"items": [], "error": str(e)}


def collect_deployments():
    if apps_v1 is None:
        return {"items": [], "error": K8S_CONFIG_ERROR or "Kubernetes client not configured"}
    try:
        dep_list = apps_v1.list_deployment_for_all_namespaces()
        items = []
        for d in dep_list.items:
            try:
                labels = dict(d.metadata.labels or {})
                selector = {}
                try:
                    if d.spec.selector and d.spec.selector.match_labels:
                        selector = dict(d.spec.selector.match_labels)
                except Exception:
                    selector = {}

                strategy_info = None
                try:
                    strat = d.spec.strategy
                    if strat:
                        strategy_info = {"type": strat.type or "RollingUpdate"}
                        ru = strat.rolling_update
                        if ru:
                            strategy_info["maxSurge"] = (
                                str(ru.max_surge) if ru.max_surge is not None else None
                            )
                            strategy_info["maxUnavailable"] = (
                                str(ru.max_unavailable)
                                if ru.max_unavailable is not None
                                else None
                            )
                except Exception:
                    strategy_info = None

                conditions = []
                for c in getattr(d.status, "conditions", None) or []:
                    try:
                        conditions.append(
                            {
                                "type": c.type,
                                "status": c.status,
                                "reason": getattr(c, "reason", None),
                                "message": getattr(c, "message", None),
                                "lastTransitionTime": safe_iso(
                                    getattr(c, "last_update_time", None)
                                    or getattr(c, "last_transition_time", None)
                                ),
                            }
                        )
                    except Exception:
                        continue

                tmpl_containers = []
                try:
                    tmpl_spec = (
                        d.spec.template.spec
                        if d.spec
                        and d.spec.template
                        and d.spec.template.spec
                        else None
                    )
                    for c in getattr(tmpl_spec, "containers", None) or []:
                        tmpl_containers.append(container_spec_to_dict(c))
                except Exception:
                    tmpl_containers = []

                items.append(
                    {
                        "name": d.metadata.name,
                        "namespace": d.metadata.namespace,
                        "readyReplicas": d.status.ready_replicas or 0,
                        "desiredReplicas": d.spec.replicas or 0,
                        "availableReplicas": d.status.available_replicas or 0,
                        "age": safe_iso(d.metadata.creation_timestamp),
                        "labels": labels,
                        "selector": selector,
                        "strategy": strategy_info,
                        "conditions": conditions,
                        "templateContainers": tmpl_containers,
                    }
                )
            except Exception:
                continue
        return {"items": items}
    except Exception as e:
        return {"items": [], "error": str(e)}


def collect_services():
    if core_v1 is None:
        return {"items": [], "error": K8S_CONFIG_ERROR or "Kubernetes client not configured"}
    try:
        svc_list = core_v1.list_service_for_all_namespaces()
        items = []
        for s in svc_list.items:
            try:
                ports = []
                for port in s.spec.ports or []:
                    try:
                        ports.append(
                            {
                                "name": port.name,
                                "port": port.port,
                                "targetPort": (
                                    str(port.target_port)
                                    if port.target_port is not None
                                    else None
                                ),
                                "protocol": port.protocol or "TCP",
                                "nodePort": getattr(port, "node_port", None),
                            }
                        )
                    except Exception:
                        continue
                external = None
                try:
                    if (
                        s.status.load_balancer
                        and s.status.load_balancer.ingress
                    ):
                        external = ", ".join(
                            ing.ip or ing.hostname or ""
                            for ing in s.status.load_balancer.ingress
                        )
                except Exception:
                    external = None

                # Per-service endpoints (ready addresses and ports).
                endpoints = []
                try:
                    ep = core_v1.read_namespaced_endpoints(
                        name=s.metadata.name, namespace=s.metadata.namespace
                    )
                    for subset in ep.subsets or []:
                        for addr in subset.addresses or []:
                            try:
                                endpoints.append(
                                    {
                                        "ip": addr.ip,
                                        "nodeName": getattr(addr, "node_name", None),
                                        "targetRefKind": getattr(
                                            getattr(addr, "target_ref", None),
                                            "kind",
                                            None,
                                        ),
                                        "ports": [
                                            {
                                                "port": p.port,
                                                "name": p.name,
                                                "protocol": p.protocol or "TCP",
                                            }
                                            for p in subset.ports or []
                                        ],
                                    }
                                )
                            except Exception:
                                continue
                except ApiException:
                    endpoints = []
                except Exception:
                    endpoints = []

                items.append(
                    {
                        "name": s.metadata.name,
                        "namespace": s.metadata.namespace,
                        "type": s.spec.type or "ClusterIP",
                        "clusterIP": s.spec.cluster_ip or "",
                        "externalIP": external,
                        "ports": ports,
                        "selector": dict(s.spec.selector or {}),
                        "sessionAffinity": getattr(
                            s.spec, "session_affinity", None
                        ),
                        "healthCheckNodePort": getattr(
                            s.spec, "health_check_node_port", None
                        ),
                        "endpoints": endpoints,
                        "age": safe_iso(s.metadata.creation_timestamp),
                    }
                )
            except Exception:
                continue
        return {"items": items}
    except Exception as e:
        return {"items": [], "error": str(e)}


def collect_ingresses():
    if networking_v1 is None:
        return {"items": [], "error": K8S_CONFIG_ERROR or "Kubernetes client not configured"}
    try:
        ing_list = networking_v1.list_ingress_for_all_namespaces()
        items = []
        for ing in ing_list.items:
            try:
                hosts = [r.host for r in (ing.spec.rules or []) if r.host]
                backends = []
                rules = []
                if ing.spec.default_backend and ing.spec.default_backend.service:
                    backends.append(ing.spec.default_backend.service.name)
                for rule in ing.spec.rules or []:
                    host = rule.host or ""
                    if rule.http:
                        for path in rule.http.paths or []:
                            try:
                                svc_name = None
                                svc_port = None
                                if path.backend and path.backend.service:
                                    svc_name = path.backend.service.name
                                    if path.backend.service.port:
                                        svc_port = (
                                            path.backend.service.port.number
                                            or path.backend.service.port.name
                                        )
                                    backends.append(svc_name)
                                rules.append(
                                    {
                                        "host": host,
                                        "path": path.path or "/",
                                        "pathType": getattr(
                                            path, "path_type", None
                                        ),
                                        "serviceName": svc_name,
                                        "servicePort": svc_port,
                                    }
                                )
                            except Exception:
                                continue
                tls_entries = []
                for t in ing.spec.tls or []:
                    try:
                        tls_entries.append(
                            {
                                "secretName": t.secret_name,
                                "hosts": list(t.hosts or []),
                            }
                        )
                    except Exception:
                        continue
                items.append(
                    {
                        "name": ing.metadata.name,
                        "namespace": ing.metadata.namespace,
                        "hosts": hosts,
                        "backendServices": list(dict.fromkeys(backends)),
                        "age": safe_iso(ing.metadata.creation_timestamp),
                        "ingressClassName": getattr(
                            ing.spec, "ingress_class_name", None
                        ),
                        "rules": rules,
                        "tls": tls_entries,
                    }
                )
            except Exception:
                continue
        return {"items": items}
    except Exception as e:
        return {"items": [], "error": str(e)}


def collect_namespaces():
    if core_v1 is None:
        return {"items": [], "error": K8S_CONFIG_ERROR or "Kubernetes client not configured"}
    try:
        ns_list = core_v1.list_namespace()
        items = []
        for ns in ns_list.items:
            try:
                phase = ns.status.phase if ns.status else "Unknown"
                items.append({
                    "name": ns.metadata.name,
                    "status": phase,
                    "age": safe_iso(ns.metadata.creation_timestamp),
                    "labels": dict(ns.metadata.labels or {}),
                })
            except Exception:
                continue
        return {"items": items}
    except Exception as e:
        return {"items": [], "error": str(e)}


def collect_configmaps():
    if core_v1 is None:
        return {"items": [], "error": K8S_CONFIG_ERROR or "Kubernetes client not configured"}
    try:
        cm_list = core_v1.list_config_map_for_all_namespaces()
        items = []
        for cm in cm_list.items:
            try:
                if cm.metadata.namespace == "kube-system":
                    continue
                data = cm.data or {}
                binary = cm.binary_data or {}
                num_keys = len(data) + len(binary)
                items.append({
                    "name": cm.metadata.name,
                    "namespace": cm.metadata.namespace,
                    "keyCount": num_keys,
                    "keys": sorted(list(data.keys()) + list(binary.keys())),
                    "data": data,
                    "age": safe_iso(cm.metadata.creation_timestamp),
                })
            except Exception:
                continue
        return {"items": items}
    except Exception as e:
        return {"items": [], "error": str(e)}


def collect_summary():
    summary = {
        "totalNodes": 0,
        "readyNodes": 0,
        "totalPods": 0,
        "runningPods": 0,
        "pendingPods": 0,
        "failedPods": 0,
        "totalDeployments": 0,
        "healthyDeployments": 0,
        "totalServices": 0,
        "totalNamespaces": 0,
    }
    if core_v1:
        try:
            nodes = core_v1.list_node()
            summary["totalNodes"] = len(nodes.items)
            for n in nodes.items:
                for c in n.status.conditions or []:
                    if c.type == "Ready" and c.status == "True":
                        summary["readyNodes"] += 1
                        break
        except Exception:
            pass
    if core_v1:
        try:
            pods = core_v1.list_pod_for_all_namespaces()
            summary["totalPods"] = len(pods.items)
            for p in pods.items:
                phase = (p.status.phase or "").lower()
                if phase == "running":
                    summary["runningPods"] += 1
                elif phase == "pending":
                    summary["pendingPods"] += 1
                elif phase in ("failed", "unknown"):
                    summary["failedPods"] += 1
        except Exception:
            pass
    if apps_v1:
        try:
            deps = apps_v1.list_deployment_for_all_namespaces()
            summary["totalDeployments"] = len(deps.items)
            for d in deps.items:
                desired = d.spec.replicas or 0
                ready = d.status.ready_replicas or 0
                if desired > 0 and ready == desired:
                    summary["healthyDeployments"] += 1
        except Exception:
            pass
    if core_v1:
        try:
            svcs = core_v1.list_service_for_all_namespaces()
            summary["totalServices"] = len(svcs.items)
        except Exception:
            pass
    if core_v1:
        try:
            nss = core_v1.list_namespace()
            summary["totalNamespaces"] = len(nss.items)
        except Exception:
            pass
    return summary


def collect_all_data():
    """Collect all cluster data for heartbeat payload."""
    nodes = collect_nodes()
    pods = collect_pods()
    deployments = collect_deployments()
    services = collect_services()
    ingresses = collect_ingresses()
    namespaces = collect_namespaces()
    configmaps = collect_configmaps()
    summary = collect_summary()
    return {
        "nodes": nodes.get("items", []),
        "pods": pods.get("items", []),
        "deployments": deployments.get("items", []),
        "services": services.get("items", []),
        "ingresses": ingresses.get("items", []),
        "namespaces": namespaces.get("items", []),
        "configmaps": configmaps.get("items", []),
        "summary": summary,
    }


def do_register():
    """Register with dashboard using REGISTRATION_TOKEN. Returns True on success, False on fatal error."""
    global _agent_secret_key
    if not DASHBOARD_URL or not REGISTRATION_TOKEN:
        logger.error(
            "Neither SECRET_KEY nor REGISTRATION_TOKEN is set. "
            "Generate a token from the dashboard Add Cluster tab and reinstall."
        )
        return False
    url = f"{DASHBOARD_URL}/api/register"
    headers = {"X-Registration-Token": REGISTRATION_TOKEN, "Content-Type": "application/json"}
    body = {"cluster_name": CLUSTER_NAME, "agent_version": "1.0"}
    for attempt in range(1, REGISTER_MAX_ATTEMPTS + 1):
        try:
            r = requests.post(url, json=body, headers=headers, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                _agent_secret_key = (data.get("secret_key") or "").strip()
                if _agent_secret_key:
                    logger.info("Registration successful. Cluster '%s' is now connected.", CLUSTER_NAME)
                    return True
                logger.error("Registration response missing secret_key.")
                return False
            if r.status_code == 410:
                logger.error(
                    "Registration token expired. "
                    "Go to dashboard Add Cluster tab and generate a new token."
                )
                return False
            if r.status_code == 401:
                try:
                    err = r.json().get("error", r.text)
                except Exception:
                    err = r.text
                logger.warning("Registration rejected (401): %s. Retrying in %ds...", err, REGISTER_RETRY_INTERVAL)
            else:
                logger.warning("Registration failed HTTP %s. Retrying in %ds...", r.status_code, REGISTER_RETRY_INTERVAL)
        except requests.RequestException as e:
            logger.warning("Registration request failed: %s. Retrying in %ds...", e, REGISTER_RETRY_INTERVAL)
        if attempt < REGISTER_MAX_ATTEMPTS:
            time.sleep(REGISTER_RETRY_INTERVAL)
    logger.error("Registration failed after %d attempts. Exiting.", REGISTER_MAX_ATTEMPTS)
    return False


def send_one_heartbeat():
    """Send a single heartbeat (used once right after registration, then in loop)."""
    global _agent_secret_key
    key = _agent_secret_key
    if not key or not DASHBOARD_URL:
        return
    try:
        data = collect_all_data()
        payload = {
            "cluster_name": CLUSTER_NAME,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "data": data,
        }
        r = requests.post(
            f"{DASHBOARD_URL}/api/heartbeat",
            json=payload,
            headers={"X-Secret-Key": key, "Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 200:
            s = data.get("summary", {})
            logger.info(
                "Heartbeat sent for cluster '%s' — %s nodes, %s pods, %s deployments",
                CLUSTER_NAME,
                s.get("totalNodes", 0),
                s.get("totalPods", 0),
                s.get("totalDeployments", 0),
            )
        else:
            logger.warning("Heartbeat failed with HTTP %s: %s", r.status_code, r.text[:200])
    except requests.RequestException as e:
        logger.warning("Heartbeat request failed: %s. Will retry next cycle.", e)


def heartbeat_loop():
    """Background thread: send heartbeat to dashboard every HEARTBEAT_INTERVAL seconds."""
    global _agent_secret_key
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        send_one_heartbeat()


@app.route("/healthz", methods=["GET"])
def healthz():
    """Liveness/readiness probe: returns ok if the app is running."""
    return jsonify({"status": "ok"})


def start_agent():
    """Ensure we have a secret key (from env or registration), then start heartbeat thread."""
    global _agent_secret_key
    if _agent_secret_key:
        logger.info("SECRET_KEY already set, skipping registration.")
    else:
        if not do_register():
            raise SystemExit(1)
    # Send first heartbeat immediately so dashboard has data right away
    send_one_heartbeat()
    # Daemon thread for subsequent heartbeats every 30s
    t = threading.Thread(target=heartbeat_loop, daemon=True)
    t.start()


# Start agent when module is loaded (so gunicorn single-worker also runs registration + heartbeat)
if DASHBOARD_URL:
    start_agent()

if __name__ == "__main__":
    if not DASHBOARD_URL:
        logger.error("DASHBOARD_URL is not set. Exiting.")
        raise SystemExit(1)
    app.run(host="0.0.0.0", port=8080)
