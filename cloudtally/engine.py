"""Synchronous inventory → pricing engine shared by `run` and `watch`."""
from __future__ import annotations

import logging
import time

from cloudtally.providers.base import CloudProvider
from cloudtally.store import Store

log = logging.getLogger("cloudtally.engine")

# a failed catalog build (seed fallback) retries sooner
CATALOG_FALLBACK_RETRY_SECONDS = 600.0


class Engine:
    def __init__(self, provider: CloudProvider, store: Store,
                 catalog_refresh_seconds: float = 86400.0):
        self.provider = provider
        self.store = store
        self.catalog_refresh_seconds = catalog_refresh_seconds
        self._catalog_built_at = 0.0

    def ensure_catalog(self) -> None:
        refresh_after = (CATALOG_FALLBACK_RETRY_SECONDS
                         if getattr(self.provider, "catalog_fallback", False)
                         else self.catalog_refresh_seconds)
        if time.time() - self._catalog_built_at > refresh_after:
            self.provider.build_catalog()
            self._catalog_built_at = time.time()

    def poll_once(self, force_detail: bool = False) -> dict:
        """One inventory+pricing pass; records a snapshot and returns the summary."""
        self.ensure_catalog()
        ts = time.time()
        resources = self.provider.fetch_resources(ts)
        costs = [self.provider.price(r) for r in resources]
        return self.store.update(costs, ts, force_detail=force_detail)


def backfill_demo_history(provider: CloudProvider, store: Store,
                          hours: float = 168.0, step_seconds: float = 900.0) -> int:
    """Seed history by replaying the demo fleet at past timestamps."""
    if store.snapshot_count() > 0:
        return 0
    now = time.time()
    n = 0
    ts = now - hours * 3600
    while ts < now - step_seconds:
        resources = provider.fetch_resources(ts)
        costs = [provider.price(r) for r in resources]
        store.update(costs, ts)
        ts += step_seconds
        n += 1
    log.info("backfilled %d demo history snapshots", n)
    return n
