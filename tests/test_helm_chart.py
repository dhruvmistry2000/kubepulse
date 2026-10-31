"""Render the agent chart and check the defaults the installer depends on."""

import os
import shutil
import subprocess
import unittest

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CHART = os.path.join(ROOT, "helm")


def _helm_available():
    return shutil.which("helm") is not None


def _container_env(docs):
    deploy = next(doc for doc in docs if doc["kind"] == "Deployment")
    env = deploy["spec"]["template"]["spec"]["containers"][0]["env"]
    return {item["name"]: item for item in env}


def helm_template(*sets):
    cmd = ["helm", "template", "test", CHART]
    for value in sets:
        cmd.extend(["--set", value])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(
            "helm template failed:\n%s\n%s" % (proc.stdout, proc.stderr)
        )
    docs = [doc for doc in yaml.safe_load_all(proc.stdout) if doc]
    return docs


@unittest.skipUnless(_helm_available(), "helm is not installed")
class TestHelmChartDefaults(unittest.TestCase):
    def test_default_values_render(self):
        docs = helm_template()
        kinds = [doc["kind"] for doc in docs]
        self.assertIn("Deployment", kinds)
        self.assertNotIn("PodDisruptionBudget", kinds)
        self.assertNotIn("NetworkPolicy", kinds)
        deploy = next(doc for doc in docs if doc["kind"] == "Deployment")
        self.assertEqual(deploy["spec"]["replicas"], 1)
        pod = deploy["spec"]["template"]["spec"]
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        container = pod["containers"][0]
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
        self.assertTrue(container["image"].endswith(":latest"))

    def test_network_policy_renders_only_when_enabled(self):
        docs = helm_template("networkPolicy.enabled=true")
        policies = [doc for doc in docs if doc["kind"] == "NetworkPolicy"]
        self.assertEqual(len(policies), 1)

    def test_pdb_renders_only_when_enabled(self):
        docs = helm_template("podDisruptionBudget.enabled=true")
        pdbs = [doc for doc in docs if doc["kind"] == "PodDisruptionBudget"]
        self.assertEqual(len(pdbs), 1)
        self.assertEqual(pdbs[0]["spec"]["minAvailable"], 1)

    def test_helm_lint_defaults(self):
        proc = subprocess.run(
            ["helm", "lint", CHART], capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_state_secret_role_can_update_precreated_secret(self):
        docs = helm_template(
            "registrationToken=tok",
            "dashboardUrl=https://dash.example",
            "clusterName=prod",
        )
        secrets = [doc for doc in docs if doc["kind"] == "Secret"]
        state = [
            doc for doc in secrets
            if doc["metadata"]["name"] == "kubepulse-agent-state"
        ]
        self.assertEqual(len(state), 1)
        annotations = state[0]["metadata"].get("annotations") or {}
        self.assertEqual(annotations.get("helm.sh/resource-policy"), "keep")
        self.assertNotIn("stringData", state[0])
        self.assertNotIn("data", state[0])

        roles = [doc for doc in docs if doc["kind"] == "Role"]
        self.assertEqual(len(roles), 1)
        rules = roles[0]["rules"]
        self.assertEqual(len(rules), 1)
        rule = rules[0]
        self.assertEqual(rule["resources"], ["secrets"])
        self.assertEqual(rule["resourceNames"], ["kubepulse-agent-state"])
        self.assertEqual(sorted(rule["verbs"]), ["get", "patch", "update"])

    def test_connection_token_is_read_from_a_secret(self):
        docs = helm_template(
            "registrationToken=tok",
            "dashboardUrl=https://dash.example",
            "clusterName=prod",
        )
        bootstrap = [
            doc for doc in docs
            if doc["kind"] == "Secret" and doc["metadata"]["name"] == "k8s-dashboard-agent"
        ]
        self.assertEqual(len(bootstrap), 1)
        self.assertEqual(bootstrap[0]["stringData"]["REGISTRATION_TOKEN"], "tok")
        self.assertEqual(bootstrap[0]["stringData"]["CLUSTER_NAME"], "prod")
        env = _container_env(docs)
        token_env = env["REGISTRATION_TOKEN"]
        self.assertNotIn("value", token_env)
        ref = token_env["valueFrom"]["secretKeyRef"]
        self.assertEqual(ref["name"], "k8s-dashboard-agent")
        self.assertEqual(ref["key"], "REGISTRATION_TOKEN")

    def test_existing_secret_is_not_duplicated_by_the_chart(self):
        docs = helm_template("existingSecret=kubepulse-agent")
        names = [doc["metadata"]["name"] for doc in docs if doc["kind"] == "Secret"]
        self.assertNotIn("k8s-dashboard-agent", names)
        env = _container_env(docs)
        for key in ("REGISTRATION_TOKEN", "DASHBOARD_URL", "CLUSTER_NAME"):
            ref = env[key]["valueFrom"]["secretKeyRef"]
            self.assertEqual(ref["name"], "kubepulse-agent")
            self.assertEqual(ref["key"], key)
            self.assertNotIn("value", env[key])

    def test_file_persistence_skips_state_secret_role(self):
        docs = helm_template("persistence.mode=file")
        names = [
            doc["metadata"]["name"]
            for doc in docs
            if doc["kind"] in ("Secret", "Role", "RoleBinding")
        ]
        self.assertNotIn("kubepulse-agent-state", names)
        self.assertFalse(any(name.endswith("-state") for name in names))
