"""Unit tests for durable registration (Part A)."""

import importlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from kubernetes.client.rest import ApiException

# --- Agent persistence tests ---

AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "agent")
DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), "..", "dashboard")
sys.path.insert(0, AGENT_DIR)


class TestAgentCredentialPersistence(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        for mod in ("persistence",):
            sys.modules.pop(mod, None)

    def _import_persistence(self):
        if "persistence" in sys.modules:
            del sys.modules["persistence"]
        return importlib.import_module("persistence")

    def test_file_mode_round_trip(self):
        os.environ["PERSISTENCE_MODE"] = "file"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "credential.json")
            os.environ["PERSISTENCE_FILE_PATH"] = path
            persistence = self._import_persistence()
            persistence.save_credential("cred-abc", "prod-cluster")
            cred, name = persistence.load_credential(expected_cluster_name="prod-cluster")
            self.assertEqual(cred, "cred-abc")
            self.assertEqual(name, "prod-cluster")

    def test_memory_mode_skips_persistence(self):
        os.environ["PERSISTENCE_MODE"] = "memory"
        persistence = self._import_persistence()
        persistence.save_credential("cred-xyz", "dev")
        cred, name = persistence.load_credential()
        self.assertIsNone(cred)
        self.assertIsNone(name)

    def test_cluster_name_mismatch_ignored(self):
        os.environ["PERSISTENCE_MODE"] = "file"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "credential.json")
            os.environ["PERSISTENCE_FILE_PATH"] = path
            persistence = self._import_persistence()
            persistence.save_credential("cred-abc", "other-cluster")
            cred, _ = persistence.load_credential(expected_cluster_name="prod-cluster")
            self.assertIsNone(cred)

    def test_secret_mode_read_write(self):
        os.environ["PERSISTENCE_MODE"] = "secret"
        os.environ["STATE_SECRET_NAME"] = "kubepulse-agent-state"
        os.environ["POD_NAMESPACE"] = "default"
        persistence = self._import_persistence()

        core_v1 = MagicMock()
        not_found = ApiException(status=404)
        core_v1.read_namespaced_secret.side_effect = not_found

        persistence.save_credential("secret-cred", "prod", core_v1=core_v1, namespace="default")
        core_v1.create_namespaced_secret.assert_called_once()

        secret_data = core_v1.create_namespaced_secret.call_args[0][1]["data"]
        core_v1.read_namespaced_secret.side_effect = None
        core_v1.read_namespaced_secret.return_value = MagicMock(data=secret_data)
        cred, name = persistence.load_credential(
            core_v1=core_v1,
            namespace="default",
            expected_cluster_name="prod",
        )
        self.assertEqual(cred, "secret-cred")
        self.assertEqual(name, "prod")


class TestCredentialResumeDecision(unittest.TestCase):
    """Test start_agent credential resolution order."""

    def test_resume_before_register(self):
        """Stored credential should be used without registration token."""
        env_backup = dict(os.environ)
        saved_path = list(sys.path)
        mods_to_drop = [k for k in sys.modules if k in ("app", "persistence") or k.startswith("app.")]
        for k in mods_to_drop:
            sys.modules.pop(k, None)
        agent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agent"))
        sys.path = [p for p in sys.path if os.path.abspath(p) != os.path.abspath(DASHBOARD_DIR)]
        sys.path.insert(0, agent_dir)
        try:
            os.environ.pop("DASHBOARD_URL", None)
            with patch("kubernetes.config.load_incluster_config", side_effect=Exception("no k8s")):
                import app as agent_app

                with patch.object(agent_app, "load_credential", return_value=("persisted-credential", "my-cluster")):
                    with patch.object(agent_app, "do_register") as mock_register:
                        with patch.object(agent_app, "send_one_heartbeat"):
                            with patch.object(agent_app.threading, "Thread"):
                                agent_app.DASHBOARD_URL = "https://dashboard.example"
                                agent_app._agent_credential = None
                                agent_app.SECRET_KEY = ""
                                agent_app.start_agent()
                                mock_register.assert_not_called()
                                self.assertEqual(agent_app._agent_credential, "persisted-credential")
        finally:
            os.environ.clear()
            os.environ.update(env_backup)
            sys.path = saved_path
            for k in mods_to_drop:
                sys.modules.pop(k, None)


# --- Dashboard registry tests ---

DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), "..", "dashboard")
sys.path.insert(0, DASHBOARD_DIR)


class TestRegistryStoreFileBackend(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["STORAGE_BACKEND"] = "file"
        os.environ["STORAGE_PATH"] = os.path.join(self._tmpdir.name, "registry.json")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._tmpdir.cleanup()
        sys.modules.pop("storage", None)

    def _new_store(self):
        if "storage" in sys.modules:
            del sys.modules["storage"]
        from storage import RegistryStore

        return RegistryStore()

    def test_register_verify_round_trip(self):
        store = self._new_store()
        entry, credential = store.register_cluster("cluster-a", agent_version="1.0")
        self.assertEqual(entry["clusterName"], "cluster-a")
        self.assertTrue(store.verify_credential("cluster-a", credential))
        self.assertFalse(store.verify_credential("cluster-a", "wrong"))

    def test_file_persistence_survives_reload(self):
        store = self._new_store()
        _, credential = store.register_cluster("cluster-b")
        store.touch_cluster("cluster-b")

        reloaded = self._new_store()
        self.assertTrue(reloaded.cluster_exists("cluster-b"))
        self.assertTrue(reloaded.verify_credential("cluster-b", credential))

        with open(os.environ["STORAGE_PATH"], encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertIn("credentialHash", on_disk["clusters"]["cluster-b"])
        self.assertNotIn("clusterCredential", on_disk["clusters"]["cluster-b"])

    def test_delete_cluster(self):
        store = self._new_store()
        store.register_cluster("to-delete")
        self.assertTrue(store.delete_cluster("to-delete"))
        self.assertFalse(store.cluster_exists("to-delete"))


class TestRegistryStoreGCSBackend(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        os.environ["STORAGE_BACKEND"] = "gcs"
        os.environ["GCS_BUCKET"] = "test-bucket"
        os.environ["GCS_OBJECT"] = "registry.json"
        self._memory = {}

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        sys.modules.pop("storage", None)

    def _mock_gcs(self):
        bucket = MagicMock()
        blob = MagicMock()

        def upload_from_string(data, content_type=None):
            self._memory["data"] = data

        def download_as_text():
            return self._memory.get("data", '{"version": 1, "clusters": {}}')

        def exists():
            return "data" in self._memory

        blob.upload_from_string.side_effect = upload_from_string
        blob.download_as_text.side_effect = download_as_text
        blob.exists.side_effect = exists
        bucket.blob.return_value = blob

        client = MagicMock()
        client.bucket.return_value = bucket
        return client

    def test_gcs_round_trip(self):
        mock_client = self._mock_gcs()
        fake_storage = MagicMock()
        fake_storage.Client.return_value = mock_client

        with patch.dict(sys.modules, {"google.cloud.storage": fake_storage}):
            if "storage" in sys.modules:
                del sys.modules["storage"]
            from storage import RegistryStore

            store = RegistryStore()
            _, credential = store.register_cluster("gcs-cluster")
            self.assertTrue(store.verify_credential("gcs-cluster", credential))

            if "storage" in sys.modules:
                del sys.modules["storage"]
            from storage import RegistryStore as ReloadedStore

            reloaded = ReloadedStore()
            self.assertTrue(reloaded.verify_credential("gcs-cluster", credential))


if __name__ == "__main__":
    unittest.main()
