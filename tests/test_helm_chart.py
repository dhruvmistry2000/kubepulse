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
