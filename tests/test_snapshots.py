"""Tests for PD snapshot pricing.

Snapshots bill on stored bytes — the incremental, compressed size — not the
source disk's provisioned size, so the mapper reads storageBytes and never
falls back to diskSizeGb. Standard and archive are separate rates.
"""
from __future__ import annotations

import pytest

from cloudtally.models import ESTIMATED, UNPRICED, USAGE_BASED, Resource
from cloudtally.pricing.catalog import seed_gcp_catalog
from cloudtally.pricing.gcp_live import _sku_key
from cloudtally.providers.gcp.live_inventory import PRICED_ASSET_TYPES, _normalize
from cloudtally.providers.gcp.mapper import _HANDLERS, GCPMapper

ASSET = "compute.googleapis.com/Snapshot"
GIB = 1024 ** 3


def _price(size_gb, snapshot_type="STANDARD", region="us-central1"):
    r = Resource(
        id="snap-1", name="snap-1", provider="gcp", service="Compute Engine",
        asset_type=ASSET, project="p", region=region, status="READY",
        attrs={"size_gb": size_gb, "snapshot_type": snapshot_type},
    )
    return GCPMapper(seed_gcp_catalog()).price(r)


def _asset(data):
    return {
        "assetType": ASSET,
        "name": "//compute.googleapis.com/projects/p/global/snapshots/snap-1",
        "resource": {"data": {"name": "snap-1", **data}},
    }


def test_asset_type_is_wired_in_both_places():
    assert ASSET in PRICED_ASSET_TYPES
    assert ASSET in _HANDLERS


def test_standard_and_archive_have_different_rates():
    std = _price(100)
    arc = _price(100, "ARCHIVE")
    assert std.monthly == pytest.approx(100 * 0.050, abs=1e-6)
    assert arc.monthly == pytest.approx(100 * 0.019, abs=1e-6)
    assert arc.hourly < std.hourly


def test_snapshot_is_estimated_not_exact():
    # stored bytes move when other snapshots in the chain are deleted
    assert _price(100).confidence == ESTIMATED


def test_missing_stored_bytes_is_usage_based_not_zero():
    cost = _price(None)
    assert cost.confidence == USAGE_BASED
    assert cost.hourly == 0.0
    assert "not in inventory" in cost.note


@pytest.mark.parametrize("region", ["us", "eu", "asia"])
def test_multi_region_bills_above_the_regional_rate(region):
    assert _price(100, region=region).monthly == pytest.approx(100 * 0.065, abs=1e-6)
    assert _price(100, region=region).hourly > _price(100, region="us-central1").hourly


def test_unknown_storage_class_is_unpriced_not_guessed():
    r = Resource(
        id="snap-x", name="snap-x", provider="gcp", service="Compute Engine",
        asset_type=ASSET, project="p", region="us-central1", status="READY",
        attrs={"size_gb": 100, "snapshot_type": "STANDARD"},
    )
    empty = GCPMapper(seed_gcp_catalog())
    empty.catalog._index.clear()
    cost = empty.price(r)
    assert cost.confidence == UNPRICED
    assert cost.hourly == 0.0


def test_live_sku_matching_does_not_steal_disk_skus():
    cat = {"usageType": "OnDemand"}
    assert _sku_key("Compute Engine", "Storage PD Snapshot in Americas", cat) == "snapshot.standard.capacity"
    assert _sku_key("Compute Engine", "Archive Snapshot Storage in Americas", cat) == "snapshot.archive.capacity"
    assert _sku_key("Compute Engine", "Storage PD Capacity in Americas", cat) == "pd-standard.capacity"
    assert _sku_key("Compute Engine", "SSD backed PD Capacity in Americas", cat) == "pd-ssd.capacity"


@pytest.mark.parametrize("description", [
    "Snapshot Egress in Americas",
    "Commercial Snapshot Restore in Americas",
    "Snapshot Data Transfer in Americas",
])
def test_non_storage_snapshot_skus_are_skipped(description):
    assert _sku_key("Compute Engine", description, {"usageType": "OnDemand"}) is None


def test_normalize_reads_storage_bytes_not_disk_size():
    r = _normalize(_asset({
        "storageBytes": str(118 * GIB),   # string-encoded int64, as the API returns it
        "diskSizeGb": "500",              # far larger; must not be used
        "snapshotType": "STANDARD",
        "storageLocations": ["us-central1"],
    }), "p")
    assert r.attrs["size_gb"] == pytest.approx(118.0)
    assert r.region == "us-central1"


def test_normalize_flags_absent_storage_bytes():
    r = _normalize(_asset({"snapshotType": "ARCHIVE", "storageLocations": ["US"]}), "p")
    assert r.attrs["size_gb"] is None
    assert r.region == "us"
    assert GCPMapper(seed_gcp_catalog()).price(r).confidence == USAGE_BASED


def test_live_normalize_matches_the_demo_attrs_shape():
    from cloudtally.providers.gcp.demo_fleet import _snapshot as demo_snapshot

    live = _normalize(_asset({
        "storageBytes": str(10 * GIB), "snapshotType": "STANDARD",
        "storageLocations": ["us-central1"],
    }), "p")
    assert set(live.attrs) == set(demo_snapshot("p", "s", "us-central1", 10).attrs)


def test_demo_fleet_prices_its_snapshots():
    from cloudtally.providers.gcp.demo_fleet import generate

    mapper = GCPMapper(seed_gcp_catalog())
    snaps = [r for r in generate(1750000000.0) if r.asset_type == ASSET]
    assert snaps, "demo fleet should carry snapshots"
    assert all(mapper.price(s).hourly > 0 for s in snaps)
