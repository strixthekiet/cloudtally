"""Pricing catalog: normalized SKU entries indexed for lookup.

Entries come from the bundled seed snapshot or the live Cloud Billing Catalog
API; both use the same schema: {key, region, price, unit, description}.
"""
from __future__ import annotations

import time
from cloudtally.models import HOURS_PER_MONTH

# unit -> factor converting catalog price to price per quantity-unit per hour
_UNIT_TO_HOURLY = {
    "h": 1.0,
    "gib_h": 1.0,
    "gib_mo": 1.0 / HOURS_PER_MONTH,
    "mo": 1.0 / HOURS_PER_MONTH,
}


class PricingCatalog:
    def __init__(self, entries: list[dict], source: str, currency: str = "USD"):
        self.source = source
        self.currency = currency
        self.captured_at = time.time()
        self._index: dict[tuple[str, str], dict] = {}
        for e in entries:
            self._index[(e["key"], e["region"])] = e

    def __len__(self) -> int:
        return len(self._index)

    def lookup(self, key: str, region: str) -> dict | None:
        return self._index.get((key, region)) or self._index.get((key, "global"))

    def hourly_rate(self, key: str, region: str) -> float | None:
        e = self.lookup(key, region)
        if e is None:
            return None
        factor = _UNIT_TO_HOURLY.get(e["unit"])
        if factor is None:
            return None
        return e["price"] * factor

    def merge_missing_from(self, other: "PricingCatalog") -> int:
        """Backfill entries absent from this catalog; returns how many."""
        added = 0
        for k, e in other._index.items():
            if k not in self._index:
                self._index[k] = e
                added += 1
        return added


def seed_gcp_catalog(currency: str = "USD") -> PricingCatalog:
    from cloudtally.pricing.fx import usd_rate
    from cloudtally.pricing.seed_gcp import build_seed_entries

    entries = build_seed_entries()
    rate = usd_rate(currency)
    if rate != 1.0:
        for e in entries:
            e["price"] = round(e["price"] * rate, 8)
    return PricingCatalog(entries, source="seed", currency=currency.upper())
