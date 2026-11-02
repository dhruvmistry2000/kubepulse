"""The dashboard deploy script asks for a URL and writes keys without leaking them."""

import os
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "scripts", "deploy-dashboard.sh")


def _parse_env(path):
    values = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value
    return values


class DeployDashboardScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def env(self, **extra):
        base = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": self.tmp.name,
            "LANG": "C",
        }
        base.update(extra)
        return base

    def run_script(self, args, env):
        return subprocess.run(
            [SCRIPT, *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=self.tmp.name,
        )

    def test_helm_release_commits_script_to_gh_pages(self):
        path = os.path.join(ROOT, ".github", "workflows", "helm-release.yaml")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        _, rest = text.split("release-chart:", 1)
        chart, script = rest.split("publish-dashboard-script:", 1)
        self.assertIn("release-chart:", text)
        self.assertIn("helm package", chart)
        self.assertNotIn("deploy-dashboard.sh", chart)
        self.assertIn("git push", chart)
        self.assertIn("scripts/deploy-dashboard.sh", script)
        self.assertIn("gh-pages/deploy-dashboard.sh", script)
        self.assertIn("ref: gh-pages", script)
        self.assertIn("git push", script)
        self.assertIn("gh-pages-publish", chart)
        self.assertIn("gh-pages-publish", script)

    def test_help_lists_url_and_targets(self):
        proc = self.run_script(["--help"], self.env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("DASHBOARD_URL", proc.stdout)
        self.assertIn("compose", proc.stdout)
        self.assertIn("kubernetes", proc.stdout)

    def test_yes_requires_dashboard_url(self):
        proc = self.run_script(
            ["--yes", "--dry-run", "--target", "docker"],
            self.env(),
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("DASHBOARD_URL", proc.stderr)

    def test_rejects_url_without_scheme_and_userinfo(self):
        missing_scheme = self.run_script(
            ["--yes", "--dry-run", "--target", "docker"],
            self.env(DASHBOARD_URL="dashboard.example.com", ADMIN_KEY="admin-key-1"),
        )
        self.assertNotEqual(missing_scheme.returncode, 0)
        self.assertIn("http://", missing_scheme.stderr)

        leaked = "super-secret-password"
        with_user = self.run_script(
            ["--yes", "--dry-run", "--target", "docker"],
            self.env(
                DASHBOARD_URL="https://user:%s@dashboard.example.com" % leaked,
                ADMIN_KEY="admin-key-1",
            ),
        )
        self.assertNotEqual(with_user.returncode, 0)
        self.assertIn("username or password", with_user.stderr)
        self.assertNotIn(leaked, with_user.stdout)
        self.assertNotIn(leaked, with_user.stderr)

    def test_docker_dry_run_keeps_provided_key_out_of_stdout(self):
        env_file = os.path.join(self.tmp.name, "dashboard.env")
        admin = "admin-key-keep-secret"
        proc = self.run_script(
            ["--yes", "--dry-run", "--target", "docker"],
            self.env(
                DASHBOARD_URL="http://172.19.0.1:8080",
                ADMIN_KEY=admin,
                VIEWER_KEY="viewer-key-1",
                KUBEPULSE_ENV_FILE=env_file,
            ),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Would run: docker run", proc.stdout)
        self.assertIn("http://172.19.0.1:8080", proc.stdout)
        self.assertNotIn(admin, proc.stdout)
        self.assertNotIn("Warning:", proc.stderr)
        self.assertEqual(stat.S_IMODE(os.stat(env_file).st_mode), 0o600)
        values = _parse_env(env_file)
        self.assertEqual(values["DASHBOARD_URL"], "http://172.19.0.1:8080")
        self.assertEqual(values["ADMIN_KEY"], admin)
        self.assertEqual(values["STORAGE_BACKEND"], "file")
        self.assertNotIn("DASHBOARD_PORT", values)

    def test_compose_dry_run_records_host_port(self):
        env_file = os.path.join(self.tmp.name, "compose.env")
        proc = self.run_script(
            ["--yes", "--dry-run", "--target", "compose"],
            self.env(
                DASHBOARD_URL="https://dashboard.example.com",
                ADMIN_KEY="admin-key-1",
                VIEWER_KEY="viewer-key-1",
                DASHBOARD_PORT="9090",
                KUBEPULSE_ENV_FILE=env_file,
            ),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Would run: docker compose", proc.stdout)
        self.assertIn("--build", proc.stdout)
        values = _parse_env(env_file)
        self.assertEqual(values["DASHBOARD_PORT"], "9090")
        self.assertEqual(values["DASHBOARD_URL"], "https://dashboard.example.com")
        self.assertNotIn("STORAGE_BACKEND", values)

    def test_generated_key_is_printed_once_and_saved(self):
        env_file = os.path.join(self.tmp.name, "generated.env")
        proc = self.run_script(
            ["--yes", "--dry-run", "--target", "docker"],
            self.env(
                DASHBOARD_URL="https://dashboard.example.com/",
                KUBEPULSE_ENV_FILE=env_file,
            ),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        values = _parse_env(env_file)
        admin = values["ADMIN_KEY"]
        self.assertEqual(len(admin), 64)
        self.assertEqual(proc.stdout.count(admin), 1)
        self.assertTrue(admin.isalnum())
        self.assertEqual(values["DASHBOARD_URL"], "https://dashboard.example.com")
        self.assertNotIn(admin, proc.stdout.split("Would run:", 1)[1])

    def test_refuses_to_overwrite_env_file(self):
        env_file = os.path.join(self.tmp.name, "existing.env")
        with open(env_file, "w", encoding="utf-8") as handle:
            handle.write("KEEP=1\n")
        proc = self.run_script(
            ["--yes", "--dry-run", "--target", "docker"],
            self.env(
                DASHBOARD_URL="https://dashboard.example.com",
                ADMIN_KEY="admin-key-1",
                VIEWER_KEY="viewer-key-1",
                KUBEPULSE_ENV_FILE=env_file,
            ),
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("overwrite", proc.stderr)
        with open(env_file, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "KEEP=1\n")

    def test_loopback_and_public_http_warn(self):
        loopback = self.run_script(
            ["--yes", "--dry-run", "--target", "docker"],
            self.env(
                DASHBOARD_URL="http://127.0.0.1:8080",
                ADMIN_KEY="admin-key-1",
                VIEWER_KEY="",
                KUBEPULSE_ENV_FILE=os.path.join(self.tmp.name, "loop.env"),
            ),
        )
        self.assertEqual(loopback.returncode, 0, loopback.stderr)
        self.assertIn("loopback", loopback.stderr)

        public_http = self.run_script(
            ["--yes", "--dry-run", "--target", "docker"],
            self.env(
                DASHBOARD_URL="http://dashboard.example.com",
                ADMIN_KEY="admin-key-1",
                VIEWER_KEY="viewer-key-1",
                KUBEPULSE_ENV_FILE=os.path.join(self.tmp.name, "http.env"),
            ),
        )
        self.assertEqual(public_http.returncode, 0, public_http.stderr)
        self.assertIn("HTTPS", public_http.stderr)

    def test_kubernetes_manifest_has_one_replica_and_no_keys(self):
        env_file = os.path.join(self.tmp.name, "k8s.env")
        manifest = os.path.join(self.tmp.name, "dashboard.yaml")
        admin = "admin-key-not-in-manifest"
        proc = self.run_script(
            ["--yes", "--dry-run", "--target", "k8s"],
            self.env(
                DASHBOARD_URL="https://dashboard.example.com",
                ADMIN_KEY=admin,
                VIEWER_KEY="viewer-key-1",
                KUBEPULSE_ENV_FILE=env_file,
                KUBEPULSE_MANIFEST_FILE=manifest,
                KUBEPULSE_NAMESPACE="pulse",
                KUBEPULSE_SERVICE_TYPE="NodePort",
                KUBEPULSE_STORAGE_CLASS="standard",
            ),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("kubectl apply -f %s" % manifest, proc.stdout)
        self.assertNotIn(admin, proc.stdout)
        with open(manifest, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn(admin, text)
        self.assertIn("replicas: 1", text)
        self.assertIn("secretRef:", text)
        self.assertIn("name: kubepulse-dashboard", text)
        self.assertIn("storageClassName: standard", text)
        self.assertIn("type: NodePort", text)
        self.assertIn("namespace: pulse", text)
        self.assertEqual(_parse_env(env_file)["ADMIN_KEY"], admin)

        import yaml

        docs = [doc for doc in yaml.safe_load_all(text) if doc]
        self.assertEqual([doc["kind"] for doc in docs], ["Namespace", "PersistentVolumeClaim", "Deployment", "Service"])
        deploy = next(doc for doc in docs if doc["kind"] == "Deployment")
        self.assertEqual(deploy["spec"]["replicas"], 1)
        container = deploy["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["envFrom"][0]["secretRef"]["name"], "kubepulse-dashboard")
        self.assertNotIn("env", container)
