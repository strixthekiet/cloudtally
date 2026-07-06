from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

# Pricing confidence levels.
EXACT = "exact"              # every component priced from the catalog
ESTIMATED = "estimated"      # priced, but with stated assumptions
USAGE_BASED = "usage_based"  # cost depends on usage invisible to inventory
UNPRICED = "unpriced"        # no pricing mapper for this asset type

CONFIDENCE_ORDER = [EXACT, ESTIMATED, USAGE_BASED, UNPRICED]

HOURS_PER_MONTH = 730.0


@dataclass
class Resource:
    """Normalized cloud resource. ``attrs`` holds the provider-specific fields
    the pricing mapper needs, shaped identically in live and demo mode."""

    id: str
    name: str
    provider: str
    service: str
    asset_type: str
    project: str
    region: str
    zone: str | None = None
    status: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class CostComponent:
    key: str
    description: str
    quantity: float
    unit: str
    unit_price: float  # per unit per hour
    hourly: float


@dataclass
class ResourceCost:
    resource: Resource
    components: list[CostComponent]
    hourly: float
    monthly: float
    confidence: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        r = self.resource
        return {
            "id": r.id,
            "name": r.name,
            "provider": r.provider,
            "service": r.service,
            "asset_type": r.asset_type,
            "project": r.project,
            "region": r.region,
            "zone": r.zone,
            "status": r.status,
            "labels": r.labels,
            "hourly": round(self.hourly, 6),
            "monthly": round(self.monthly, 2),
            "confidence": self.confidence,
            "note": self.note,
            "components": [asdict(c) for c in self.components],
        }


def make_cost(
    resource: Resource,
    components: list[CostComponent],
    confidence: str,
    note: str = "",
) -> ResourceCost:
    hourly = sum(c.hourly for c in components)
    return ResourceCost(
        resource=resource,
        components=components,
        hourly=hourly,
        monthly=hourly * HOURS_PER_MONTH,
        confidence=confidence,
        note=note,
    )
