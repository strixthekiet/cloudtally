"""Tests for Cloud NAT pricing.

Cloud NAT bills through the Cloud Router hosting it: nothing for a BGP-only
router, per VM-hour up to the 32-VM cap, then a flat per-gateway rate. Data
processing is usage-based and must stay out of the total.
"""
from __future__ import annotations

import pytest

from cloudtally.models import ESTIMATED, EXACT, Resource
from cloudtally.pricing.catalog import seed_gcp_catalog
from cloudtally.pricing.gcp_live import _sku_key
from cloudtally.providers.gcp.live_inventory import _normalize
from cloudtally.providers.gcp.mapper import _NAT_VM_CAP, GCPMapper

PER_VM = 0.0014
CAPPED = 0.044


def _price(nat_count=1, nat_vm_count=None, region="us-central1"):
    r = Resource(
        id="router-1", name="router-1", provider="gcp", service="Compute Engine",
        asset_type="compute.googleapis.com/Router", project="p", region=region,
        attrs={"nat_count": nat_count, "nat_vm_count": nat_vm_count},
    )
    return GCPMapper(seed_gcp_catalog()).price(r)


def test_router_without_nat_is_free():
    cost = _price(nat_count=0)
    assert cost.confidence == EXACT
    assert cost.hourly == 0.0
    assert "no charge" in cost.note


def test_below_the_cap_bills_per_vm():
    cost = _price(nat_vm_count=12)
    assert cost.confidence == ESTIMATED
    assert cost.hourly == pytest.approx(12 * PER_VM)


def test_at_or_above_the_cap_bills_a_flat_gateway_rate():
    at_cap = _price(nat_vm_count=_NAT_VM_CAP)
    assert at_cap.hourly == pytest.approx(CAPPED)
    assert _price(nat_vm_count=200).hourly == pytest.approx(at_cap.hourly)


def test_the_cap_actually_caps():
    assert _price(nat_vm_count=_NAT_VM_CAP - 1).hourly < _price(nat_vm_count=_NAT_VM_CAP).hourly


def test_unknown_vm_count_falls_back_to_the_cap_and_says_so():
    cost = _price(nat_vm_count=None)
    assert cost.confidence == ESTIMATED
    assert cost.hourly == pytest.approx(CAPPED)
    assert "not in inventory" in cost.note


def test_multiple_gateways_multiply():
    assert _price(nat_count=3).hourly == pytest.approx(3 * CAPPED)


def test_rate_is_flat_across_regions():
    assert _price(nat_vm_count=10, region="asia-east1").hourly == pytest.approx(
        _price(nat_vm_count=10, region="us-central1").hourly)


def test_only_the_uptime_sku_is_priced():
    cat = {"usageType": "OnDemand"}
    assert _sku_key("Compute Engine", "NAT Gateway: Uptime charge in Americas", cat) == "nat.gateway.vm"
    assert _sku_key("Compute Engine", "NAT Gateway: Data Processing charge in Americas", cat) is None


def test_live_normalize_matches_the_demo_attrs_shape():
    from cloudtally.providers.gcp.demo_fleet import _router as demo_router

    live = _normalize({
        "assetType": "compute.googleapis.com/Router",
        "name": "//compute.googleapis.com/projects/p/regions/us-central1/routers/r1",
        "resource": {"data": {
            "name": "r1",
            "region": "https://www.googleapis.com/compute/v1/projects/p/regions/us-central1",
            "nats": [{"name": "nat-1"}],
        }},
    }, "p")
    assert live.region == "us-central1"
    assert live.attrs["nat_count"] == 1
    assert set(live.attrs) == set(demo_router("p", "r1", "us-central1").attrs)


def test_bgp_only_router_normalizes_to_zero_gateways():
    live = _normalize({
        "assetType": "compute.googleapis.com/Router",
        "name": "//compute.googleapis.com/projects/p/regions/us-central1/routers/r2",
        "resource": {"data": {"name": "r2", "region": "regions/us-central1"}},
    }, "p")
    assert live.attrs["nat_count"] == 0
    assert GCPMapper(seed_gcp_catalog()).price(live).hourly == 0.0
