"""Tests for `*-custom-*` machine type names.

The custom-shape branches called float() on the vCPU and memory fields without
guarding, so a name whose vCPU field isn't numeric raised out of price() and
aborted the whole poll instead of coming back UNPRICED. The E2 shared-core
custom shapes are exactly that shape and they are real:
`e2-custom-{micro,small,medium}-{memoryMiB}` names a size, not a vCPU count.
"""
from __future__ import annotations

import pytest

from cloudtally.cli import _build
from cloudtally.config import Config
from cloudtally.models import UNPRICED, Resource
from cloudtally.pricing.catalog import seed_gcp_catalog
from cloudtally.providers.gcp.machine_types import parse_machine_type
from cloudtally.providers.gcp.mapper import GCPMapper


def _instance(machine_type: str, name: str = "vm-1") -> Resource:
    return Resource(
        id=name, name=name, provider="gcp", service="Compute Engine",
        asset_type="compute.googleapis.com/Instance", project="acme-prod",
        region="us-central1", status="RUNNING",
        attrs={"machine_type": machine_type, "spot": False},
    )


@pytest.mark.parametrize(
    "machine_type",
    [
        "e2-custom-micro-1024",
        "e2-custom-micro-2048",
        "e2-custom-small-2048",
        "e2-custom-medium-4096",
    ],
)
def test_e2_shared_core_custom_is_unrecognized_not_fatal(machine_type):
    assert parse_machine_type(machine_type) is None


@pytest.mark.parametrize(
    "machine_type",
    ["custom-x-8192", "custom-2-", "n2-custom--20480", "e2-custom-4-lots"],
)
def test_malformed_custom_names_return_none(machine_type):
    assert parse_machine_type(machine_type) is None


@pytest.mark.parametrize(
    "machine_type,family,vcpu,ram_gib",
    [
        ("custom-2-8192", "n1", 2.0, 8.0),
        ("n2-custom-4-20480", "n2", 4.0, 20.0),
        ("n2-custom-8-32768-ext", "n2", 8.0, 32.0),
    ],
)
def test_valid_custom_shapes_still_parse(machine_type, family, vcpu, ram_gib):
    shape = parse_machine_type(machine_type)
    assert shape is not None, f"{machine_type} should still parse"
    assert (shape.family, shape.vcpu, shape.ram_gib) == (family, vcpu, ram_gib)


def test_mapper_reports_unpriced_instead_of_raising():
    cost = GCPMapper(seed_gcp_catalog()).price(_instance("e2-custom-medium-4096"))
    assert cost.confidence == UNPRICED
    assert cost.hourly == 0.0
    assert "e2-custom-medium-4096" in cost.note


def test_one_bad_machine_type_does_not_kill_the_poll(tmp_path):
    cfg = Config(mode="demo", db_path=str(tmp_path / "h.db"))
    provider, store, engine = _build(cfg)

    demo_fleet = provider.fetch_resources
    bad = _instance("e2-custom-medium-4096", name="vm-bad")
    provider.fetch_resources = lambda ts: [*demo_fleet(ts), bad]

    summary = engine.poll_once(force_detail=True)

    assert summary["total_hourly"] > 0
    assert summary["priced_count"] > 0
    assert summary["confidence_counts"][UNPRICED] >= 1
    by_id = {r["id"]: r for r in store.latest_snapshot()["resources"]}
    assert by_id["vm-bad"]["confidence"] == UNPRICED
