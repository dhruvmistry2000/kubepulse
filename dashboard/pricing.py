"""Node pricing — credential-free only.

Sources (no cloud account credentials):
  1. Static price map (ConfigMap)
  2. Public list-price APIs: Azure Retail Prices, AWS public offer index (HTTPS)
  3. Usage-based estimate from metrics-server CPU/RAM × $/vcpu-hr and $/gb-hr
  4. Capacity-based fallback when metrics are absent
"""

import json
import logging
import os
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests

def _ensure_common_on_path():
    here = Path(__file__).resolve().parent
    for candidate in (here, here.parent):
        if (candidate / "common" / "quantity.py").is_file():
            path = str(candidate)
            if path not in sys.path:
                sys.path.insert(0, path)
            return


_ensure_common_on_path()
from common.quantity import parse_memory_bytes  # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_PRICE_MAP = os.path.join(os.path.dirname(__file__), "prices.json")
PRICE_MAP_PATH = (os.environ.get("PRICE_MAP_PATH") or _DEFAULT_PRICE_MAP).strip()
PRICING_REFRESH_INTERVAL = int(os.environ.get("PRICING_REFRESH_INTERVAL") or "43200")
AWS_PUBLIC_INDEX_URL = (
    "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/index.json"
)
AWS_PUBLIC_BASE = "https://pricing.us-east-1.amazonaws.com"
HOURS_PER_MONTH = 730


def _env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes")


def _live_master_enabled():
    raw = os.environ.get("PRICING_LIVE_ENABLED")
    if raw is None or raw == "":
        return True
    return _env_bool("PRICING_LIVE_ENABLED", False)


def _azure_live_enabled():
    if not _live_master_enabled():
        return False
    raw = os.environ.get("PRICING_AZURE_LIVE_ENABLED")
    if raw is None or raw == "":
        return True
    return _env_bool("PRICING_AZURE_LIVE_ENABLED", False)


def _aws_live_enabled():
    if not _live_master_enabled():
        return False
    raw = os.environ.get("PRICING_AWS_LIVE_ENABLED")
    if raw is None or raw == "":
        return True
    return _env_bool("PRICING_AWS_LIVE_ENABLED", False)


def _prefer_live():
    raw = os.environ.get("PRICING_PREFER_LIVE")
    if raw is None or raw == "":
        return False
    return _env_bool("PRICING_PREFER_LIVE", False)


def _pricing_model():
    """instance = full VM list price; usage = meter live CPU/RAM (default)."""
    return (os.environ.get("PRICING_MODEL") or "usage").strip().lower()


def _pricing_enabled():
    raw = os.environ.get("PRICING_ENABLED")
    if raw is None or raw == "":
        return True
    return _env_bool("PRICING_ENABLED", False)


