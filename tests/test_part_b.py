"""Unit tests for Part B: provider parsing, pricing, history."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "agent")
DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), "..", "dashboard")
sys.path.insert(0, AGENT_DIR)
sys.path.insert(0, DASHBOARD_DIR)

from provider import parse_provider_id, parse_node_cloud_info
from pricing import PriceLookup, enrich_nodes_with_cost, compute_cost_summary


class TestProviderParsing(unittest.TestCase):
    def test_gcp_provider_id(self):
        self.assertEqual(parse_provider_id("gce://project/zone/instance"), "gcp")

    def test_aws_provider_id(self):
        self.assertEqual(parse_provider_id("aws:///us-east-1/i-abc123"), "aws")

    def test_azure_provider_id(self):
        self.assertEqual(parse_provider_id("azure:///subscriptions/sub/resource"), "azure")

    def test_onprem_empty(self):
        self.assertEqual(parse_provider_id(""), "onprem")
        self.assertEqual(parse_provider_id(None), "onprem")

    def test_onprem_malformed(self):
        self.assertEqual(parse_provider_id("digitalocean://droplet/1"), "onprem")

    def test_instance_type_and_region_labels(self):
        info = parse_node_cloud_info(
            "gce://p/z/n",
            {
                "node.kubernetes.io/instance-type": "e2-medium",
                "topology.kubernetes.io/region": "us-central1",
                "topology.kubernetes.io/zone": "us-central1-a",
            },
        )
        self.assertEqual(info["provider"], "gcp")
        self.assertEqual(info["instanceType"], "e2-medium")
        self.assertEqual(info["region"], "us-central1")
        self.assertEqual(info["zone"], "us-central1-a")


class TestPriceLookup(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._price_path = os.path.join(self._tmpdir.name, "prices.json")
        with open(self._price_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "prices": {
                        "gcp/us-central1/e2-medium": 0.0335,
                        "aws/*/m5.large": 0.096,
                    },
                    "fallback": {
                        "onprem": {"vcpuPerHour": 0.02, "gbPerHour": 0.003},
                    },
                },
                fh,
            )
        os.environ["PRICE_MAP_PATH"] = self._price_path
        os.environ["PRICING_ENABLED"] = "true"
        os.environ["PRICING_LIVE_ENABLED"] = "false"
        os.environ["PRICING_AWS_LIVE_ENABLED"] = "false"
        os.environ["PRICING_AZURE_LIVE_ENABLED"] = "false"
        os.environ["PRICING_PREFER_LIVE"] = "false"
        os.environ["PRICING_MODEL"] = "instance"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._tmpdir.cleanup()

    def test_static_exact_match(self):
        lookup = PriceLookup()
        result = lookup.price_node("gcp", "us-central1", "e2-medium", "2", "4Gi")
        self.assertTrue(result["priced"])
        self.assertEqual(result["pricingSource"], "static")
        self.assertAlmostEqual(result["hourlyUsd"], 0.0335)

    def test_static_wildcard(self):
        lookup = PriceLookup()
        result = lookup.price_node("aws", "eu-west-1", "m5.large", "2", "8Gi")
        self.assertTrue(result["priced"])
        self.assertEqual(result["pricingSource"], "static")

    def test_fallback_estimated(self):
        lookup = PriceLookup()
        result = lookup.price_node("onprem", None, None, "4", "16Gi")
        self.assertTrue(result["priced"])
        self.assertEqual(result["pricingSource"], "estimated")

    def test_unpriced_not_zero(self):
        lookup = PriceLookup()
        result = lookup.price_node("onprem", None, None, None, None)
        self.assertFalse(result["priced"])
        self.assertIsNone(result["hourlyUsd"])
        self.assertEqual(result["pricingSource"], "unpriced")

    def test_aws_public_live_pricing(self):
        os.environ["PRICING_LIVE_ENABLED"] = "true"
        os.environ["PRICING_AWS_LIVE_ENABLED"] = "true"
        lookup = PriceLookup()
        lookup._aws_offers["us-east-1"] = {
            "fetched_at": __import__("time").time(),
            "data": {
                "products": {
                    "SKU1": {
                        "attributes": {
                            "instanceType": "m5.large",
                            "operatingSystem": "Linux",
                            "tenancy": "Shared",
                            "preInstalledSw": "NA",
                            "capacitystatus": "Used",
                        }
                    }
                },
                "terms": {
                    "OnDemand": {
                        "T1": {
                            "sku": "SKU1",
                            "priceDimensions": {
                                "D1": {"pricePerUnit": {"USD": "0.096"}}
                            },
                        }
                    }
                },
            },
        }
        result = lookup.lookup_aws_live("us-east-1", "m5.large")
        self.assertEqual(result, (0.096, "live"))

    def test_usage_based_pricing(self):
        os.environ["PRICING_MODEL"] = "usage"
        lookup = PriceLookup()
        result = lookup.price_node(
            "gcp",
            "us-central1",
            "e2-medium",
            "4",
            "16Gi",
            cpu_usage="500m",
            memory_usage="2Gi",
            cpu_allocatable="4",
            memory_allocatable="16Gi",
        )
        self.assertTrue(result["priced"])
        self.assertEqual(result["pricingSource"], "usage")
        self.assertGreater(result["hourlyUsd"], 0)

    def test_prefer_live_over_static(self):
        os.environ["PRICING_LIVE_ENABLED"] = "true"
        os.environ["PRICING_PREFER_LIVE"] = "true"
        os.environ["PRICING_AWS_LIVE_ENABLED"] = "true"
        lookup = PriceLookup()
        lookup._aws_offers["us-east-1"] = {
            "fetched_at": __import__("time").time(),
            "data": {
                "products": {
                    "SKU1": {
                        "attributes": {
                            "instanceType": "m5.large",
                            "operatingSystem": "Linux",
                            "tenancy": "Shared",
                            "preInstalledSw": "NA",
                            "capacitystatus": "Used",
                        }
                    }
                },
                "terms": {
                    "OnDemand": {
                        "T1": {
                            "sku": "SKU1",
                            "priceDimensions": {
                                "D1": {"pricePerUnit": {"USD": "0.150"}}
                            },
                        }
                    }
                },
            },
        }
        result = lookup.price_node("aws", "us-east-1", "m5.large", "2", "8Gi")
        self.assertTrue(result["priced"])
        self.assertEqual(result["pricingSource"], "live")
        self.assertAlmostEqual(result["hourlyUsd"], 0.150)


class TestDashboardCostEnrichment(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._price_path = os.path.join(self._tmpdir.name, "prices.json")
        with open(self._price_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "fallback": {
                        "gcp": {"vcpuPerHour": 0.03, "gbPerHour": 0.004},
                    }
                },
                fh,
            )
        os.environ["PRICE_MAP_PATH"] = self._price_path
        os.environ["PRICING_ENABLED"] = "true"
        os.environ["PRICING_MODEL"] = "usage"
        os.environ["PRICING_LIVE_ENABLED"] = "false"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._tmpdir.cleanup()

    def test_enrich_and_summary(self):
        nodes = [
            {
                "name": "n1",
                "provider": "gcp",
                "region": "us-central1",
                "instanceType": "e2-medium",
                "cpuCapacity": "2",
                "memoryCapacity": "4Gi",
                "cpuUsage": "1000m",
                "memoryUsage": "2Gi",
                "allocatableCPU": "2",
                "allocatableMemory": "4Gi",
            }
        ]
        enrich_nodes_with_cost(nodes)
        self.assertTrue(nodes[0]["priced"])
        self.assertGreater(nodes[0]["hourlyUsd"], 0)
        summary = compute_cost_summary(nodes)
        self.assertEqual(summary["pricedNodes"], 1)
        self.assertGreater(summary["hourlyUsd"], 0)


class TestHistoryStore(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["STORAGE_BACKEND"] = "file"
        os.environ["HISTORY_PATH"] = os.path.join(self._tmpdir.name, "history.json")
        os.environ["HISTORY_RETENTION_DAYS"] = "30"
        os.environ["SCALING_INTERVAL_SECONDS"] = "0"
        if "history" in sys.modules:
            del sys.modules["history"]

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._tmpdir.cleanup()
        sys.modules.pop("history", None)

    def _store(self):
        from history import HistoryStore

        return HistoryStore()

    def test_scaling_and_retired(self):
        store = self._store()
        nodes = [
            {
                "name": "node-a",
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
        ]
        store.record_heartbeat("prod", nodes)
        scaling = store.get_scaling("prod")
        self.assertEqual(scaling["prod"][-1]["nodeCount"], 1)

        store.record_heartbeat("prod", [])
        retired = store.get_retired_nodes("prod")
        self.assertEqual(len(retired), 1)
        self.assertEqual(retired[0]["nodeName"], "node-a")
        self.assertTrue(retired[0]["retired"])


if __name__ == "__main__":
    unittest.main()
