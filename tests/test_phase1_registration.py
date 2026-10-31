"""Pending registration: a failed credential save must not lock the cluster name."""

import importlib
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), "..", "dashboard")
sys.path.insert(0, DASHBOARD_DIR)

ADMIN = {"X-Admin-Key": "master-bootstrap-key"}


class TestPendingRegistration(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._sys_path = list(sys.path)
        os.environ["STORAGE_BACKEND"] = "file"
        os.environ["STORAGE_PATH"] = os.path.join(self._tmpdir.name, "registry.json")
        os.environ["HISTORY_PATH"] = os.path.join(self._tmpdir.name, "history.json")
        os.environ["SECRET_KEY"] = "master-bootstrap-key"
        os.environ["ADMIN_KEY"] = "master-bootstrap-key"
        os.environ["DASHBOARD_URL"] = "https://dash.example"
        self._drop_dashboard_modules()
        sys.path = [self._sys_path[0]] + [
            p for p in self._sys_path[1:] if not p.rstrip("/").endswith("/agent")
        ]
        sys.path.insert(0, DASHBOARD_DIR)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._tmpdir.cleanup()
        sys.path = self._sys_path
        self._drop_dashboard_modules()

    def _drop_dashboard_modules(self):
        for mod in list(sys.modules):
            if mod in ("app", "storage", "history") or mod.startswith("app."):
                del sys.modules[mod]

    def _load_app(self):
        import app as dashboard_app

        return dashboard_app

    def _token(self, client, name="prod"):
        resp = client.post(
            "/api/generate-token",
            json={"cluster_name": name},
            headers=ADMIN,
        )
        self.assertEqual(resp.status_code, 200, resp.get_json())
        return resp.get_json()

    def _register(self, client, token):
        resp = client.post(
            "/api/register",
            json={"cluster_name": "prod", "agent_version": "1.0"},
            headers={"X-Registration-Token": token},
        )
        self.assertEqual(resp.status_code, 200, resp.get_json())
        return resp.get_json()

    def _heartbeat(self, client, name, key, nodes=None):
        return client.post(
            "/api/heartbeat",
            json={
                "cluster_name": name,
                "data": {"summary": {"totalNodes": 1}, "nodes": nodes if nodes is not None else []},
            },
            headers={"X-Secret-Key": key},
        )

    def test_pending_cluster_can_be_reregistered_until_heartbeat(self):
        dashboard = self._load_app()
        client = dashboard.app.test_client()
        first = self._token(client)
        reg = self._register(client, first["token"])
        cred1 = reg["cluster_credential"]

        clusters = client.get("/api/clusters", headers=ADMIN).get_json()
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["status"], "pending")
        self.assertFalse(clusters[0]["is_healthy"])

        spent = client.post(
            "/api/register",
            json={},
            headers={"X-Registration-Token": first["token"]},
        )
        self.assertEqual(spent.status_code, 401)

        second = self._token(client)
        self.assertNotEqual(second["token"], first["token"])
        reg2 = self._register(client, second["token"])
        cred2 = reg2["cluster_credential"]
        self.assertNotEqual(cred1, cred2)
        self.assertEqual(len(client.get("/api/clusters", headers=ADMIN).get_json()), 1)

        self.assertEqual(self._heartbeat(client, "prod", cred1).status_code, 401)
        self.assertEqual(
            self._heartbeat(client, "prod", "master-bootstrap-key").status_code,
            401,
        )
        self.assertEqual(self._heartbeat(client, "prod", cred2).status_code, 200)

        clusters = client.get("/api/clusters", headers=ADMIN).get_json()
        self.assertEqual(clusters[0]["status"], "active")
        taken = client.post(
            "/api/generate-token",
            json={"cluster_name": "prod"},
            headers=ADMIN,
        )
        self.assertEqual(taken.status_code, 409)

    def test_production_name_round_trip(self):
        dashboard = self._load_app()
        client = dashboard.app.test_client()
        issued = self._token(client, "Production")
        self.assertEqual(issued["cluster_name"], "production")
        self.assertIn("--set registrationToken=", issued["helm_command"])
        self.assertIn("clusterName=production", issued["helm_command"])
        self.assertNotIn("kubectl create secret", issued["helm_command"])

        reg = client.post(
            "/api/register",
            json={"cluster_name": "Production", "agent_version": "1.0"},
            headers={"X-Registration-Token": issued["token"]},
        )
        self.assertEqual(reg.status_code, 200, reg.get_json())
        body = reg.get_json()
        self.assertEqual(body["cluster_name"], "production")

        # The agent heartbeats the name returned by /api/register.
        exact = self._heartbeat(client, body["cluster_name"], body["cluster_credential"])
        self.assertEqual(exact.status_code, 200)
        # A later heartbeat that still uses the typed name resolves to the same cluster.
        mixed = self._heartbeat(client, "Production", body["cluster_credential"])
        self.assertEqual(mixed.status_code, 200)
        self.assertEqual(len(client.get("/api/clusters", headers=ADMIN).get_json()), 1)

        rejected = client.post(
            "/api/generate-token",
            json={"cluster_name": "my_cluster"},
            headers=ADMIN,
        )
        self.assertEqual(rejected.status_code, 400)

    def test_legacy_mixed_case_key_still_accepts_heartbeat(self):
        dashboard = self._load_app()
        dashboard.registry._clusters["Production"] = {
            "clusterName": "Production",
            "credentialHash": __import__("hashlib").sha256(b"legacy-cred").hexdigest(),
            "createdAt": "2020-01-01T00:00:00+00:00",
            "lastSeen": "2020-01-01T00:00:00+00:00",
            "agent_version": "0.9",
        }
        dashboard.registry._persist()
        client = dashboard.app.test_client()
        ok = self._heartbeat(client, "production", "legacy-cred")
        self.assertEqual(ok.status_code, 200)
        deleted = client.delete("/api/cluster/Production", headers=ADMIN)
        self.assertEqual(deleted.status_code, 200)

    def test_registry_write_failure_keeps_token_usable(self):
        dashboard = self._load_app()
        client = dashboard.app.test_client()
        token = self._token(client)["token"]
        with patch.object(dashboard.registry, "register_cluster", side_effect=OSError("disk full")):
            failed = client.post(
                "/api/register",
                json={"agent_version": "1.0"},
                headers={"X-Registration-Token": token},
            )
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(client.get("/api/clusters", headers=ADMIN).get_json(), [])
        retried = self._register(client, token)
        self.assertTrue(retried["cluster_credential"])

    def test_legacy_key_works_for_preexisting_cluster_only(self):
        dashboard = self._load_app()
        dashboard.registry._clusters["legacy"] = {
            "clusterName": "legacy",
            "credentialHash": "not-the-global-key",
            "createdAt": "2020-01-01T00:00:00+00:00",
            "lastSeen": "2020-01-01T00:00:00+00:00",
            "agent_version": "0.9",
        }
        dashboard.registry._persist()
        client = dashboard.app.test_client()
        ok = self._heartbeat(client, "legacy", "master-bootstrap-key")
        self.assertEqual(ok.status_code, 200)

        deleted = client.delete("/api/cluster/legacy", headers=ADMIN)
        self.assertEqual(deleted.status_code, 200)
        rejected = self._heartbeat(client, "legacy", "master-bootstrap-key")
        self.assertIn(rejected.status_code, (401, 400))
        self.assertEqual(client.get("/api/clusters", headers=ADMIN).get_json(), [])


