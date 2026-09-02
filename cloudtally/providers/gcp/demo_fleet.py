"""Synthetic GCP fleet for demo mode. Everything is a deterministic function
of the timestamp, so repeated polls show believable movement and history
backfill can replay past polls. Attrs mirror the live inventory shapes."""
from __future__ import annotations

import math

from cloudtally.models import Resource

P_PROD = "acme-prod"
P_STAGE = "acme-staging"
P_DATA = "acme-data"


def _res(project: str, service: str, asset_type: str, name: str, region: str,
         zone: str | None = None, status: str = "", labels: dict | None = None,
         **attrs) -> Resource:
    return Resource(
        id=f"gcp/{project}/{asset_type.split('/')[-1].lower()}/{name}",
        name=name,
        provider="gcp",
        service=service,
        asset_type=asset_type,
        project=project,
        region=region,
        zone=zone,
        status=status,
        labels=labels or {},
        attrs=attrs,
    )


def _instance(project, name, region, zone, machine_type, status="RUNNING",
              spot=False, labels=None):
    return _res(project, "Compute Engine", "compute.googleapis.com/Instance",
                name, region, zone, status, labels, machine_type=machine_type, spot=spot)


def _disk(project, name, region, zone, size_gb, disk_type):
    return _res(project, "Compute Engine", "compute.googleapis.com/Disk",
                name, region, zone, "READY", None, size_gb=size_gb, disk_type=disk_type)


def _bucket(project, name, region, storage_class, size_gb):
    return _res(project, "Cloud Storage", "storage.googleapis.com/Bucket",
                name, region, None, "ACTIVE", None,
                storage_class=storage_class, size_gb=size_gb)


def _router(project, name, region, nat_count=0, nat_vm_count=None):
    return _res(project, "Compute Engine", "compute.googleapis.com/Router",
                name, region, None, "", None,
                nat_count=nat_count, nat_vm_count=nat_vm_count)


def _diurnal_extra_web(ts: float) -> int:
    """0..3 extra web instances, peaking mid-afternoon UTC."""
    h = (ts / 3600.0) % 24
    return max(0, min(3, round(1.5 + 1.5 * math.sin(2 * math.pi * (h - 8) / 24))))


