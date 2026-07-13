"""Cloud provider detection from Kubernetes Node objects (no cloud credentials)."""

import re

_PROVIDER_PREFIXES = {
    "gce://": "gcp",
    "aws://": "aws",
    "azure://": "azure",
}


def parse_provider_id(provider_id):
    """
    Derive cloud provider from spec.providerID prefix.

    Returns one of: gcp, aws, azure, onprem
    """
    if not provider_id or not isinstance(provider_id, str):
        return "onprem"
    pid = provider_id.strip().lower()
    for prefix, provider in _PROVIDER_PREFIXES.items():
        if pid.startswith(prefix):
            return provider
    if pid:
        return "onprem"
    return "onprem"


def parse_instance_type(labels):
    """Read instance type from standard node labels."""
    if not labels:
        return None
    return (
        labels.get("node.kubernetes.io/instance-type")
        or labels.get("beta.kubernetes.io/instance-type")
        or None
    )


def parse_region_zone(labels):
    """Read region and zone from topology labels."""
    if not labels:
        return None, None
    region = labels.get("topology.kubernetes.io/region") or labels.get(
        "failure-domain.beta.kubernetes.io/region"
    )
    zone = labels.get("topology.kubernetes.io/zone") or labels.get(
        "failure-domain.beta.kubernetes.io/zone"
    )
    return region, zone


def parse_node_cloud_info(provider_id, labels):
    """Return provider, instanceType, region, zone for a node."""
    labels = labels or {}
    provider = parse_provider_id(provider_id)
    instance_type = parse_instance_type(labels)
    region, zone = parse_region_zone(labels)
    return {
        "provider": provider,
        "instanceType": instance_type,
        "region": region,
        "zone": zone,
    }
