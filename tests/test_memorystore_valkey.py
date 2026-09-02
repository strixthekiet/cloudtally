"""Tests for Memorystore for Valkey support.

Our node pools already got covered in test_arm64_machine_types.py, but Memorystore was a
separate gap: the inventory fetcher only knew about the legacy `redis.googleapis.com/Instance`
asset type. Real Memorystore for Valkey instances live under
`memorystore.googleapis.com/Instance`, which never showed up at all, so those instances were
silently missing from cost reports rather than UNPRICED.
"""
from __future__ import annotations

import pytest

from cloudtally.models import Resource
from cloudtally.pricing.catalog import seed_gcp_catalog
from cloudtally.pricing.gcp_live import _MEMORYSTORE_NODE_PREFIX, _MEMORYSTORE_NODE_TYPES
from cloudtally.providers.gcp.live_inventory import PRICED_ASSET_TYPES, SERVICE_NAMES
from cloudtally.providers.gcp.mapper import GCPMapper


def _resource(node_type, shard_count=1, replica_count=0, region="us-west1"):
    return Resource(
        id="mem-1", name="mem-1", provider="gcp", service="Memorystore for Valkey",
        asset_type="memorystore.googleapis.com/Instance", project="p", region=region,
        status="ACTIVE",
        attrs={"node_type": node_type, "shard_count": shard_count, "replica_count": replica_count},
    )


def test_asset_type_is_tracked():
    assert "memorystore.googleapis.com/Instance" in PRICED_ASSET_TYPES
    assert SERVICE_NAMES["memorystore.googleapis.com"] == "Memorystore for Valkey"


@pytest.mark.parametrize("label,key", list(_MEMORYSTORE_NODE_TYPES.items()))
def test_seed_catalog_has_every_node_type(label, key):
    catalog = seed_gcp_catalog()
    assert catalog.hourly_rate(f"memorystore.{key}.node", "us-west1") is not None, (
        f"missing seed price for {label} ({key})"
    )


def test_prices_standard_small_no_replicas():
    # ph-valkey-canary: STANDARD_SMALL, 1 shard, 0 replicas. Real published price for this
    # node type is $0.1425/h in us-central1/us-west1.
    catalog = seed_gcp_catalog()
    mapper = GCPMapper(catalog)
    cost = mapper.price(_resource("STANDARD_SMALL", shard_count=1, replica_count=0))
    assert cost.hourly == pytest.approx(0.1425, abs=1e-4)


def test_prices_highmem_xlarge_with_replica():
    # ph-valkey-production: HIGHMEM_XLARGE, 1 shard, 1 replica means 2 nodes billed.
    # Real published price is $0.8581/h per node in us-central1/us-west1.
    catalog = seed_gcp_catalog()
    mapper = GCPMapper(catalog)
    cost = mapper.price(_resource("HIGHMEM_XLARGE", shard_count=1, replica_count=1))
    assert cost.hourly == pytest.approx(0.8581 * 2, abs=1e-4)


def test_prices_multiple_shards():
    # 3 shards x (1 primary + 1 replica) = 6 nodes.
    catalog = seed_gcp_catalog()
    mapper = GCPMapper(catalog)
    cost = mapper.price(_resource("STANDARD_LARGE", shard_count=3, replica_count=1))
    assert cost.hourly == pytest.approx(0.5698 * 6, abs=1e-4)


def test_unknown_node_type_is_unpriced():
    catalog = seed_gcp_catalog()
    mapper = GCPMapper(catalog)
    cost = mapper.price(_resource("SOME_FUTURE_NODE_TYPE"))
    assert cost.hourly == 0
    assert "SOME_FUTURE_NODE_TYPE" in cost.note or "some_future_node_type" in cost.note.lower()


def test_live_sku_regex_matches_every_node_type_description():
    import re

    pattern = re.compile(rf"^({_MEMORYSTORE_NODE_PREFIX}) Node ")
    # Real descriptions pulled from the live Billing Catalog API (Cloud Memorystore service),
    # one per node type, region name appended.
    descriptions = [
        "Shared Core Nano Node Oregon",
        "Custom Pico Node Oregon",
        "Custom Micro Node Oregon",
        "Custom Mini Node Oregon",
        "Standard Small Node Oregon",
        "Highmem Medium Node Oregon",
        "Highcpu Medium Node Oregon",
        "Standard Large Node Oregon",
        "Highmem XLarge Node Oregon",
        "Highmem 2xlarge Node Oregon",
    ]
    for d in descriptions:
        assert pattern.match(d), f"regex should match {d!r}"


def test_live_sku_regex_does_not_match_backup_or_egress_skus():
    import re

    pattern = re.compile(rf"^({_MEMORYSTORE_NODE_PREFIX}) Node ")
    # Real SKU descriptions from the same service that must NOT be treated as node pricing.
    non_node_descriptions = [
        "Memorystore for Valkey: Backups in Taiwan",
        "Memorystore for Valkey: AOF Storage Berlin",
        "Cloud Memorystore: Network Data Transfer GCP Inter Region between Latin America and Africa",
    ]
    for d in non_node_descriptions:
        assert not pattern.match(d), f"regex should not match {d!r}"