def _parse_cpu_cores(value):
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("m"):
        try:
            return float(s[:-1]) / 1000.0
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_cpu_usage_cores(value):
    """Parse metrics-server CPU usage (nanocores or millicores) to cores."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("n"):
        try:
            return float(s[:-1]) / 1e9
        except ValueError:
            return None
    if s.endswith("m"):
        try:
            return float(s[:-1]) / 1000.0
        except ValueError:
            return None
    return _parse_cpu_cores(value)


def _parse_memory_gib(value):
    nbytes = parse_memory_bytes(value)
    if nbytes is None:
        return None
    return nbytes / (1024 ** 3)


class PriceLookup:
    """Credential-free pricing: static map, public APIs, usage/capacity estimates."""

    def __init__(self):
        self._lock = threading.Lock()
        self._static_map = {}
        self._live_cache = {}
        self._fallback_rates = {}
        self._aws_index = None
        self._aws_index_fetched_at = 0
        self._aws_offers = {}
        self._refresh_queue = queue.Queue()
        self._scheduled = set()
        self._load_static_map()
        self._load_disk_cache()
        worker = threading.Thread(target=self._refresh_loop, daemon=True)
        worker.start()

    def _load_static_map(self):
        price_map_path = (os.environ.get("PRICE_MAP_PATH") or PRICE_MAP_PATH).strip()
        try:
            with open(price_map_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            logger.warning("Price map not found at %s; using fallbacks only", price_map_path)
            data = {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load price map %s: %s", price_map_path, exc)
            data = {}

        prices = data.get("prices") or data
        if isinstance(prices, dict):
            self._static_map = prices
        self._fallback_rates = data.get("fallback") or {
            "gcp": {"vcpuPerHour": 0.031611, "gbPerHour": 0.004237},
            "aws": {"vcpuPerHour": 0.0464, "gbPerHour": 0.005},
            "azure": {"vcpuPerHour": 0.0416, "gbPerHour": 0.005},
            "onprem": {"vcpuPerHour": 0.02, "gbPerHour": 0.003},
        }

    @staticmethod
    def _key(provider, region, instance_type):
        return f"{provider}/{region}/{instance_type}"

    @staticmethod
    def _wildcard_key(provider, instance_type):
        return f"{provider}/*/{instance_type}"

    def _cache_get(self, cache_key):
        with self._lock:
            cached = self._live_cache.get(cache_key)
            if cached and (time.time() - cached["fetched_at"]) < PRICING_REFRESH_INTERVAL:
                return cached["price"], cached.get("source", "live")
        return None

    def _cache_set(self, cache_key, price, source="live"):
        with self._lock:
            self._live_cache[cache_key] = {
                "price": price,
                "source": source,
                "fetched_at": time.time(),
            }

    def lookup_static(self, provider, region, instance_type):
        if not instance_type:
            return None
        region = region or "*"
        exact = self._key(provider, region, instance_type)
        if exact in self._static_map:
            return float(self._static_map[exact]), "static"
        wildcard = self._wildcard_key(provider, instance_type)
        if wildcard in self._static_map:
            return float(self._static_map[wildcard]), "static"
        return None

    def lookup_azure_live(self, region, instance_type):
        """Azure Retail Prices API — public, no credentials."""
        if not _azure_live_enabled() or not region or not instance_type:
            return None
        cache_key = ("azure", region, instance_type)
        hit = self._cache_get(cache_key)
        if hit:
            return hit

        self._schedule_refresh("azure", region, instance_type)
        return None

    def _fetch_azure_now(self, region, instance_type):
        cache_key = ("azure", region, instance_type)
        try:
            filt = (
                f"armRegionName eq '{region}' and armSkuName eq '{instance_type}' "
                "and priceType eq 'Consumption'"
            )
            r = requests.get(
                "https://prices.azure.com/api/retail/prices",
                params={"$filter": filt},
                timeout=15,
            )
            r.raise_for_status()
            for item in r.json().get("Items") or []:
                unit = (item.get("unitOfMeasure") or "").lower()
                if unit in ("1 hour", "hour", "hours"):
                    hourly = float(item.get("retailPrice") or item.get("unitPrice") or 0)
                    if hourly > 0:
                        self._cache_set(cache_key, hourly, "live")
                        self._write_disk_cache("azure-%s-%s.json" % (region, instance_type), {"hourly": hourly})
                        return
        except Exception as exc:
            logger.debug("Azure public pricing failed for %s/%s: %s", region, instance_type, exc)

    def _load_aws_public_offer(self, region):
        now = time.time()
        with self._lock:
            cached = self._aws_offers.get(region)
            if cached and (now - cached["fetched_at"]) < PRICING_REFRESH_INTERVAL:
                return cached["data"]

        self._schedule_refresh("aws", region, None)
        return None

    def _fetch_aws_offer_now(self, region):
        now = time.time()
        try:
            if self._aws_index is None or (now - self._aws_index_fetched_at) >= PRICING_REFRESH_INTERVAL:
                r = requests.get(AWS_PUBLIC_INDEX_URL, timeout=30)
                r.raise_for_status()
                self._aws_index = r.json()
                self._aws_index_fetched_at = now
                self._write_disk_cache("aws-index.json", self._aws_index)

            region_info = (self._aws_index.get("regions") or {}).get(region)
            if not region_info:
                return

            offer_url = AWS_PUBLIC_BASE + region_info["currentVersionUrl"]
            r = requests.get(offer_url, timeout=60)
            r.raise_for_status()
            offer = r.json()
            with self._lock:
                self._aws_offers[region] = {"data": offer, "fetched_at": now}
            self._write_disk_cache("aws-%s.json" % region, offer)
        except Exception as exc:
            logger.debug("AWS public offer load failed for %s: %s", region, exc)

    def _price_cache_dir(self):
        directory = os.environ.get("PRICE_CACHE_DIR") or os.path.join(
            tempfile.gettempdir(), "kubepulse-prices"
        )
        os.makedirs(directory, exist_ok=True)
        return directory

    def _write_disk_cache(self, name, payload):
        path = os.path.join(self._price_cache_dir(), name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)

    def _load_disk_cache(self):
        directory = os.environ.get("PRICE_CACHE_DIR") or os.path.join(
            tempfile.gettempdir(), "kubepulse-prices"
        )
        index_path = os.path.join(directory, "aws-index.json")
        if not os.path.isfile(index_path):
            return
        age = time.time() - os.path.getmtime(index_path)
        if age >= PRICING_REFRESH_INTERVAL:
            return
        try:
            with open(index_path, "r", encoding="utf-8") as fh:
                self._aws_index = json.load(fh)
            self._aws_index_fetched_at = time.time() - age
        except (OSError, json.JSONDecodeError):
            return

    def _schedule_refresh(self, kind, region, instance_type):
        key = (kind, region, instance_type)
        with self._lock:
            if key in self._scheduled:
                return
            self._scheduled.add(key)
        self._refresh_queue.put(key)

    def _refresh_loop(self):
        while True:
            kind, region, instance_type = self._refresh_queue.get()
            try:
                if kind == "aws":
                    self._fetch_aws_offer_now(region)
                elif kind == "azure":
                    self._fetch_azure_now(region, instance_type)
            except Exception:
                logger.debug("price refresh failed for %s %s", kind, region, exc_info=True)
            finally:
                with self._lock:
                    self._scheduled.discard((kind, region, instance_type))

    def lookup_aws_live(self, region, instance_type):
        """AWS public price list JSON — no credentials."""
        if not _aws_live_enabled() or not region or not instance_type:
            return None
        cache_key = ("aws", region, instance_type)
        hit = self._cache_get(cache_key)
        if hit:
            return hit

        offer = self._load_aws_public_offer(region)
        if not offer:
            return None

        products = offer.get("products") or {}
        on_demand = (offer.get("terms") or {}).get("OnDemand") or {}

        for term in on_demand.values():
            sku = term.get("sku")
            product = products.get(sku) or {}
            attrs = product.get("attributes") or {}
            if attrs.get("instanceType") != instance_type:
                continue
            if attrs.get("operatingSystem", "Linux") != "Linux":
                continue
            if attrs.get("tenancy", "Shared") != "Shared":
                continue
            if attrs.get("preInstalledSw", "NA") != "NA":
                continue
            if attrs.get("capacitystatus", "Used") != "Used":
                continue
            for dim in (term.get("priceDimensions") or {}).values():
                usd = (dim.get("pricePerUnit") or {}).get("USD")
                if usd is not None:
                    hourly = float(usd)
                    if hourly > 0:
                        self._cache_set(cache_key, hourly, "live")
                        return hourly, "live"
        return None

    def lookup_live(self, provider, region, instance_type):
        if provider == "azure":
            return self.lookup_azure_live(region, instance_type)
        if provider == "aws":
            return self.lookup_aws_live(region, instance_type)
        return None

    def lookup_capacity_estimate(self, provider, cpu_capacity, memory_capacity):
        rates = self._fallback_rates.get(provider) or self._fallback_rates.get("onprem") or {}
        vcpu_rate = float(rates.get("vcpuPerHour") or 0)
        gb_rate = float(rates.get("gbPerHour") or 0)
        cores = _parse_cpu_cores(cpu_capacity)
        gib = _parse_memory_gib(memory_capacity)
        if cores is None and gib is None:
            return None
        cost = (cores or 0) * vcpu_rate + (gib or 0) * gb_rate
        if cost <= 0:
            return None
        return cost, "estimated"

    def lookup_usage_cost(
        self,
        provider,
        cpu_usage,
        memory_usage,
        cpu_allocatable=None,
        memory_allocatable=None,
        cpu_capacity=None,
        memory_capacity=None,
    ):
        """
        Meter cost from live cluster usage (metrics-server).
        Uses actual CPU/RAM when present; otherwise allocatable; then capacity.
        """
        rates = self._fallback_rates.get(provider) or self._fallback_rates.get("onprem") or {}
        vcpu_rate = float(rates.get("vcpuPerHour") or 0)
        gb_rate = float(rates.get("gbPerHour") or 0)

        cores = _parse_cpu_usage_cores(cpu_usage)
        gib = _parse_memory_gib(memory_usage)
        source = "usage"

        if cores is None:
            cores = _parse_cpu_cores(cpu_allocatable) or _parse_cpu_cores(cpu_capacity)
            source = "usage-allocatable" if cpu_allocatable else "usage-capacity"
        if gib is None:
            gib = _parse_memory_gib(memory_allocatable) or _parse_memory_gib(memory_capacity)
            if source == "usage":
                source = "usage-allocatable" if memory_allocatable else "usage-capacity"

        if cores is None and gib is None:
            return None

        cost = (cores or 0) * vcpu_rate + (gib or 0) * gb_rate
        if cost <= 0:
            return None
        return cost, source

    def price_node(
        self,
        provider,
        region,
        instance_type,
        cpu_capacity,
        memory_capacity,
        cpu_usage=None,
        memory_usage=None,
        cpu_allocatable=None,
        memory_allocatable=None,
    ):
        result = {
            "hourlyUsd": None,
            "monthlyUsd": None,
            "pricingSource": "unpriced",
            "priced": False,
        }

        if not _pricing_enabled():
            return result

        hourly = None
        source = None

        if _pricing_model() == "usage":
            usage = self.lookup_usage_cost(
                provider,
                cpu_usage,
                memory_usage,
                cpu_allocatable=cpu_allocatable,
                memory_allocatable=memory_allocatable,
                cpu_capacity=cpu_capacity,
                memory_capacity=memory_capacity,
            )
            if usage:
                hourly, source = usage
        else:
            static = self.lookup_static(provider, region, instance_type)
            live = self.lookup_live(provider, region, instance_type)

            if _prefer_live() and live:
                hourly, source = live
            elif static:
                hourly, source = static
            elif live:
                hourly, source = live

            if hourly is None:
                fb = self.lookup_capacity_estimate(provider, cpu_capacity, memory_capacity)
                if fb:
                    hourly, source = fb

        if hourly is not None and hourly > 0:
            result["hourlyUsd"] = round(hourly, 6)
            result["monthlyUsd"] = round(hourly * HOURS_PER_MONTH, 2)
            result["pricingSource"] = source
            result["priced"] = True
        return result


def enrich_nodes_with_cost(nodes, price_lookup=None):
    """Attach cost fields to node dicts from agent resource data. Mutates in place."""
    lookup = price_lookup or PriceLookup()
    for n in nodes or []:
        pricing = lookup.price_node(
            n.get("provider") or "onprem",
            n.get("region"),
            n.get("instanceType"),
            n.get("cpuCapacity"),
            n.get("memoryCapacity"),
            cpu_usage=n.get("cpuUsage"),
            memory_usage=n.get("memoryUsage"),
            cpu_allocatable=n.get("allocatableCPU"),
            memory_allocatable=n.get("allocatableMemory"),
        )
        n["hourlyUsd"] = pricing["hourlyUsd"]
        n["monthlyUsd"] = pricing["monthlyUsd"]
        n["pricingSource"] = pricing["pricingSource"]
        n["priced"] = pricing["priced"]
    return nodes


def compute_cost_summary(nodes):
    """Roll up node costs by provider."""
    summary = {
        "hourlyUsd": 0.0,
        "monthlyUsd": 0.0,
        "pricedNodes": 0,
        "unpricedNodes": 0,
        "byProvider": {},
    }
    for n in nodes or []:
        provider = n.get("provider") or "onprem"
        hourly = n.get("hourlyUsd")
        monthly = n.get("monthlyUsd")
        if n.get("priced") and hourly is not None:
            summary["hourlyUsd"] += hourly
            summary["monthlyUsd"] += monthly or (hourly * HOURS_PER_MONTH)
            summary["pricedNodes"] += 1
            prov = summary["byProvider"].setdefault(
                provider, {"hourlyUsd": 0.0, "monthlyUsd": 0.0, "nodeCount": 0}
            )
            prov["hourlyUsd"] += hourly
            prov["monthlyUsd"] += monthly or (hourly * HOURS_PER_MONTH)
            prov["nodeCount"] += 1
        else:
            summary["unpricedNodes"] += 1
    summary["hourlyUsd"] = round(summary["hourlyUsd"], 4)
    summary["monthlyUsd"] = round(summary["monthlyUsd"], 2)
    for prov in summary["byProvider"].values():
        prov["hourlyUsd"] = round(prov["hourlyUsd"], 4)
        prov["monthlyUsd"] = round(prov["monthlyUsd"], 2)
    return summary
