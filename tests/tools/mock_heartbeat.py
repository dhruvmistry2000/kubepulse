"""Dev-only mock agent. Not copied into the dashboard or agent images.

Registers two clusters with the real token flow and posts heartbeats.
`prod` keeps reporting. `staging` stops after 60 seconds so it goes stale.
"""

import json
import os
import time
import urllib.error
import urllib.request

BASE = (os.environ.get("DASHBOARD_URL") or "http://127.0.0.1:8765").rstrip("/")
ADMIN_KEY = (os.environ.get("ADMIN_KEY") or "dev-admin-key").strip()
INTERVAL = 30
STAGING_LIFETIME = 60


def _request(method, path, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"error": raw}
        return exc.code, payload


def register(name):
    status, token_body = _request(
        "POST",
        "/api/generate-token",
        {"cluster_name": name},
        {"X-Admin-Key": ADMIN_KEY},
    )
    if status != 200:
        raise SystemExit("token for %s failed: %s %s" % (name, status, token_body))
    status, reg = _request(
        "POST",
        "/api/register",
        {"cluster_name": name, "agent_version": "mock"},
        {"X-Registration-Token": token_body["token"]},
    )
    if status != 200:
        raise SystemExit("register %s failed: %s %s" % (name, status, reg))
    secret = reg.get("cluster_credential") or reg.get("secret_key")
    if not secret:
        raise SystemExit("register %s returned no credential" % name)
    return secret


def _node(index, ready=True):
    name = "node-%02d" % index
    return {
        "name": name,
        "uid": "uid-%s" % name,
        "provider": "gcp",
        "instanceType": "e2-standard-4",
        "region": "us-central1",
        "status": "Ready" if ready else "NotReady",
        "podCount": 14 if ready else 2,
        "cpuUsage": "3200m" if ready else "100m",
        "allocatableCPU": "3920m",
        "cpuCapacity": "4",
        "memoryUsage": "10Gi" if ready else "1Gi",
        "allocatableMemory": "16Gi",
        "memoryCapacity": "16Gi",
        "priced": True,
        "hourlyUsd": 0.134,
        "age": "2026-10-01T00:00:00+00:00",
        "labels": {"node": name},
    }


def _pod(index, namespace, status, node, workload):
    name = "%s-%02d" % (workload, index)
    events = []
    restarts = 0
    if status == "CrashLoopBackOff":
        restarts = 7
        events.append({
            "type": "Warning",
            "reason": "BackOff",
            "message": "Back-off restarting failed container api",
            "lastTimestamp": "2026-10-31T12:00:00+00:00",
        })
    elif status == "Pending":
        events.append({
            "type": "Warning",
            "reason": "FailedScheduling",
            "message": "0/6 nodes are available: 1 node not ready",
            "lastTimestamp": "2026-10-31T12:00:00+00:00",
        })
    return {
        "name": name,
        "namespace": namespace,
        "status": status,
        "workload": workload,
        "workloadKind": "Deployment",
        "nodeName": node,
        "restartCount": restarts,
        "images": ["ghcr.io/example/%s:1.4.2" % workload],
        "labels": {"app": workload},
        "age": "2026-10-20T00:00:00+00:00",
        "cpuUsage": "50m",
        "memoryUsage": "128Mi",
        "events": events,
        "containers": [{
            "name": "app",
            "image": "ghcr.io/example/%s:1.4.2" % workload,
            "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}},
        }],
    }


