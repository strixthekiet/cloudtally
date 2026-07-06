"""GCP resource -> cost components. One mapper serves live and demo
inventory; asset types without a handler come back UNPRICED so coverage
gaps stay visible."""
from __future__ import annotations

from cloudtally.models import (
    ESTIMATED,
    EXACT,
    UNPRICED,
    USAGE_BASED,
    CostComponent,
    Resource,
    ResourceCost,
    make_cost,
)
from cloudtally.pricing.catalog import PricingCatalog
from cloudtally.providers.gcp.machine_types import parse_machine_type


def _component(
    cat: PricingCatalog, region: str, key: str, description: str, quantity: float, unit: str
) -> CostComponent | None:
    rate = cat.hourly_rate(key, region)
    if rate is None:
        return None
    return CostComponent(
        key=key,
        description=description,
        quantity=quantity,
        unit=unit,
        unit_price=round(rate, 8),
        hourly=rate * quantity,
    )


class GCPMapper:
    def __init__(self, catalog: PricingCatalog):
        self.catalog = catalog

    def price(self, r: Resource) -> ResourceCost:
        handler = _HANDLERS.get(r.asset_type)
        if handler is None:
            return make_cost(r, [], UNPRICED, f"No pricing mapper for {r.asset_type} yet")
        return handler(self, r)

    def _instance(self, r: Resource) -> ResourceCost:
        if r.status not in ("RUNNING", ""):
            return make_cost(
                r, [], EXACT, f"Instance is {r.status}: no compute charges (disks bill separately)"
            )
        mt = str(r.attrs.get("machine_type", ""))
        shape = parse_machine_type(mt)
        if shape is None:
            return make_cost(r, [], UNPRICED, f"Unrecognized machine type '{mt}'")

        cat, region = self.catalog, r.region
        comps: list[CostComponent] = []
        if shape.flat_key:
            c = _component(cat, region, shape.flat_key, f"{mt} (shared-core)", 1, "instance")
            if c:
                comps.append(c)
        else:
            spot = bool(r.attrs.get("spot"))
            suffix = ".spot" if spot else ""
            tag = " · Spot" if spot else ""
            c1 = _component(
                cat, region, f"{shape.family}.cpu{suffix}",
                f"{shape.vcpu:g} vCPU ({shape.family.upper()}{tag})", shape.vcpu, "vCPU",
            )
            c2 = _component(
                cat, region, f"{shape.family}.ram{suffix}",
                f"{shape.ram_gib:g} GiB RAM ({shape.family.upper()}{tag})", shape.ram_gib, "GiB",
            )
            comps.extend(c for c in (c1, c2) if c)

        if not comps:
            return make_cost(r, [], UNPRICED, f"No SKUs for {mt} in {region}")
        note = ""
        confidence = EXACT
        if r.attrs.get("licenses"):
            note = "Premium OS license charges not included"
            confidence = ESTIMATED
        return make_cost(r, comps, confidence, note)

    def _disk(self, r: Resource) -> ResourceCost:
        size = float(r.attrs.get("size_gb", 0) or 0)
        disk_type = str(r.attrs.get("disk_type", "pd-standard"))
        c = _component(
            self.catalog, r.region, f"{disk_type}.capacity",
            f"{size:g} GiB {disk_type}", size, "GiB",
        )
        if c is None:
            return make_cost(r, [], UNPRICED, f"No SKU for disk type '{disk_type}'")
        return make_cost(r, [c], EXACT)

    def _address(self, r: Resource) -> ResourceCost:
        if r.attrs.get("address_type") == "INTERNAL":
            return make_cost(r, [], EXACT, "Internal IP: free")
        in_use = r.status == "IN_USE"
        key = "static-ip.attached" if in_use else "static-ip.unattached"
        label = "External IPv4 (in use)" if in_use else "External IPv4 (reserved, idle)"
        c = _component(self.catalog, r.region, key, label, 1, "address")
        if c is None:
            return make_cost(r, [], UNPRICED, "No SKU for external IP")
        return make_cost(r, [c], EXACT)

    def _forwarding_rule(self, r: Resource) -> ResourceCost:
        c = _component(self.catalog, r.region, "lb.forwarding-rule", "Forwarding rule", 1, "rule")
        if c is None:
            return make_cost(r, [], UNPRICED, "No SKU for forwarding rule")
        return make_cost(r, [c], ESTIMATED, "Data processing charges excluded (usage-based)")

    def _gke_cluster(self, r: Resource) -> ResourceCost:
        scope = "regional" if r.attrs.get("location_type") == "regional" else "zonal"
        c = _component(
            self.catalog, r.region, f"gke.cluster.{scope}",
            f"Cluster management fee ({scope})", 1, "cluster",
        )
        if c is None:
            return make_cost(r, [], UNPRICED, "No SKU for GKE cluster fee")
        return make_cost(r, [c], EXACT, "Node VMs bill as Compute Engine instances (listed separately)")

    def _bucket(self, r: Resource) -> ResourceCost:
        storage_class = str(r.attrs.get("storage_class", "STANDARD")).lower()
        size = r.attrs.get("size_gb")
        if size is None:
            return make_cost(
                r, [], USAGE_BASED,
                "Bucket size not in inventory; needs Cloud Monitoring metrics. Ops/egress always usage-based.",
            )
        c = _component(
            self.catalog, r.region, f"gcs.{storage_class}.capacity",
            f"{float(size):g} GiB {storage_class} storage", float(size), "GiB",
        )
        if c is None:
            return make_cost(r, [], UNPRICED, f"No SKU for storage class '{storage_class}'")
        return make_cost(r, [c], ESTIMATED, "At-rest capacity only; operations and egress excluded")

    def _cloudsql(self, r: Resource) -> ResourceCost:
        if r.status not in ("RUNNABLE", "RUNNING", ""):
            return make_cost(r, [], EXACT, f"Instance is {r.status}: storage may still bill")
        tier = str(r.attrs.get("tier", ""))
        engine_raw = str(r.attrs.get("engine", "POSTGRES")).upper()
        engine = "mysql" if "MYSQL" in engine_raw else "postgresql"
        scope = "regional" if r.attrs.get("availability") == "REGIONAL" else "zonal"
        cat, region = self.catalog, r.region
        comps: list[CostComponent] = []
        confidence = EXACT
        note = ""

        if tier in ("db-f1-micro", "db-g1-small"):
            # regional SKUs already include the HA standby in their rate
            c = _component(cat, region, f"cloudsql.shared.{tier}.{scope}",
                           f"{tier} (shared-core, {scope})", 1, "instance")
            if c:
                comps.append(c)
        elif tier.startswith("db-custom-"):
            parts = tier.split("-")
            vcpu, ram_gib = float(parts[2]), float(parts[3]) / 1024.0
            c1 = _component(cat, region, f"cloudsql.{engine}.{scope}.cpu", f"{vcpu:g} vCPU", vcpu, "vCPU")
            c2 = _component(cat, region, f"cloudsql.{engine}.{scope}.ram", f"{ram_gib:g} GiB RAM", ram_gib, "GiB")
            comps.extend(c for c in (c1, c2) if c)
        else:
            confidence = ESTIMATED
            note = f"Unrecognized tier '{tier}'; storage-only estimate"

        disk_gb = float(r.attrs.get("disk_size_gb", 0) or 0)
        if disk_gb:
            disk_kind = "storage-ssd" if r.attrs.get("disk_type", "PD_SSD") == "PD_SSD" else "storage-hdd"
            key = f"cloudsql.{disk_kind}.{scope}"
            if self.catalog.hourly_rate(key, region) is None:
                key = f"cloudsql.{disk_kind}.zonal"
            c = _component(cat, region, key, f"{disk_gb:g} GiB {disk_kind.split('-')[1].upper()} storage", disk_gb, "GiB")
            if c:
                comps.append(c)

        if not comps:
            return make_cost(r, [], UNPRICED, f"No Cloud SQL SKUs for tier '{tier}' in {region}")
        return make_cost(r, comps, confidence, note)

    def _redis(self, r: Resource) -> ResourceCost:
        mem = float(r.attrs.get("memory_gb", 0) or 0)
        tier = "standard_ha" if str(r.attrs.get("tier", "BASIC")).upper() == "STANDARD_HA" else "basic"
        c = _component(
            self.catalog, r.region, f"redis.{tier}.capacity",
            f"{mem:g} GiB Redis ({tier.replace('_', ' ')})", mem, "GiB",
        )
        if c is None:
            return make_cost(r, [], UNPRICED, "No SKU for Memorystore Redis")
        return make_cost(r, [c], EXACT)

    def _usage_based_serverless(self, r: Resource) -> ResourceCost:
        return make_cost(
            r, [], USAGE_BASED,
            "Billed per request / CPU-second; not derivable from inventory alone",
        )


_HANDLERS = {
    "compute.googleapis.com/Instance": GCPMapper._instance,
    "compute.googleapis.com/Disk": GCPMapper._disk,
    "compute.googleapis.com/Address": GCPMapper._address,
    "compute.googleapis.com/ForwardingRule": GCPMapper._forwarding_rule,
    "container.googleapis.com/Cluster": GCPMapper._gke_cluster,
    "storage.googleapis.com/Bucket": GCPMapper._bucket,
    "sqladmin.googleapis.com/Instance": GCPMapper._cloudsql,
    "redis.googleapis.com/Instance": GCPMapper._redis,
    "run.googleapis.com/Service": GCPMapper._usage_based_serverless,
    "cloudfunctions.googleapis.com/Function": GCPMapper._usage_based_serverless,
}
