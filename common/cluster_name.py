"""Canonical cluster names shared by the dashboard and the agent."""

import re

# DNS-1123 subdomain (RFC 1123). Kubernetes uses the same rule for most object names.
_DNS1123_SUBDOMAIN = re.compile(
    r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$"
)
MAX_CLUSTER_NAME_LENGTH = 253


def normalize_cluster_name(name):
    """Lowercase, trim, and validate a cluster name.

    Returns the canonical name, or None when it is not a DNS-1123 subdomain.
    """
    if name is None:
        return None
    canonical = str(name).strip().lower()
    if not canonical or len(canonical) > MAX_CLUSTER_NAME_LENGTH:
        return None
    labels = canonical.split(".")
    if any(len(label) > 63 for label in labels):
        return None
    if not _DNS1123_SUBDOMAIN.match(canonical):
        return None
    return canonical
