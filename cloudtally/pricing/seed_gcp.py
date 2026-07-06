"""Bundled GCP list-price snapshot (approximate, mid-2026, us-central1 base).
Used in demo mode and as a fallback when the Billing Catalog API is
unreachable. Catalog keys are the contract between SKU sources and the
mapper: the live normalizer reduces real SKUs to these same keys.

Units:
  h       -> per hour (quantity is a count: vCPU, rules, instances)
  gib_h   -> per GiB-hour
  gib_mo  -> per GiB-month (converted to hourly by the catalog)
  mo      -> per month flat
"""
from __future__ import annotations

# key -> (base price in us-central1, unit, description)
BASE_PRICES: dict[str, tuple[float, str, str]] = {
    # Compute Engine
    "e2.cpu": (0.021811, "h", "E2 vCPU"),
    "e2.ram": (0.002923, "gib_h", "E2 RAM"),
    "e2.cpu.spot": (0.006543, "h", "E2 vCPU (Spot)"),
    "e2.ram.spot": (0.000877, "gib_h", "E2 RAM (Spot)"),
    "n1.cpu": (0.031611, "h", "N1 vCPU"),
    "n1.ram": (0.004237, "gib_h", "N1 RAM"),
    "n1.cpu.spot": (0.006655, "h", "N1 vCPU (Spot)"),
    "n1.ram.spot": (0.000892, "gib_h", "N1 RAM (Spot)"),
    "n2.cpu": (0.031611, "h", "N2 vCPU"),
    "n2.ram": (0.004237, "gib_h", "N2 RAM"),
    "n2.cpu.spot": (0.007650, "h", "N2 vCPU (Spot)"),
    "n2.ram.spot": (0.001025, "gib_h", "N2 RAM (Spot)"),
    "n2d.cpu": (0.027502, "h", "N2D vCPU"),
    "n2d.ram": (0.003686, "gib_h", "N2D RAM"),
    "n2d.cpu.spot": (0.006656, "h", "N2D vCPU (Spot)"),
    "n2d.ram.spot": (0.000892, "gib_h", "N2D RAM (Spot)"),
    "c2.cpu": (0.033980, "h", "C2 vCPU"),
    "c2.ram": (0.004550, "gib_h", "C2 RAM"),
    "c2.cpu.spot": (0.008223, "h", "C2 vCPU (Spot)"),
    "c2.ram.spot": (0.001101, "gib_h", "C2 RAM (Spot)"),
    "t2d.cpu": (0.027502, "h", "T2D vCPU"),
    "t2d.ram": (0.003686, "gib_h", "T2D RAM"),
    "t2d.cpu.spot": (0.006656, "h", "T2D vCPU (Spot)"),
    "t2d.ram.spot": (0.000892, "gib_h", "T2D RAM (Spot)"),
    # shared-core: flat per instance-hour
    "e2-micro.flat": (0.008376, "h", "e2-micro instance"),
    "e2-small.flat": (0.016751, "h", "e2-small instance"),
    "e2-medium.flat": (0.033503, "h", "e2-medium instance"),
    "f1-micro.flat": (0.007600, "h", "f1-micro instance"),
    "g1-small.flat": (0.025700, "h", "g1-small instance"),
    # persistent disks
    "pd-standard.capacity": (0.040, "gib_mo", "Standard PD capacity"),
    "pd-balanced.capacity": (0.100, "gib_mo", "Balanced PD capacity"),
    "pd-ssd.capacity": (0.170, "gib_mo", "SSD PD capacity"),
    "pd-extreme.capacity": (0.125, "gib_mo", "Extreme PD capacity"),
    # external IPv4
    "static-ip.attached": (0.005, "h", "External IPv4 (in use)"),
    "static-ip.unattached": (0.010, "h", "External IPv4 (reserved, unused)"),
    # GKE cluster management fee (nodes bill as Compute Engine)
    "gke.cluster.regional": (0.100, "h", "GKE regional cluster fee"),
    "gke.cluster.zonal": (0.100, "h", "GKE zonal cluster fee"),
    # Cloud Storage at-rest capacity
    "gcs.standard.capacity": (0.020, "gib_mo", "Standard storage"),
    "gcs.nearline.capacity": (0.010, "gib_mo", "Nearline storage"),
    "gcs.coldline.capacity": (0.004, "gib_mo", "Coldline storage"),
    "gcs.archive.capacity": (0.0012, "gib_mo", "Archive storage"),
    # Cloud SQL
    "cloudsql.postgresql.zonal.cpu": (0.0590, "h", "Cloud SQL PG vCPU (zonal)"),
    "cloudsql.postgresql.zonal.ram": (0.0100, "gib_h", "Cloud SQL PG RAM (zonal)"),
    "cloudsql.postgresql.regional.cpu": (0.1180, "h", "Cloud SQL PG vCPU (HA)"),
    "cloudsql.postgresql.regional.ram": (0.0200, "gib_h", "Cloud SQL PG RAM (HA)"),
    "cloudsql.mysql.zonal.cpu": (0.0590, "h", "Cloud SQL MySQL vCPU (zonal)"),
    "cloudsql.mysql.zonal.ram": (0.0100, "gib_h", "Cloud SQL MySQL RAM (zonal)"),
    "cloudsql.mysql.regional.cpu": (0.1180, "h", "Cloud SQL MySQL vCPU (HA)"),
    "cloudsql.mysql.regional.ram": (0.0200, "gib_h", "Cloud SQL MySQL RAM (HA)"),
    "cloudsql.storage-ssd.zonal": (0.170, "gib_mo", "Cloud SQL SSD storage"),
    "cloudsql.storage-ssd.regional": (0.340, "gib_mo", "Cloud SQL SSD storage (HA)"),
    "cloudsql.storage-hdd.zonal": (0.090, "gib_mo", "Cloud SQL HDD storage"),
    "cloudsql.shared.db-f1-micro.zonal": (0.0105, "h", "Cloud SQL db-f1-micro"),
    "cloudsql.shared.db-f1-micro.regional": (0.0210, "h", "Cloud SQL db-f1-micro (HA)"),
    "cloudsql.shared.db-g1-small.zonal": (0.0350, "h", "Cloud SQL db-g1-small"),
    "cloudsql.shared.db-g1-small.regional": (0.0700, "h", "Cloud SQL db-g1-small (HA)"),
    # Memorystore for Redis, per provisioned GiB-hour
    "redis.basic.capacity": (0.049, "gib_h", "Memorystore Redis (Basic)"),
    "redis.standard_ha.capacity": (0.066, "gib_h", "Memorystore Redis (Standard HA)"),
    # load balancing
    "lb.forwarding-rule": (0.025, "h", "Forwarding rule"),
}

# rough list-price multipliers relative to us-central1
REGION_MULTIPLIERS: dict[str, float] = {
    "us-central1": 1.00,
    "us-east1": 1.00,
    "us-west1": 1.00,
    "europe-west1": 1.10,
    "europe-west2": 1.17,
    "asia-east1": 1.14,
    "asia-southeast1": 1.17,
    "global": 1.00,
}


def build_seed_entries() -> list[dict]:
    entries: list[dict] = []
    for key, (base, unit, desc) in BASE_PRICES.items():
        for region, mult in REGION_MULTIPLIERS.items():
            entries.append(
                {
                    "key": key,
                    "region": region,
                    "price": round(base * mult, 8),
                    "unit": unit,
                    "description": desc,
                }
            )
    return entries
