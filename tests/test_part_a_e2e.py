"""End-to-end test for durable registration across dashboard restarts."""

import importlib
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), "..", "dashboard")
sys.path.insert(0, DASHBOARD_DIR)


class TestRegistrationEndToEnd(unittest.TestCase):
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

        for mod in list(sys.modules):
            if mod in ("app", "storage", "history") or mod.startswith("app."):
                del sys.modules[mod]
        sys.path = [self._sys_path[0]] + [
            p for p in self._sys_path[1:] if not p.rstrip("/").endswith("/agent")
        ]
        sys.path.insert(0, DASHBOARD_DIR)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._tmpdir.cleanup()
        sys.path = self._sys_path
        for mod in list(sys.modules):
            if mod in ("app", "storage", "history") or mod.startswith("app."):
                del sys.modules[mod]

    def _load_app(self):
        import app as dashboard_app

        return dashboard_app

    def test_register_heartbeat_survives_dashboard_restart(self):
        dashboard = self._load_app()
        client = dashboard.app.test_client()

        token_resp = client.post(
            "/api/generate-token",
            json={"cluster_name": "prod"},
            headers={"X-Admin-Key": "master-bootstrap-key"},
        )
        self.assertEqual(token_resp.status_code, 200)
        token = token_resp.get_json()["token"]

        reg_resp = client.post(
            "/api/register",
            json={"cluster_name": "prod", "agent_version": "1.0"},
            headers={"X-Registration-Token": token},
        )
        self.assertEqual(reg_resp.status_code, 200)
        credential = reg_resp.get_json()["cluster_credential"]
        self.assertTrue(credential)

        hb_resp = client.post(
            "/api/heartbeat",
            json={
                "cluster_name": "prod",
                "data": {"summary": {"totalNodes": 3}},
            },
            headers={"X-Secret-Key": credential},
        )
        self.assertEqual(hb_resp.status_code, 200)

        clusters = client.get("/api/clusters", headers={"X-Admin-Key": "master-bootstrap-key"}).get_json()
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["name"], "prod")
        self.assertEqual(clusters[0]["summary"]["totalNodes"], 3)

        # Simulate dashboard pod restart: reload app module and registry from disk.
        for mod in ("app", "storage"):
            sys.modules.pop(mod, None)
        dashboard2 = self._load_app()
        client2 = dashboard2.app.test_client()

        clusters_after_restart = client2.get("/api/clusters", headers={"X-Admin-Key": "master-bootstrap-key"}).get_json()
        self.assertEqual(len(clusters_after_restart), 1)
        self.assertEqual(clusters_after_restart[0]["name"], "prod")
        self.assertIsNone(clusters_after_restart[0]["summary"])

        hb2 = client2.post(
            "/api/heartbeat",
            json={
                "cluster_name": "prod",
                "data": {"summary": {"totalNodes": 5}},
            },
            headers={"X-Secret-Key": credential},
        )
        self.assertEqual(hb2.status_code, 200)

        clusters_refreshed = client2.get("/api/clusters", headers={"X-Admin-Key": "master-bootstrap-key"}).get_json()
        self.assertEqual(clusters_refreshed[0]["summary"]["totalNodes"], 5)

    def test_helm_upgrade_without_token_uses_stored_credential(self):
        """Agent with stored credential should not need registration token."""
        agent_dir = os.path.join(os.path.dirname(__file__), "..", "agent")
        saved_path = list(sys.path)
        for mod in list(sys.modules):
            if mod in ("persistence", "app") or mod.startswith("app."):
                sys.modules.pop(mod, None)
        sys.path = [p for p in sys.path if os.path.abspath(p) != os.path.abspath(DASHBOARD_DIR)]
        sys.path.insert(0, agent_dir)

        os.environ["PERSISTENCE_MODE"] = "file"
        try:
            with tempfile.TemporaryDirectory() as agent_tmp:
                cred_path = os.path.join(agent_tmp, "credential.json")
                os.environ["PERSISTENCE_FILE_PATH"] = cred_path
                os.environ.pop("REGISTRATION_TOKEN", None)
                os.environ.pop("DASHBOARD_URL", None)

                persistence = importlib.import_module("persistence")
                persistence.save_credential("stored-per-cluster-cred", "prod")

                with patch("kubernetes.config.load_incluster_config", side_effect=Exception("no k8s")):
                    with patch("app.do_register") as mock_register:
                        with patch("app.send_one_heartbeat"):
                            with patch("app.threading.Thread"):
                                import app as agent_app

                                agent_app.DASHBOARD_URL = "https://dash.example"
                                agent_app.CLUSTER_NAME = "prod"
                                agent_app.SECRET_KEY = ""
                                agent_app._agent_credential = None
                                agent_app.start_agent()
                                mock_register.assert_not_called()
                                self.assertEqual(agent_app._agent_credential, "stored-per-cluster-cred")
        finally:
            sys.path = saved_path
            for mod in list(sys.modules):
                if mod in ("persistence", "app") or mod.startswith("app."):
                    sys.modules.pop(mod, None)


if __name__ == "__main__":
    unittest.main()
