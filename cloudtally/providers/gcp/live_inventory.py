"""Live GCP inventory via the Cloud Asset Inventory API, normalized into
``Resource`` objects with the same attrs shapes demo_fleet produces."""
from __future__ import annotations

import logging

from cloudtally.models import Resource

log = logging.getLogger("cloudtally.inventory.gcp")

ASSET_BASE = "https://cloudasset.googleapis.com/v1"

# requested explicitly to keep pulls small
PRICED_ASSET_TYPES = [
    "compute.googleapis.com/Instance",
    "compute.googleapis.com/Disk",
    "compute.googleapis.com/Address",
    "compute.googleapis.com/ForwardingRule",
    "container.googleapis.com/Cluster",
    "storage.googleapis.com/Bucket",
    "sqladmin.googleapis.com/Instance",
    "redis.googleapis.com/Instance",
    "run.googleapis.com/Service",
    "cloudfunctions.googleapis.com/Function",
]

SERVICE_NAMES = {
    "compute.googleapis.com": "Compute Engine",
    "container.googleapis.com": "Kubernetes Engine",
    "storage.googleapis.com": "Cloud Storage",
    "sqladmin.googleapis.com": "Cloud SQL",
    "redis.googleapis.com": "Memorystore for Redis",
    "run.googleapis.com": "Cloud Run",
    "cloudfunctions.googleapis.com": "Cloud Functions",
    "bigquery.googleapis.com": "BigQuery",
}

_FREE_LICENSE_HINTS = ("debian", "ubuntu", "cos-", "rocky", "almalinux", "fedora")


def _last(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def _zone_region(zone: str) -> str:
    return zone.rsplit("-", 1)[0] if zone else "global"


def _premium_licenses(data: dict) -> bool:
    for disk in data.get("disks", []):
        for lic in disk.get("licenses", []):
            name = _last(lic).lower()
            if not any(h in name for h in _FREE_LICENSE_HINTS):
                return True
    return False


def _normalize(asset: dict, project: str) -> Resource | None:
    asset_type = asset.get("assetType", "")
    data = (asset.get("resource") or {}).get("data") or {}
    domain = asset_type.split("/")[0]
    service = SERVICE_NAMES.get(domain, domain.split(".")[0].title())
    full_name = asset.get("name", "")
    name = data.get("name") or _last(full_name)

    raw_status = data.get("status") or data.get("state") or ""
    # some services (Cloud Run) put an object here, not a string
    status = raw_status if isinstance(raw_status, str) else ""
    region, zone = "global", None
    attrs: dict = {}

    if asset_type == "compute.googleapis.com/Instance":
        zone = _last(data.get("zone", ""))
        region = _zone_region(zone)
        sched = data.get("scheduling", {})
        attrs = {
            "machine_type": _last(data.get("machineType", "")),
            "spot": sched.get("provisioningModel") == "SPOT" or bool(sched.get("preemptible")),
            "licenses": _premium_licenses(data),
        }
    elif asset_type == "compute.googleapis.com/Disk":
        zone = _last(data.get("zone", "")) or None
        region = _zone_region(zone) if zone else _last(data.get("region", "")) or "global"
        attrs = {"size_gb": float(data.get("sizeGb", 0) or 0),
                 "disk_type": _last(data.get("type", "pd-standard"))}
    elif asset_type == "compute.googleapis.com/Address":
        region = _last(data.get("region", "")) or "global"
        attrs = {"address_type": data.get("addressType", "EXTERNAL")}
    elif asset_type == "compute.googleapis.com/ForwardingRule":
        region = _last(data.get("region", "")) or "global"
    elif asset_type == "container.googleapis.com/Cluster":
        location = data.get("location", "")
        is_regional = location.count("-") == 1  # "us-central1" vs "us-central1-a"
        region = location if is_regional else _zone_region(location)
        attrs = {"location_type": "regional" if is_regional else "zonal"}
    elif asset_type == "storage.googleapis.com/Bucket":
        region = str(data.get("location", "global")).lower()
        # size is not in inventory; the mapper flags buckets usage_based
        attrs = {"storage_class": data.get("storageClass", "STANDARD"), "size_gb": None}
    elif asset_type == "sqladmin.googleapis.com/Instance":
        settings = data.get("settings", {})
        region = data.get("region", "global")
        attrs = {
            "tier": settings.get("tier", ""),
            "engine": data.get("databaseVersion", "POSTGRES"),
            "availability": settings.get("availabilityType", "ZONAL"),
            "disk_size_gb": float(settings.get("dataDiskSizeGb", 0) or 0),
            "disk_type": settings.get("dataDiskType", "PD_SSD"),
        }
    elif asset_type == "redis.googleapis.com/Instance":
        parts = full_name.split("/")
        region = parts[parts.index("locations") + 1] if "locations" in parts else "global"
        attrs = {"memory_gb": float(data.get("memorySizeGb", 0) or 0),
                 "tier": data.get("tier", "BASIC")}
    elif asset_type in ("run.googleapis.com/Service", "cloudfunctions.googleapis.com/Function"):
        parts = full_name.split("/")
        region = parts[parts.index("locations") + 1] if "locations" in parts else "global"

    return Resource(
        id=full_name or f"gcp/{project}/{asset_type}/{name}",
        name=name,
        provider="gcp",
        service=service,
        asset_type=asset_type,
        project=project,
        region=region,
        zone=zone,
        status=status,
        labels=data.get("labels", {}) or {},
        attrs=attrs,
    )


def fetch_project_resources(session, project: str, include_unpriced: bool = False) -> list[Resource]:
    out: list[Resource] = []
    token = None
    while True:
        # assetTypes is a repeated query param — pass a list so requests
        # encodes assetTypes=a&assetTypes=b (comma-joining reads as one type)
        params: dict = {"contentType": "RESOURCE", "pageSize": 1000}
        if not include_unpriced:
            params["assetTypes"] = PRICED_ASSET_TYPES
        if token:
            params["pageToken"] = token
        # x-goog-user-project: user-credential ADC needs a quota project
        resp = session.get(
            f"{ASSET_BASE}/projects/{project}/assets",
            params=params,
            headers={"x-goog-user-project": project},
            timeout=120,
        )
        if resp.status_code != 200:
            try:
                msg = resp.json()["error"]["message"]
            except Exception:
                msg = resp.text[:200]
            raise RuntimeError(f"Asset API {resp.status_code} for {project}: {msg}")
        page = resp.json()
        for asset in page.get("assets", []):
            r = _normalize(asset, project)
            if r is not None:
                out.append(r)
        token = page.get("nextPageToken")
        if not token:
            break
    log.info("inventory: %s -> %d resources", project, len(out))
    return out
