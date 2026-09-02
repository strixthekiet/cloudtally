"""Contract checks for adding a priced asset type.

Adding one touches four places that have to stay keyed together. These cover
the parts that can be checked generically, so the next asset type can't land
half-wired the way a missing seed price would.
"""
from __future__ import annotations

from cloudtally.pricing.catalog import _UNIT_TO_HOURLY
from cloudtally.pricing.seed_gcp import BASE_PRICES, FLAT_RATE_KEYS
from cloudtally.providers.gcp.live_inventory import PRICED_ASSET_TYPES, SERVICE_NAMES
from cloudtally.providers.gcp.mapper import _HANDLERS


def test_every_handler_is_inventoried_and_vice_versa():
    assert set(_HANDLERS) == set(PRICED_ASSET_TYPES)


def test_every_priced_asset_domain_has_a_service_name():
    assert {t.split("/")[0] for t in PRICED_ASSET_TYPES} <= set(SERVICE_NAMES)


def test_every_seed_unit_is_convertible():
    bad = {k: u for k, (_, u, _) in BASE_PRICES.items() if u not in _UNIT_TO_HOURLY}
    assert not bad, f"an unconvertible unit silently unprices the resource: {bad}"


def test_seed_prices_are_positive():
    assert not {k: p for k, (p, _, _) in BASE_PRICES.items() if p <= 0}


def test_flat_rate_keys_are_real_keys():
    assert FLAT_RATE_KEYS <= set(BASE_PRICES)