def prod_payload():
    nodes = [_node(i, ready=(i != 6)) for i in range(1, 7)]
    pods = []
    namespaces = ("payments", "checkout", "default")
    workloads = ("api", "worker", "web")
    for i in range(80):
        if i < 3:
            status = "Pending"
        elif i < 6:
            status = "CrashLoopBackOff"
        else:
            status = "Running"
        pods.append(_pod(
            i,
            namespaces[i % len(namespaces)],
            status,
            nodes[i % len(nodes)]["name"],
            workloads[i % len(workloads)],
        ))
    running = sum(1 for p in pods if p["status"] == "Running")
    pending = sum(1 for p in pods if p["status"] == "Pending")
    failed = sum(1 for p in pods if p["status"] == "CrashLoopBackOff")
    return {
        "nodesAuthoritative": True,
        "summary": {
            "totalNodes": 6,
            "readyNodes": 5,
            "totalPods": len(pods),
            "runningPods": running,
            "pendingPods": pending,
            "failedPods": failed,
            "totalDeployments": 4,
            "healthyDeployments": 3,
            "totalServices": 3,
            "totalNamespaces": 3,
        },
        "nodes": nodes,
        "pods": pods,
        "deployments": [
            {"name": "api", "namespace": "payments", "readyReplicas": 2, "desiredReplicas": 3, "availableReplicas": 2, "age": "2026-10-01T00:00:00+00:00", "labels": {"app": "api"}, "selector": {"app": "api"}},
            {"name": "worker", "namespace": "payments", "readyReplicas": 4, "desiredReplicas": 4, "availableReplicas": 4, "age": "2026-10-01T00:00:00+00:00", "labels": {"app": "worker"}, "selector": {"app": "worker"}},
            {"name": "web", "namespace": "checkout", "readyReplicas": 2, "desiredReplicas": 2, "availableReplicas": 2, "age": "2026-10-01T00:00:00+00:00", "labels": {"app": "web"}, "selector": {"app": "web"}},
            {"name": "billing", "namespace": "default", "readyReplicas": 1, "desiredReplicas": 1, "availableReplicas": 1, "age": "2026-10-01T00:00:00+00:00", "labels": {"app": "billing"}, "selector": {"app": "billing"}},
        ],
        "services": [
            {"name": "api", "namespace": "payments", "type": "ClusterIP", "clusterIP": "10.0.12.8", "ports": [{"name": "http", "port": 80, "targetPort": 8080, "protocol": "TCP"}], "selector": {"app": "api"}, "age": "2026-10-01T00:00:00+00:00"},
            {"name": "web", "namespace": "checkout", "type": "LoadBalancer", "clusterIP": "10.0.12.9", "externalIP": "203.0.113.10", "ports": [{"name": "http", "port": 80, "targetPort": 8080, "protocol": "TCP"}], "selector": {"app": "web"}, "age": "2026-10-01T00:00:00+00:00"},
        ],
        "ingresses": [
            {"name": "web", "namespace": "checkout", "className": "nginx", "hosts": ["shop.example.com"], "age": "2026-10-01T00:00:00+00:00"},
        ],
        "namespaces": [
            {"name": "payments", "status": "Active", "age": "2026-10-01T00:00:00+00:00"},
            {"name": "checkout", "status": "Active", "age": "2026-10-01T00:00:00+00:00"},
            {"name": "default", "status": "Active", "age": "2026-10-01T00:00:00+00:00"},
        ],
        "configmaps": [
            {"name": "api-config", "namespace": "payments", "keyCount": 2, "keys": [{"name": "LOG_LEVEL", "size": 4}, {"name": "REGION", "size": 12}], "age": "2026-10-01T00:00:00+00:00"},
        ],
    }


def staging_payload():
    data = prod_payload()
    data["summary"]["totalNodes"] = 2
    data["summary"]["readyNodes"] = 2
    data["summary"]["totalPods"] = 8
    data["nodes"] = data["nodes"][:2]
    data["pods"] = [p for p in data["pods"] if p["status"] == "Running"][:8]
    return data


def beat(name, secret, data):
    status, body = _request(
        "POST",
        "/api/heartbeat",
        {"cluster_name": name, "agent_version": "mock", "data": data},
        {"X-Secret-Key": secret},
    )
    print("%s heartbeat %s" % (name, status), flush=True)
    if status != 200:
        print(body, flush=True)


def main():
    secrets = {"prod": register("prod"), "staging": register("staging")}
    print("registered prod and staging", flush=True)
    started = time.time()
    staging_open = True
    while True:
        beat("prod", secrets["prod"], prod_payload())
        if staging_open and (time.time() - started) <= STAGING_LIFETIME:
            beat("staging", secrets["staging"], staging_payload())
        elif staging_open:
            staging_open = False
            print("staging stopped reporting", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
