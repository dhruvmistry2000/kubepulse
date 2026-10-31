"""Read APIs require a viewer or admin key. Secrets compare in constant time."""

import importlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = str(ROOT / "dashboard")
AGENT_DIR = str(ROOT / "agent")

ADMIN_KEY = "admin-key"
VIEWER_KEY = "viewer-key"
LEGACY_KEY = "legacy-key"


def _drop_dashboard_modules():
    for mod in list(sys.modules):
        if mod in ("app", "storage", "history", "pricing") or mod.startswith("app."):
            del sys.modules[mod]


class TestReadAuth(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        self._sys_path = list(sys.path)
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["STORAGE_BACKEND"] = "file"
        os.environ["STORAGE_PATH"] = os.path.join(self._tmpdir.name, "registry.json")
        os.environ["HISTORY_PATH"] = os.path.join(self._tmpdir.name, "history.json")
        os.environ["SECRET_KEY"] = LEGACY_KEY
        os.environ["ADMIN_KEY"] = ADMIN_KEY
        os.environ["VIEWER_KEY"] = VIEWER_KEY
        os.environ["DEBUG"] = "0"
        os.environ.pop("FLASK_DEBUG", None)
        os.environ["DASHBOARD_URL"] = "https://dash.example"
        _drop_dashboard_modules()
        sys.path.insert(0, DASHBOARD_DIR)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._tmpdir.cleanup()
        sys.path[:] = self._sys_path
        _drop_dashboard_modules()

    def _load_app(self):
        import app as dashboard_app

        return dashboard_app

    def _activate_cluster(self, client):
        issued = client.post(
            "/api/generate-token",
            json={"cluster_name": "prod"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        self.assertEqual(issued.status_code, 200, issued.get_json())
        token = issued.get_json()["token"]
        registered = client.post(
            "/api/register",
            json={"agent_version": "1.0"},
            headers={"X-Registration-Token": token},
        )
        self.assertEqual(registered.status_code, 200, registered.get_json())
        credential = registered.get_json()["cluster_credential"]
        heartbeat = client.post(
            "/api/heartbeat",
            json={"cluster_name": "prod", "data": {"nodes": [], "pods": [], "summary": {}}},
            headers={"X-Secret-Key": credential},
        )
        self.assertEqual(heartbeat.status_code, 200, heartbeat.get_json())
        return credential

    def test_auth_matrix(self):
        dashboard = self._load_app()
        client = dashboard.app.test_client()
        credential = self._activate_cluster(client)
        identities = {
            "none": {},
            "wrong": {"X-Api-Key": "wrong-key"},
            "viewer": {"X-Viewer-Key": VIEWER_KEY},
            "admin": {"X-Admin-Key": ADMIN_KEY},
            "legacy": {"X-Api-Key": LEGACY_KEY},
        }
        known = set()
        for rule in dashboard.app.url_map.iter_rules():
            if rule.endpoint == "static":
                continue
            path = rule.rule.replace("<cluster_name>", "prod")
            if rule.endpoint == "api_delete_cluster":
                path = "/api/cluster/not-registered"
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                known.add((method, rule.endpoint))
                for name, headers in identities.items():
                    kwargs = {"headers": headers}
                    if method in ("POST", "PUT", "PATCH"):
                        kwargs["json"] = {"cluster_name": "prod"}
                    if rule.endpoint == "api_heartbeat":
                        kwargs["headers"] = dict(headers)
                        if name == "legacy":
                            kwargs["headers"] = {"X-Secret-Key": LEGACY_KEY}
                        kwargs["json"] = {
                            "cluster_name": "prod",
                            "data": {"nodes": [], "summary": {}},
                        }
                    response = client.open(path, method=method, **kwargs)
                    self._expect(rule.endpoint, method, name, response.status_code, path)
        self.assertIn(("GET", "api_clusters"), known)
        self.assertIn(("POST", "api_heartbeat"), known)
        self.assertIn(("POST", "api_generate_token"), known)
        self.assertIn(("DELETE", "api_delete_cluster"), known)
        again = client.post(
            "/api/heartbeat",
            json={"cluster_name": "prod", "data": {"nodes": [], "summary": {}}},
            headers={"X-Secret-Key": credential},
        )
        self.assertEqual(again.status_code, 200)

    def _expect(self, endpoint, method, identity, status, path):
        if endpoint == "index":
            self.assertEqual(status, 200, path)
            return
        if endpoint == "api_register":
            self.assertEqual(status, 401, (identity, path))
            return
        if endpoint == "api_heartbeat":
            if identity == "legacy":
                self.assertEqual(status, 200, path)
            else:
                self.assertEqual(status, 401, (identity, path))
            return
        if endpoint in ("api_generate_token", "api_pending_tokens", "api_delete_cluster"):
            if identity == "admin":
                self.assertNotEqual(status, 401, path)
            else:
                self.assertEqual(status, 401, (identity, method, path))
            return
        if identity in ("viewer", "admin"):
            self.assertNotEqual(status, 401, (identity, method, path))
            self.assertLess(status, 500, (identity, method, path))
        else:
            self.assertEqual(status, 401, (identity, method, path))

    def test_page_sets_content_security_policy(self):
        dashboard = self._load_app()
        response = dashboard.app.test_client().get("/")
        policy = response.headers["Content-Security-Policy"]
        self.assertIn("script-src 'nonce-", policy)
        self.assertNotIn("unsafe-inline", policy.split(";")[1])
        body = response.get_data(as_text=True)
        self.assertIn('nonce="', body)
        self.assertNotIn("localStorage.setItem('adminKey'", body)
        self.assertNotIn("localStorage.setItem('accessKey'", body)

    def test_heartbeat_strips_secret_payload_fields(self):
        dashboard = self._load_app()
        client = dashboard.app.test_client()
        credential = self._activate_cluster(client)
        sent = client.post(
            "/api/heartbeat",
            json={
                "cluster_name": "prod",
                "data": {
                    "pods": [{"name": "api", "annotations": {"token": "raw-secret"}}],
                    "configmaps": [{
                        "name": "app",
                        "namespace": "default",
                        "keys": [{"name": "token", "size": 5}],
                        "data": {"token": "raw-secret"},
                    }],
                },
            },
            headers={"X-Secret-Key": credential},
        )
        self.assertEqual(sent.status_code, 200, sent.get_json())
        reader = {"X-Viewer-Key": VIEWER_KEY}
        pods = client.get("/api/cluster/prod/pods", headers=reader).get_json()
        self.assertEqual(pods["items"][0]["annotations"], {"token": "[redacted]"})
        configmaps = client.get("/api/cluster/prod/configmaps", headers=reader).get_json()
        self.assertNotIn("data", configmaps["items"][0])
        self.assertEqual(configmaps["items"][0]["keys"], [{"name": "token", "size": 5}])

    def test_legacy_key_does_not_grant_admin_when_distinct(self):
        dashboard = self._load_app()
        client = dashboard.app.test_client()
        response = client.post(
            "/api/generate-token",
            json={"cluster_name": "prod"},
            headers={"X-Admin-Key": LEGACY_KEY},
        )
        self.assertEqual(response.status_code, 401)


class TestStartup(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        self._sys_path = list(sys.path)
        _drop_dashboard_modules()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        sys.path[:] = self._sys_path
        _drop_dashboard_modules()

    def test_refuses_to_start_without_admin_key(self):
        os.environ.pop("ADMIN_KEY", None)
        os.environ.pop("FLASK_DEBUG", None)
        os.environ["DEBUG"] = "0"
        os.environ["SECRET_KEY"] = LEGACY_KEY
        sys.path.insert(0, DASHBOARD_DIR)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            with self.assertRaises(SystemExit):
                importlib.import_module("app")
        text = stdout.getvalue()
        self.assertNotIn(LEGACY_KEY, text)
        self.assertNotIn("Generated SECRET_KEY", text)

    def test_debug_mode_starts_without_printing_a_secret(self):
        os.environ.pop("ADMIN_KEY", None)
        os.environ["SECRET_KEY"] = LEGACY_KEY
        os.environ["DEBUG"] = "1"
        sys.path.insert(0, DASHBOARD_DIR)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            module = importlib.import_module("app")
        self.assertEqual(module.ADMIN_KEY, "")
        self.assertEqual(module.SECRET_KEY, LEGACY_KEY)
        text = stdout.getvalue()
        self.assertNotIn(LEGACY_KEY, text)
        self.assertNotIn("Generated SECRET_KEY", text)


class TestSecretCompare(unittest.TestCase):
    def test_compare_digest_rejects_prefix(self):
        sys.path.insert(0, DASHBOARD_DIR)
        try:
            from storage import secrets_equal
        finally:
            sys.path[:] = [p for p in sys.path if p != DASHBOARD_DIR]
        self.assertTrue(secrets_equal("admin-key", "admin-key"))
        self.assertFalse(secrets_equal("admin-key", "admin-key-extra"))
        self.assertFalse(secrets_equal("admin-key", "admin"))
        self.assertFalse(secrets_equal("", "admin-key"))
        self.assertFalse(secrets_equal("admin-key", ""))


class TestPayloadRedaction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("DASHBOARD_URL", None)
        spec = importlib.util.spec_from_file_location(
            "kubepulse_agent_phase2",
            str(ROOT / "agent" / "app.py"),
        )
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, AGENT_DIR)
        sys.path.insert(0, str(ROOT))
        spec.loader.exec_module(module)
        cls.agent = module

    def test_configmap_sends_names_and_sizes_only(self):
        keys = self.agent.configmap_key_stats(
            {"token": "hello", "note": "x"},
            {"blob": "YQ=="},
        )
        names = {item["name"]: item["size"] for item in keys}
        self.assertEqual(names, {"blob": 4, "note": 1, "token": 5})
        self.assertTrue(all(set(item) == {"name", "size"} for item in keys))

    def test_annotations_are_redacted(self):
        redacted = self.agent.redact_annotations(
            {"kubectl.kubernetes.io/last-applied-configuration": "{\"secret\":1}"}
        )
        self.assertEqual(
            redacted,
            {"kubectl.kubernetes.io/last-applied-configuration": "[redacted]"},
        )
