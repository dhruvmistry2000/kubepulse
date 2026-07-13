"""
Append-only node history for scaling timelines and retired-node tracking.

Stored in the same durable backend pattern as the cluster registry.
"""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

STORAGE_BACKEND = (os.environ.get("STORAGE_BACKEND") or "file").strip().lower()
HISTORY_PATH = (os.environ.get("HISTORY_PATH") or "/data/kubepulse/history.json").strip()
GCS_BUCKET = (os.environ.get("GCS_BUCKET") or "").strip()
GCS_HISTORY_OBJECT = (os.environ.get("GCS_HISTORY_OBJECT") or "kubepulse/history.json").strip()
HISTORY_RETENTION_DAYS = int(os.environ.get("HISTORY_RETENTION_DAYS") or "30")
SCALING_INTERVAL_SECONDS = int(os.environ.get("SCALING_INTERVAL_SECONDS") or "300")
HEARTBEAT_INTERVAL_SECONDS = int(os.environ.get("HEARTBEAT_INTERVAL_SECONDS") or "30")
HOURS_PER_MONTH = 730


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _cpu_pct(usage, allocatable):
    if not usage or not allocatable:
        return None
    import re

    def cpu_millis(s):
        s = str(s).strip()
        if s.endswith("n"):
            return float(s[:-1]) / 1e6
        if s.endswith("m"):
            return float(s[:-1])
        try:
            return float(s) * 1000
        except ValueError:
            return None

    def mem_bytes(s):
        s = str(s).strip()
        m = re.match(r"^([\d.]+)\s*([KMGTPkmgtp]i?)?$", s)
        if not m:
            return None
        n = float(m.group(1))
        u = (m.group(2) or "").lower()
        mult = {"ki": 1024, "mi": 1024 ** 2, "gi": 1024 ** 3}.get(u, 1)
        return n * mult

    used = cpu_millis(usage)
    alloc = cpu_millis(allocatable)
    if used is None or not alloc:
        return None
    return min(100, round((used / alloc) * 100))


def _mem_pct(usage, allocatable):
    if not usage or not allocatable:
        return None
    import re

    def mem_bytes(s):
        s = str(s).strip()
        m = re.match(r"^([\d.]+)\s*([KMGTPkmgtp]i?)?$", s)
        if not m:
            return None
        n = float(m.group(1))
        u = (m.group(2) or "").lower()
        mult = {"ki": 1024, "mi": 1024 ** 2, "gi": 1024 ** 3}.get(u, 1)
        return n * mult

    used = mem_bytes(usage)
    alloc = mem_bytes(allocatable)
    if used is None or not alloc:
        return None
    return min(100, round((used / alloc) * 100))


