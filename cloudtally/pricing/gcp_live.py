"""Live GCP pricing via the Cloud Billing Catalog API, normalized to the
catalog-key schema shared with the seed snapshot. SKUs that match no known
pattern are skipped; the seed backfills the gaps."""
from __future__ import annotations

import logging
import re

log = logging.getLogger("cloudtally.pricing.gcp")

BILLING_BASE = "https://cloudbilling.googleapis.com/v1"

WANTED_SERVICES = {
    "Compute Engine",
    "Cloud Storage",
    "Cloud SQL",
    "Kubernetes Engine",
    "Memorystore for Redis",
}

_FAMILIES = "E2|N1 Predefined|N2D|N2|C2D|C2|T2D"

_MT_FLAT = {
    "E2 Micro Instance with burstable CPU": "e2-micro.flat",
    "E2 Small Instance with 1 VCPU": "e2-small.flat",
    "E2 Medium Instance with 1 VCPU": "e2-medium.flat",
    "Micro Instance with burstable CPU": "f1-micro.flat",
    "Small Instance with 1 VCPU": "g1-small.flat",
}


def _family_key(raw: str) -> str:
    return raw.replace(" Predefined", "").lower()


def _sku_key(service: str, description: str, category: dict) -> str | None:
    """Map one live SKU to a catalog key, or None to skip it."""
    d = description
    usage = category.get("usageType", "")

    if service == "Compute Engine":
        m = re.match(rf"^Spot Preemptible ({_FAMILIES}) Instance (Core|Ram)", d)
        if m:
            part = "cpu" if m.group(2) == "Core" else "ram"
            return f"{_family_key(m.group(1))}.{part}.spot"
        m = re.match(rf"^({_FAMILIES}) Instance (Core|Ram) running", d)
        if m and usage == "OnDemand":
            part = "cpu" if m.group(2) == "Core" else "ram"
            return f"{_family_key(m.group(1))}.{part}"
        for prefix, key in _MT_FLAT.items():
            if d.startswith(prefix) and usage == "OnDemand":
                return key
        if d.startswith("Storage PD Capacity"):
            return "pd-standard.capacity"
        if d.startswith("Balanced PD Capacity"):
            return "pd-balanced.capacity"
        if d.startswith("SSD backed PD Capacity"):
            return "pd-ssd.capacity"
        if d.startswith("Extreme PD Capacity"):
            return "pd-extreme.capacity"
        if "External IP Charge" in d and "Standard VM" in d:
            return "static-ip.attached"
        if d.startswith("Static Ip Charge"):
            return "static-ip.unattached"
        if "Forwarding Rule Minimum Service Charge" in d:
            return "lb.forwarding-rule"
        return None

    if service == "Kubernetes Engine":
        if d.startswith("Regional Kubernetes Clusters"):
            return "gke.cluster.regional"
        if d.startswith("Zonal Kubernetes Clusters"):
            return "gke.cluster.zonal"
        return None

    if service == "Cloud Storage":
        m = re.match(r"^(Standard|Nearline|Coldline|Archive) Storage", d)
        if m and "Storage" in category.get("resourceFamily", ""):
            return f"gcs.{m.group(1).lower()}.capacity"
        return None

    if service == "Cloud SQL":
        m = re.match(
            r"^Cloud SQL for (PostgreSQL|MySQL): (Zonal|Regional) - (vCPU|RAM|Standard storage|SSD storage)",
            d,
        )
        if m:
            engine = m.group(1).lower()
            scope = m.group(2).lower()
            part = m.group(3)
            if part == "vCPU":
                return f"cloudsql.{engine}.{scope}.cpu"
            if part == "RAM":
                return f"cloudsql.{engine}.{scope}.ram"
            if part == "SSD storage":
                return f"cloudsql.storage-ssd.{scope}"
            return f"cloudsql.storage-hdd.{scope}"
        # shared-core tiers: live descriptions say "Micro/Small instance",
        # not the tier name; regional SKUs already include the HA standby
        m = re.match(
            r"^Cloud SQL for (?:PostgreSQL|MySQL): (Zonal|Regional) - (Micro|Small) instance", d
        )
        if m:
            tier = "db-f1-micro" if m.group(2) == "Micro" else "db-g1-small"
            return f"cloudsql.shared.{tier}.{m.group(1).lower()}"
        if "db-f1-micro" in d:
            return "cloudsql.shared.db-f1-micro.zonal"
        if "db-g1-small" in d:
            return "cloudsql.shared.db-g1-small.zonal"
        return None

    if service == "Memorystore for Redis":
        if "Capacity Basic" in d:
            return "redis.basic.capacity"
        if "Capacity Standard" in d:
            return "redis.standard_ha.capacity"
        return None

    return None


def _unit_from_expression(expr: dict) -> str | None:
    usage_unit = expr.get("usageUnit", "")
    return {
        "h": "h",
        "GiBy.h": "gib_h",
        "GiBy.mo": "gib_mo",
        "mo": "mo",
    }.get(usage_unit)


def _price_from_expression(expr: dict) -> float | None:
    """First non-zero tiered rate."""
    for tier in expr.get("tieredRates", []):
        p = tier.get("unitPrice", {})
        value = float(p.get("units", 0) or 0) + float(p.get("nanos", 0) or 0) / 1e9
        if value > 0:
            return value
    return None


def fetch_gcp_catalog_entries(session, api_key: str | None = None,
                              currency: str = "USD") -> list[dict]:
    """``session`` is a requests-compatible session (AuthorizedSession for
    ADC, plain requests.Session with an API key). The Catalog API converts
    prices to ``currency`` server-side."""

    def get(url: str, params: dict) -> dict:
        if api_key:
            params = {**params, "key": api_key}
        resp = session.get(url, params=params, timeout=60)
        if resp.status_code != 200:
            try:
                msg = resp.json()["error"]["message"]
            except Exception:
                msg = resp.text[:200]
            raise RuntimeError(f"Billing Catalog API {resp.status_code}: {msg}")
        return resp.json()

    # resolve service display names -> service resource names
    services: dict[str, str] = {}
    token = None
    while True:
        params = {"pageSize": 5000}
        if token:
            params["pageToken"] = token
        page = get(f"{BILLING_BASE}/services", params)
        for svc in page.get("services", []):
            if svc.get("displayName") in WANTED_SERVICES:
                services[svc["displayName"]] = svc["name"]
        token = page.get("nextPageToken")
        if not token:
            break

    entries: list[dict] = []
    for display_name, service_name in services.items():
        token = None
        matched = 0
        while True:
            params = {"pageSize": 5000, "currencyCode": currency.upper()}
            if token:
                params["pageToken"] = token
            page = get(f"{BILLING_BASE}/{service_name}/skus", params)
            for sku in page.get("skus", []):
                key = _sku_key(display_name, sku.get("description", ""), sku.get("category", {}))
                if key is None:
                    continue
                infos = sku.get("pricingInfo", [])
                if not infos:
                    continue
                expr = infos[0].get("pricingExpression", {})
                unit = _unit_from_expression(expr)
                price = _price_from_expression(expr)
                if unit is None or price is None:
                    continue
                regions = sku.get("serviceRegions", []) or ["global"]
                for region in regions:
                    entries.append(
                        {
                            "key": key,
                            "region": region,
                            "price": price,
                            "unit": unit,
                            "description": sku.get("description", key),
                        }
                    )
                    matched += 1
            token = page.get("nextPageToken")
            if not token:
                break
        log.info("catalog: %s -> %d normalized entries", display_name, matched)

    return entries
