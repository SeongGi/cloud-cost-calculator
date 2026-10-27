"""Every service name the app's follow-up suggestions say must resolve to a catalog service."""

import pytest

from pricing.engine import load_catalog, resolve_service

# Names used in apps/app/src/lib/follow-ups.ts "adds" questions, per cloud.
NAMES = {
    "aws": ["VPN", "ALB", "RDS", "ElastiCache", "AWS WAF", "NAT Gateway", "S3", "CloudWatch"],
    "azure": ["VPN", "Application Gateway", "PostgreSQL", "Azure Cache for Redis", "Azure Firewall", "Blob Storage", "Log Analytics"],
    "gcp": ["VPN", "Cloud Load Balancing", "Cloud SQL", "Memorystore", "Cloud Armor", "Cloud NAT", "Cloud Storage", "Cloud Logging"],
}


@pytest.mark.parametrize("cloud,name", [(c, n) for c, ns in NAMES.items() for n in ns])
def test_suggested_name_resolves(cloud, name):
    service, _ = resolve_service(cloud, name, load_catalog(cloud))
    assert service is not None