class HistoryStore:
    """Thread-safe scaling + retired-node history."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = self._empty()
        self._load()

    def _empty(self):
        return {"version": 1, "scaling": {}, "nodes": {}}

    def _load_file(self):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return self._empty()
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load history from %s: %s", HISTORY_PATH, exc)
            return self._empty()

    def _save_file(self, data):
        directory = os.path.dirname(HISTORY_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=directory or None, prefix=".history-", suffix=".json"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp_path, HISTORY_PATH)
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
            raise RuntimeError("google-cloud-storage required for gcs history") from exc
        if not GCS_BUCKET:
            raise RuntimeError("GCS_BUCKET required for gcs history")
        client = storage.Client()
        blob = client.bucket(GCS_BUCKET).blob(GCS_HISTORY_OBJECT)
        if not blob.exists():
            return self._empty()
        return json.loads(blob.download_as_text())

    def _save_gcs(self, data):
        try:
            from google.cloud import storage
        except ImportError as exc:
            raise RuntimeError("google-cloud-storage required for gcs history") from exc
        client = storage.Client()
        blob = client.bucket(GCS_BUCKET).blob(GCS_HISTORY_OBJECT)
        blob.upload_from_string(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            content_type="application/json",
        )

    def _load(self):
        if STORAGE_BACKEND == "gcs":
            raw = self._load_gcs()
        else:
            raw = self._load_file()
        with self._lock:
            self._data = raw if isinstance(raw, dict) else self._empty()
            if "scaling" not in self._data:
                self._data["scaling"] = {}
            if "nodes" not in self._data:
                self._data["nodes"] = {}

    def _persist(self):
        if STORAGE_BACKEND == "gcs":
            self._save_gcs(self._data)
        else:
            self._save_file(self._data)

    def _prune(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_RETENTION_DAYS)
        scaling = self._data.get("scaling") or {}
        for cluster, points in list(scaling.items()):
            kept = []
            for pt in points or []:
                ts = _parse_iso(pt.get("ts"))
                if ts and ts >= cutoff:
                    kept.append(pt)
            scaling[cluster] = kept[-500:]
        self._data["scaling"] = scaling

        nodes = self._data.get("nodes") or {}
        to_delete = []
        for key, rec in nodes.items():
            last = _parse_iso(rec.get("lastSeen"))
            if rec.get("retired") and last and last < cutoff:
                to_delete.append(key)
        for key in to_delete:
            del nodes[key]

    def record_heartbeat(self, cluster_name, nodes, cost_summary=None):
        """Update scaling timeline and per-node lifetime records from a heartbeat."""
        now = _now_iso()
        node_count = len(nodes or [])
        interval_hours = HEARTBEAT_INTERVAL_SECONDS / 3600.0

        with self._lock:
            scaling = self._data.setdefault("scaling", {})
            points = scaling.setdefault(cluster_name, [])
            should_append = True
            if points:
                last = points[-1]
                last_ts = _parse_iso(last.get("ts"))
                if (
                    last.get("nodeCount") == node_count
                    and last_ts
                    and (datetime.now(timezone.utc) - last_ts).total_seconds()
                    < SCALING_INTERVAL_SECONDS
                ):
                    should_append = False
            if should_append:
                points.append({"ts": now, "nodeCount": node_count})
                scaling[cluster_name] = points[-500:]

            node_records = self._data.setdefault("nodes", {})
            seen_keys = set()
            for n in nodes or []:
                name = n.get("name")
                if not name:
                    continue
                key = f"{cluster_name}:{name}"
                seen_keys.add(key)
                rec = node_records.get(key) or {
                    "cluster": cluster_name,
                    "nodeName": name,
                    "provider": n.get("provider"),
                    "instanceType": n.get("instanceType"),
                    "region": n.get("region"),
                    "firstSeen": now,
                    "lastSeen": now,
                    "retired": False,
                    "peakCpuPct": None,
                    "peakMemPct": None,
                    "accruedCostUsd": 0.0,
                }
                rec["lastSeen"] = now
                rec["retired"] = False
                rec["provider"] = n.get("provider") or rec.get("provider")
                rec["instanceType"] = n.get("instanceType") or rec.get("instanceType")
                rec["region"] = n.get("region") or rec.get("region")

                cpu_pct = _cpu_pct(n.get("cpuUsage"), n.get("allocatableCPU"))
                mem_pct = _mem_pct(n.get("memoryUsage"), n.get("allocatableMemory"))
                if cpu_pct is not None:
                    rec["peakCpuPct"] = max(rec.get("peakCpuPct") or 0, cpu_pct)
                if mem_pct is not None:
                    rec["peakMemPct"] = max(rec.get("peakMemPct") or 0, mem_pct)

                hourly = n.get("hourlyUsd")
                if n.get("priced") and hourly:
                    rec["accruedCostUsd"] = round(
                        (rec.get("accruedCostUsd") or 0) + hourly * interval_hours, 4
                    )
                node_records[key] = rec

            prefix = f"{cluster_name}:"
            for key, rec in list(node_records.items()):
                if not key.startswith(prefix):
                    continue
                if key not in seen_keys and not rec.get("retired"):
                    rec["retired"] = True
                    node_records[key] = rec

            self._prune()
            self._persist()

    def get_scaling(self, cluster_name=None):
        with self._lock:
            scaling = self._data.get("scaling") or {}
            if cluster_name:
                return {cluster_name: list(scaling.get(cluster_name) or [])}
            return {k: list(v) for k, v in scaling.items()}

    def get_retired_nodes(self, cluster_name=None):
        with self._lock:
            nodes = self._data.get("nodes") or {}
            out = []
            for rec in nodes.values():
                if not rec.get("retired"):
                    continue
                if cluster_name and rec.get("cluster") != cluster_name:
                    continue
                out.append(dict(rec))
            out.sort(key=lambda r: r.get("lastSeen") or "", reverse=True)
            return out

    def get_fleet_cost_from_live(self, live_data_by_cluster):
        """Aggregate live cost payloads across clusters."""
        total_hourly = 0.0
        total_monthly = 0.0
        by_provider = {}
        by_cluster = {}
        for cluster, data in (live_data_by_cluster or {}).items():
            cost = (data or {}).get("cost") or {}
            hourly = cost.get("hourlyUsd") or 0
            monthly = cost.get("monthlyUsd") or 0
            by_cluster[cluster] = {
                "hourlyUsd": hourly,
                "monthlyUsd": monthly,
                "pricedNodes": cost.get("pricedNodes", 0),
                "unpricedNodes": cost.get("unpricedNodes", 0),
            }
            total_hourly += hourly
            total_monthly += monthly
            for prov, pdata in (cost.get("byProvider") or {}).items():
                entry = by_provider.setdefault(
                    prov, {"hourlyUsd": 0.0, "monthlyUsd": 0.0, "nodeCount": 0}
                )
                entry["hourlyUsd"] += pdata.get("hourlyUsd") or 0
                entry["monthlyUsd"] += pdata.get("monthlyUsd") or 0
                entry["nodeCount"] += pdata.get("nodeCount") or 0
        return {
            "hourlyUsd": round(total_hourly, 4),
            "monthlyUsd": round(total_monthly, 2),
            "byProvider": by_provider,
            "byCluster": by_cluster,
        }

    def delete_cluster(self, cluster_name):
        with self._lock:
            scaling = self._data.get("scaling") or {}
            scaling.pop(cluster_name, None)
            nodes = self._data.get("nodes") or {}
            to_del = [k for k in nodes if k.startswith(f"{cluster_name}:")]
            for k in to_del:
                del nodes[k]
            self._persist()