def _batch_worker_count(ts: float) -> int:
    """2..6 spot workers, re-decided every 20 minutes."""
    bucket = int(ts // 1200)
    return 2 + (bucket * 2654435761) % 5


def _ci_runner_up(ts: float) -> bool:
    """Ephemeral CI runner, up ~2 of every 3 three-minute windows."""
    return (int(ts // 180) % 3) != 0


def generate(ts: float) -> list[Resource]:
    out: list[Resource] = []
    uc, ew, ae = "us-central1", "europe-west1", "asia-east1"

    # acme-prod
    web_count = 3 + _diurnal_extra_web(ts)
    for i in range(1, web_count + 1):
        zone = f"{uc}-{'a' if i % 2 else 'b'}"
        out.append(_instance(P_PROD, f"web-{i}", uc, zone, "e2-standard-4",
                             labels={"tier": "web"}))
        out.append(_disk(P_PROD, f"web-{i}-boot", uc, zone, 50, "pd-balanced"))

    for i in (1, 2):
        out.append(_instance(P_PROD, f"api-{i}", uc, f"{uc}-a", "n2-standard-8",
                             labels={"tier": "api"}))
        out.append(_disk(P_PROD, f"api-{i}-boot", uc, f"{uc}-a", 100, "pd-balanced"))
        out.append(_disk(P_PROD, f"api-{i}-data", uc, f"{uc}-a", 250, "pd-ssd"))

    out.append(_instance(P_PROD, "worker-1", uc, f"{uc}-b", "c2-standard-8",
                         labels={"tier": "worker"}))
    out.append(_disk(P_PROD, "worker-1-boot", uc, f"{uc}-b", 100, "pd-balanced"))

    # stopped experiment whose disk still bills
    out.append(_instance(P_PROD, "ml-experiment-1", uc, f"{uc}-a", "n1-standard-8",
                         status="TERMINATED", labels={"tier": "ml"}))
    out.append(_disk(P_PROD, "ml-experiment-1-disk", uc, f"{uc}-a", 200, "pd-ssd"))

    out.append(_res(P_PROD, "Kubernetes Engine", "container.googleapis.com/Cluster",
                    "prod-gke", uc, None, "RUNNING", None, location_type="regional"))
    for i in range(1, 7):
        zone = f"{uc}-{'abc'[i % 3]}"
        out.append(_instance(P_PROD, f"gke-prod-gke-pool-{i}", uc, zone, "e2-standard-4",
                             labels={"goog-gke-node": "true"}))
        out.append(_disk(P_PROD, f"gke-prod-gke-pool-{i}-boot", uc, zone, 100, "pd-balanced"))

    out.append(_res(P_PROD, "Cloud SQL", "sqladmin.googleapis.com/Instance",
                    "orders-db", uc, None, "RUNNABLE", None,
                    tier="db-custom-4-15360", engine="POSTGRES_16",
                    availability="REGIONAL", disk_size_gb=500, disk_type="PD_SSD"))
    out.append(_res(P_PROD, "Memorystore for Redis", "redis.googleapis.com/Instance",
                    "session-cache", uc, None, "READY", None,
                    memory_gb=5, tier="STANDARD_HA"))

    out.append(_res(P_PROD, "Compute Engine", "compute.googleapis.com/Address",
                    "web-lb-ip", uc, None, "IN_USE", None, address_type="EXTERNAL"))
    out.append(_res(P_PROD, "Compute Engine", "compute.googleapis.com/Address",
                    "api-lb-ip", uc, None, "IN_USE", None, address_type="EXTERNAL"))
    out.append(_res(P_PROD, "Compute Engine", "compute.googleapis.com/Address",
                    "legacy-ip", uc, None, "RESERVED", None, address_type="EXTERNAL"))
    out.append(_res(P_PROD, "Compute Engine", "compute.googleapis.com/ForwardingRule",
                    "web-https-fr", uc, None, "", None))
    out.append(_res(P_PROD, "Compute Engine", "compute.googleapis.com/ForwardingRule",
                    "api-https-fr", uc, None, "", None))

    out.append(_router(P_PROD, "prod-nat-router", uc, nat_count=1, nat_vm_count=12))

    out.append(_bucket(P_PROD, "acme-prod-assets", uc, "STANDARD", 2300))
    out.append(_bucket(P_PROD, "acme-prod-backups", uc, "NEARLINE", 8200))

    out.append(_res(P_PROD, "Cloud Run", "run.googleapis.com/Service",
                    "checkout-service", uc, None, "READY"))
    out.append(_res(P_PROD, "Cloud Run", "run.googleapis.com/Service",
                    "email-renderer", uc, None, "READY"))

    # acme-staging
    out.append(_instance(P_STAGE, "stage-web-1", ew, f"{ew}-b", "e2-medium"))
    out.append(_disk(P_STAGE, "stage-web-1-boot", ew, f"{ew}-b", 30, "pd-standard"))
    out.append(_instance(P_STAGE, "stage-api-1", ew, f"{ew}-b", "e2-small"))
    out.append(_disk(P_STAGE, "stage-api-1-boot", ew, f"{ew}-b", 30, "pd-standard"))
    out.append(_res(P_STAGE, "Cloud SQL", "sqladmin.googleapis.com/Instance",
                    "staging-db", ew, None, "RUNNABLE", None,
                    tier="db-f1-micro", engine="POSTGRES_16",
                    availability="ZONAL", disk_size_gb=20, disk_type="PD_HDD"))
    out.append(_bucket(P_STAGE, "acme-staging-assets", ew, "STANDARD", 140))
    out.append(_res(P_STAGE, "Compute Engine", "compute.googleapis.com/Address",
                    "staging-ip", ew, None, "RESERVED", None, address_type="EXTERNAL"))
    out.append(_router(P_STAGE, "stage-bgp-router", ew))
    out.append(_res(P_STAGE, "Cloud Functions", "cloudfunctions.googleapis.com/Function",
                    "nightly-report", ew, None, "ACTIVE"))

    # acme-data
    for i in range(1, _batch_worker_count(ts) + 1):
        out.append(_instance(P_DATA, f"batch-worker-{i}", ae, f"{ae}-a",
                             "c2-standard-16", spot=True, labels={"tier": "batch"}))
    out.append(_instance(P_DATA, "etl-orchestrator", ae, f"{ae}-a", "n2d-standard-4"))
    out.append(_disk(P_DATA, "etl-orchestrator-boot", ae, f"{ae}-a", 80, "pd-balanced"))
    if _ci_runner_up(ts):
        out.append(_instance(P_DATA, "ci-runner-ephemeral", ae, f"{ae}-b",
                             "e2-standard-8", labels={"tier": "ci"}))
    out.append(_router(P_DATA, "data-nat-router", ae, nat_count=1, nat_vm_count=64))
    out.append(_bucket(P_DATA, "acme-data-lake", ae, "ARCHIVE", 21000))
    out.append(_bucket(P_DATA, "acme-data-warehouse-export", ae, "STANDARD", 3500))
    out.append(_res(P_DATA, "Cloud Functions", "cloudfunctions.googleapis.com/Function",
                    "ingest-trigger", ae, None, "ACTIVE"))
    # no mapper for this type — shows up as "unpriced"
    out.append(_res(P_DATA, "BigQuery", "bigquery.googleapis.com/Dataset",
                    "analytics_events", ae, None, "ACTIVE"))

    return out