class TestAgentSaveFailure(unittest.TestCase):
    def test_save_failure_exits_without_claiming_success(self):
        env_backup = dict(os.environ)
        saved_path = list(sys.path)
        os.environ.pop("DASHBOARD_URL", None)
        os.environ["PERSISTENCE_MODE"] = "memory"
        agent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agent"))
        sys.path = [p for p in sys.path if os.path.abspath(p) != os.path.abspath(DASHBOARD_DIR)]
        sys.path.insert(0, agent_dir)
        for mod in ("app", "persistence", "provider"):
            sys.modules.pop(mod, None)
        try:
            with patch("kubernetes.config.load_incluster_config", side_effect=Exception("no k8s")):
                import app as agent_app

            response = Mock()
            response.status_code = 200
            response.json.return_value = {
                "cluster_credential": "issued-cred",
                "cluster_name": "prod",
            }
            agent_app.DASHBOARD_URL = "https://dash.example"
            agent_app.CLUSTER_NAME = "prod"
            agent_app.REGISTRATION_TOKEN = "tok"
            agent_app.SECRET_KEY = ""
            agent_app._agent_credential = None
            with patch.object(agent_app, "load_credential", return_value=(None, None)):
                with patch.object(agent_app.requests, "post", return_value=response):
                    with patch.object(agent_app, "save_credential", side_effect=OSError("denied")):
                        with patch.object(agent_app, "send_one_heartbeat") as heartbeat:
                            with self.assertRaises(SystemExit):
                                agent_app.start_agent()
                            heartbeat.assert_not_called()
        finally:
            os.environ.clear()
            os.environ.update(env_backup)
            sys.path = saved_path
            for mod in ("app", "persistence", "provider"):
                sys.modules.pop(mod, None)

    def test_register_uses_canonical_name_from_dashboard(self):
        env_backup = dict(os.environ)
        saved_path = list(sys.path)
        os.environ.pop("DASHBOARD_URL", None)
        os.environ["PERSISTENCE_MODE"] = "memory"
        os.environ["CLUSTER_NAME"] = "Production"
        agent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agent"))
        sys.path = [p for p in sys.path if os.path.abspath(p) != os.path.abspath(DASHBOARD_DIR)]
        sys.path.insert(0, agent_dir)
        for mod in ("app", "persistence", "provider", "common", "common.cluster_name"):
            sys.modules.pop(mod, None)
        try:
            with patch("kubernetes.config.load_incluster_config", side_effect=Exception("no k8s")):
                import app as agent_app

            response = Mock()
            response.status_code = 200
            response.json.return_value = {
                "cluster_credential": "issued-cred",
                "cluster_name": "production",
            }
            agent_app.DASHBOARD_URL = "https://dash.example"
            agent_app.CLUSTER_NAME = "Production"
            agent_app.REGISTRATION_TOKEN = "tok"
            agent_app.SECRET_KEY = ""
            agent_app._agent_credential = None
            with patch.object(agent_app, "load_credential", return_value=(None, None)):
                with patch.object(agent_app.requests, "post", return_value=response):
                    with patch.object(agent_app, "save_credential") as save:
                        with patch.object(agent_app, "send_one_heartbeat"):
                            with patch.object(agent_app.threading, "Thread"):
                                agent_app.start_agent()
            self.assertEqual(agent_app.CLUSTER_NAME, "production")
            save.assert_called_once()
            self.assertEqual(save.call_args.args[1], "production")
        finally:
            os.environ.clear()
            os.environ.update(env_backup)
            sys.path = saved_path
            for mod in ("app", "persistence", "provider"):
                sys.modules.pop(mod, None)
