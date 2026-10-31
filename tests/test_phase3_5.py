"""History, memory quantities, heartbeats, and agent collection edges."""

import gzip
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DASHBOARD_DIR = os.path.join(ROOT, "dashboard")
AGENT_DIR = os.path.join(ROOT, "agent")
sys.path.insert(0, ROOT)

from common.quantity import parse_memory_bytes


class TestMemoryQuantities(unittest.TestCase):
    def test_each_suffix(self):
        expected = {
            "1Ki": 1024,
            "1Mi": 1024 ** 2,
            "1Gi": 1024 ** 3,
            "1Ti": 1024 ** 4,
            "1Pi": 1024 ** 5,
            "1Ei": 1024 ** 6,
            "1k": 1000,
            "1M": 1000 ** 2,
            "1G": 1000 ** 3,
            "1T": 1000 ** 4,
            "1P": 1000 ** 5,
            "1E": 1000 ** 6,
            "1m": 0.001,
            "4Gi": 4 * 1024 ** 3,
        }
        for text, nbytes in expected.items():
            self.assertEqual(parse_memory_bytes(text), nbytes, text)
        self.assertIsNone(parse_memory_bytes("nope"))
        self.assertIsNone(parse_memory_bytes(""))


class TestHistoryEdges(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._sys_path = list(sys.path)
        os.environ["STORAGE_BACKEND"] = "file"
        os.environ["HISTORY_PATH"] = os.path.join(self._tmpdir.name, "history.json")
        os.environ["HISTORY_RETENTION_DAYS"] = "30"
        os.environ["SCALING_INTERVAL_SECONDS"] = "0"
        os.environ["STORE_WRITE_DEBOUNCE_SECONDS"] = "1"
        sys.path.insert(0, DASHBOARD_DIR)
        self._drop_history()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._tmpdir.cleanup()
        sys.path = self._sys_path
        self._drop_history()

    def _drop_history(self):
        sys.modules.pop("history", None)

    def _store(self):
        self._drop_history()
        from history import HistoryStore

        return HistoryStore()

    def _node(self, name="node-a", uid=""):
        return {
            "name": name,
            "uid": uid,
            "provider": "gcp",
            "instanceType": "e2-medium",
            "region": "us-central1",
            "priced": True,
            "hourlyUsd": 0.05,
            "cpuUsage": "500m",
            "allocatableCPU": "2",
            "memoryUsage": "1Gi",
            "allocatableMemory": "4Gi",
        }

    def test_failed_node_list_does_not_retire_or_scale_to_zero(self):
        store = self._store()
        store.record_heartbeat("prod", [self._node()])
        before = store.get_scaling("prod")["prod"]
        store.record_heartbeat("prod", [], nodes_authoritative=False)
        self.assertEqual(store.get_retired_nodes("prod"), [])
        self.assertEqual(store.get_scaling("prod")["prod"], before)

    def test_accrual_uses_elapsed_time_and_caps_it(self):
        store = self._store()
        node = self._node()
        store.record_heartbeat("prod", [node])
        key = "prod:node-a"
        self.assertEqual(store._data["nodes"][key]["accruedCostUsd"], 0.0)

        seen = datetime.now(timezone.utc) - timedelta(seconds=10)
        store._data["nodes"][key]["lastSeen"] = seen.isoformat()
        store.record_heartbeat("prod", [node])
        self.assertAlmostEqual(
            store._data["nodes"][key]["accruedCostUsd"],
            round(0.05 * 10 / 3600.0, 4),
        )

        seen = datetime.now(timezone.utc) - timedelta(seconds=120)
        store._data["nodes"][key]["lastSeen"] = seen.isoformat()
        before = store._data["nodes"][key]["accruedCostUsd"]
        store.record_heartbeat("prod", [node])
        self.assertAlmostEqual(
            store._data["nodes"][key]["accruedCostUsd"] - before,
            round(0.05 * 60 / 3600.0, 4),
        )

    def test_node_uid_keeps_reused_names_apart(self):
        store = self._store()
        store.record_heartbeat(
            "prod",
            [self._node(uid="uid-a"), self._node(uid="uid-b")],
        )
        self.assertIn("prod:uid-a:node-a", store._data["nodes"])
        self.assertIn("prod:uid-b:node-a", store._data["nodes"])
        store.record_heartbeat("prod", [self._node(uid="uid-a")])
        retired = store.get_retired_nodes("prod")
        self.assertEqual([item["uid"] for item in retired], ["uid-b"])

    def test_later_history_write_is_debounced(self):
        store = self._store()
        store.record_heartbeat("prod", [self._node()])
        with open(os.environ["HISTORY_PATH"], encoding="utf-8") as fh:
            first = json.load(fh)
        store._data["nodes"]["prod:node-a"]["lastSeen"] = (
            datetime.now(timezone.utc) - timedelta(seconds=10)
        ).isoformat()
        store.record_heartbeat("prod", [self._node()])
        with open(os.environ["HISTORY_PATH"], encoding="utf-8") as fh:
            during = json.load(fh)
        self.assertEqual(during, first)
        time.sleep(1.4)
        with open(os.environ["HISTORY_PATH"], encoding="utf-8") as fh:
            after = json.load(fh)
        self.assertNotEqual(after["nodes"]["prod:node-a"]["accruedCostUsd"], 0)


class TestHeartbeatGuards(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._sys_path = list(sys.path)
        os.environ["STORAGE_BACKEND"] = "file"
        os.environ["STORAGE_PATH"] = os.path.join(self._tmpdir.name, "registry.json")
        os.environ["HISTORY_PATH"] = os.path.join(self._tmpdir.name, "history.json")
        os.environ["SCALING_INTERVAL_SECONDS"] = "0"
        os.environ["SECRET_KEY"] = "legacy-heartbeat-key"
        os.environ["ADMIN_KEY"] = "admin-key"
        os.environ["DASHBOARD_URL"] = "https://dash.example"
        os.environ["MAX_CONTENT_LENGTH"] = "512"
        os.environ.pop("VIEWER_KEY", None)
        self._drop()
        sys.path.insert(0, DASHBOARD_DIR)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._tmpdir.cleanup()
        sys.path = self._sys_path
        self._drop()

    def _drop(self):
        for mod in list(sys.modules):
            if mod in ("app", "storage", "history", "pricing") or mod.startswith("app."):
                del sys.modules[mod]

    def _app(self):
        import app as dashboard_app

        dashboard_app._register_hits.clear()
        return dashboard_app

    def _activate(self, client, name="prod"):
        token = client.post(
            "/api/generate-token",
            json={"cluster_name": name},
            headers={"X-Admin-Key": "admin-key"},
        )
        self.assertEqual(token.status_code, 200, token.get_json())
        registered = client.post(
            "/api/register",
            json={"cluster_name": name, "agent_version": "1.0"},
            headers={"X-Registration-Token": token.get_json()["token"]},
        )
        self.assertEqual(registered.status_code, 200, registered.get_json())
        credential = registered.get_json()["cluster_credential"]
        beat = client.post(
            "/api/heartbeat",
            json={
                "cluster_name": name,
                "data": {
                    "nodesAuthoritative": True,
                    "nodes": [{"name": "node-a", "hourlyUsd": 0.05, "priced": True}],
                },
            },
            headers={"X-Secret-Key": credential},
        )
        self.assertEqual(beat.status_code, 200, beat.get_json())
        return credential

    def test_gzip_and_plain_heartbeats(self):
        dashboard = self._app()
        client = dashboard.app.test_client()
        credential = self._activate(client)
        payload = {
            "cluster_name": "prod",
            "data": {"nodesAuthoritative": True, "nodes": [{"name": "node-a"}]},
        }
        raw = gzip.compress(json.dumps(payload).encode("utf-8"))
        resp = client.post(
            "/api/heartbeat",
            data=raw,
            headers={
                "X-Secret-Key": credential,
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.get_json())

    def test_register_is_throttled_per_ip(self):
        dashboard = self._app()
        client = dashboard.app.test_client()
        for _ in range(10):
            resp = client.post("/api/register")
            self.assertEqual(resp.status_code, 401)
        blocked = client.post("/api/register")
        self.assertEqual(blocked.status_code, 429)

    def test_oversized_body_is_rejected(self):
        dashboard = self._app()
        client = dashboard.app.test_client()
        resp = client.post(
            "/api/heartbeat",
            data=b"x" * 600,
            headers={"Content-Type": "application/json", "X-Secret-Key": "k"},
        )
        self.assertEqual(resp.status_code, 413)

    def test_heartbeat_schema(self):
        dashboard = self._app()
        client = dashboard.app.test_client()
        credential = self._activate(client)
        bad_name = client.post(
            "/api/heartbeat",
            json={"cluster_name": 1, "data": {}},
            headers={"X-Secret-Key": credential},
        )
        self.assertEqual(bad_name.status_code, 400)
        bad_data = client.post(
            "/api/heartbeat",
            json={"cluster_name": "prod", "data": ["nope"]},
            headers={"X-Secret-Key": credential},
        )
        self.assertEqual(bad_data.status_code, 400)

    def test_failed_node_snapshot_keeps_previous_nodes(self):
        dashboard = self._app()
        client = dashboard.app.test_client()
        credential = self._activate(client)
        failed = client.post(
            "/api/heartbeat",
            json={
                "cluster_name": "prod",
                "data": {"nodesAuthoritative": False, "nodes": []},
            },
            headers={"X-Secret-Key": credential},
        )
        self.assertEqual(failed.status_code, 200)
        nodes = client.get(
            "/api/cluster/prod/nodes", headers={"X-Admin-Key": "admin-key"}
        ).get_json()
        self.assertEqual(nodes["items"][0]["name"], "node-a")
        retired = client.get(
            "/api/cluster/prod/retired-nodes", headers={"X-Admin-Key": "admin-key"}
        ).get_json()
        self.assertEqual(retired["items"], [])
        scaling = client.get(
            "/api/cluster/prod/scaling", headers={"X-Admin-Key": "admin-key"}
        ).get_json()
        counts = [point["nodeCount"] for point in scaling["scaling"]["prod"]]
        self.assertNotIn(0, counts)

    def test_delete_removes_state_and_legacy_key_cannot_recreate(self):
        dashboard = self._app()
        client = dashboard.app.test_client()
        credential = self._activate(client)
        deleted = client.delete(
            "/api/cluster/prod", headers={"X-Admin-Key": "admin-key"}
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertNotIn("prod", dashboard.live_data)
        self.assertFalse(dashboard.registry.cluster_exists("prod"))
        self.assertEqual(dashboard.history.get_retired_nodes("prod"), [])
        legacy = client.post(
            "/api/heartbeat",
            json={"cluster_name": "prod", "data": {"nodes": [{"name": "node-a"}]}},
            headers={"X-Secret-Key": "legacy-heartbeat-key"},
        )
        self.assertEqual(legacy.status_code, 401)
        again = client.post(
            "/api/heartbeat",
            json={"cluster_name": "prod", "data": {"nodes": [{"name": "node-a"}]}},
            headers={"X-Secret-Key": credential},
        )
        self.assertEqual(again.status_code, 401)
        self.assertFalse(dashboard.registry.cluster_exists("prod"))


class TestPriceLookupDoesNotBlock(unittest.TestCase):
    def setUp(self):
        self._sys_path = list(sys.path)
        sys.path.insert(0, DASHBOARD_DIR)
        sys.modules.pop("pricing", None)

    def tearDown(self):
        sys.path = self._sys_path
        sys.modules.pop("pricing", None)

    def test_cache_miss_returns_without_downloading(self):
        from pricing import PriceLookup

        lookup = PriceLookup()
        gate = threading.Event()

        def blocked(*_args, **_kwargs):
            gate.wait(2)
            raise OSError("stopped")

        with patch("pricing.requests.get", side_effect=blocked):
            started = time.time()
            result = lookup.lookup_aws_live("us-east-1", "m5.large")
            elapsed = time.time() - started
            gate.set()
            time.sleep(0.2)
        self.assertIsNone(result)
        self.assertLess(elapsed, 0.5)

    def test_memory_gib_uses_binary_and_milli(self):
        from pricing import _parse_memory_gib

        self.assertEqual(_parse_memory_gib("4Gi"), 4)
        self.assertLess(_parse_memory_gib("1m"), 0.001)


class TestAgentCollection(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        os.environ.pop("DASHBOARD_URL", None)
        self._sys_path = list(sys.path)
        sys.path.insert(0, AGENT_DIR)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        sys.path = self._sys_path
        sys.modules.pop("kubepulse_agent_phase35", None)

    def _agent(self):
        path = os.path.join(AGENT_DIR, "app.py")
        spec = importlib.util.spec_from_file_location("kubepulse_agent_phase35", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["kubepulse_agent_phase35"] = module
        spec.loader.exec_module(module)
        return module

    def test_metrics_server_absence_returns_empty(self):
        agent = self._agent()
        agent.METRICS_AVAILABLE = True
        agent.custom_objects = Mock()
        agent.custom_objects.list_cluster_custom_object.side_effect = Exception(
            "metrics server absent"
        )
        self.assertEqual(agent.get_node_metrics(), {})

    def test_replicaset_owner_is_keyed_by_namespace(self):
        agent = self._agent()

        class Owner:
            def __init__(self, kind, name):
                self.kind = kind
                self.name = name

        class Meta:
            def __init__(self, namespace, name, deployment):
                self.namespace = namespace
                self.name = name
                self.owner_references = [Owner("Deployment", deployment)]

        class ReplicaSet:
            def __init__(self, namespace, name, deployment):
                self.metadata = Meta(namespace, name, deployment)

        class ReplicaSetList:
            def __init__(self, items):
                self.items = items

        apps = Mock()
        apps.list_replicaset_for_all_namespaces.return_value = ReplicaSetList(
            [
                ReplicaSet("alpha", "web", "web-alpha"),
                ReplicaSet("beta", "web", "web-beta"),
            ]
        )
        mapped = agent.get_replicaset_owner_map(apps)
        self.assertEqual(mapped[("alpha", "web")], "web-alpha")
        self.assertEqual(mapped[("beta", "web")], "web-beta")

    def test_healthz_tracks_heartbeat_success(self):
        agent = self._agent()
        client = agent.app.test_client()
        self.assertEqual(client.get("/healthz").status_code, 200)
        agent.DASHBOARD_URL = "https://dash.example"
        agent._last_heartbeat_ok = 0
        self.assertEqual(client.get("/healthz").status_code, 503)
        agent._last_heartbeat_ok = time.time()
        self.assertEqual(client.get("/healthz").status_code, 200)
        agent._last_heartbeat_ok = time.time() - (2 * agent.HEARTBEAT_INTERVAL + 1)
        stale = client.get("/healthz")
        self.assertEqual(stale.status_code, 503)
        self.assertEqual(stale.get_json()["status"], "stale")


if __name__ == "__main__":
    unittest.main()
