"""Tests for T2A (Ampere Altra) and C4A (Axion) arm64 machine support.

`parse_machine_type` needs to recognize these families or instances on them
come back UNPRICED. The live SKU matcher's family regex needs T2A/C4A too,
otherwise even a correctly-parsed instance has no rate to price against.
"""
from __future__ import annotations

import pytest

from cloudtally.models import Resource
from cloudtally.pricing.catalog import seed_gcp_catalog
from cloudtally.pricing.gcp_live import _FAMILIES
from cloudtally.providers.gcp.machine_types import parse_machine_type
from cloudtally.providers.gcp.mapper import GCPMapper


@pytest.mark.parametrize(
    "machine_type,vcpu,ram_gib",
    [
        ("t2a-standard-4", 4.0, 16.0),
        ("t2a-standard-48", 48.0, 192.0),
        ("c4a-standard-4", 4.0, 16.0),
        ("c4a-highcpu-8", 8.0, 16.0),
        ("c4a-highmem-2", 2.0, 16.0),
        # These have a -lssd or -metal suffix, real C4A shapes per gcloud compute
        # machine-types list. The parser needs to handle 4-part names, not just 3.
        ("c4a-standard-4-lssd", 4.0, 16.0),
        ("c4a-highmem-96-metal", 96.0, 768.0),
    ],
)
def test_parses_arm64_shapes(machine_type, vcpu, ram_gib):
    shape = parse_machine_type(machine_type)
    assert shape is not None, f"{machine_type} should parse"
    assert shape.vcpu == vcpu
    assert shape.ram_gib == ram_gib


def test_family_regex_matches_live_sku_descriptions():
    import re

    on_demand = re.compile(rf"^({_FAMILIES}) Instance (Core|Ram) running")
    spot = re.compile(rf"^Spot Preemptible ({_FAMILIES}) Instance (Core|Ram)")

    assert on_demand.match("T2A Instance Core running in Americas")
    assert on_demand.match("T2A Instance Ram running in Americas")
    assert on_demand.match("C4A Instance Core running in Americas")
    assert on_demand.match("C4A Instance Ram running in Americas")
    assert spot.match("Spot Preemptible T2A Instance Core in Americas")
    assert spot.match("Spot Preemptible C4A Instance Ram in Americas")


def test_seed_catalog_prices_t2a_instance():
    catalog = seed_gcp_catalog()
    mapper = GCPMapper(catalog)
    r = Resource(
        id="t2a-1", name="t2a-1", provider="gcp", service="Compute Engine",
        asset_type="compute.googleapis.com/Instance", project="p", region="us-west1",
        status="RUNNING", attrs={"machine_type": "t2a-standard-4", "spot": False},
    )
    cost = mapper.price(r)
    assert cost.hourly > 0
    # Google publishes t2a-standard-4 at $0.154/h on-demand in us-central1. We picked the
    # seed cpu/ram values so this comes out to that number.
    assert cost.hourly == pytest.approx(0.154, abs=1e-4)


def test_seed_catalog_prices_c4a_instance_on_demand_and_spot():
    catalog = seed_gcp_catalog()
    mapper = GCPMapper(catalog)

    on_demand = Resource(
        id="c4a-1", name="c4a-1", provider="gcp", service="Compute Engine",
        asset_type="compute.googleapis.com/Instance", project="p", region="us-west1",
        status="RUNNING", attrs={"machine_type": "c4a-standard-4", "spot": False},
    )
    spot = Resource(
        id="c4a-2", name="c4a-2", provider="gcp", service="Compute Engine",
        asset_type="compute.googleapis.com/Instance", project="p", region="us-west1",
        status="RUNNING", attrs={"machine_type": "c4a-highcpu-4", "spot": True},
    )

    # We solved cpu/ram from these two published prices: c4a-standard-4 on-demand at
    # $0.1796/h and c4a-highcpu-4 spot at $0.0796/h. Both should come out exact.
    assert mapper.price(on_demand).hourly == pytest.approx(0.1796, abs=1e-4)
    assert mapper.price(spot).hourly == pytest.approx(0.0796, abs=1e-4)


def test_unrecognized_arm64_like_name_still_unpriced():
    """A made-up family name should still fail to parse, not accidentally match something."""
    shape = parse_machine_type("z9a-standard-4")
    assert shape is None
