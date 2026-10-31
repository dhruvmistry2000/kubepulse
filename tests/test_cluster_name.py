"""Unit tests for the shared cluster-name normalizer."""

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from common.cluster_name import normalize_cluster_name


class TestNormalizeClusterName(unittest.TestCase):
    def test_lowercase_and_trim(self):
        self.assertEqual(normalize_cluster_name("  Production  "), "production")

    def test_dots_and_hyphens(self):
        self.assertEqual(normalize_cluster_name("Prod.EU-1"), "prod.eu-1")

    def test_rejects_underscore_and_empty(self):
        self.assertIsNone(normalize_cluster_name("my_cluster"))
        self.assertIsNone(normalize_cluster_name(""))
        self.assertIsNone(normalize_cluster_name("..."))
        self.assertIsNone(normalize_cluster_name("-prod"))

    def test_label_length(self):
        self.assertEqual(normalize_cluster_name("a" * 63), "a" * 63)
        self.assertIsNone(normalize_cluster_name("a" * 64))
        self.assertIsNone(normalize_cluster_name("a" * 254))


if __name__ == "__main__":
    unittest.main()
