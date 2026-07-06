from __future__ import annotations

from abc import ABC, abstractmethod

from cloudtally.models import Resource, ResourceCost


class CloudProvider(ABC):
    name: str = ""
    display_name: str = ""

    @abstractmethod
    def build_catalog(self) -> None:
        """Fetch or refresh the pricing catalog."""

    @abstractmethod
    def fetch_resources(self, ts: float) -> list[Resource]:
        """Inventory snapshot; ``ts`` only matters in demo mode."""

    @abstractmethod
    def price(self, resource: Resource) -> ResourceCost:
        """Map one resource to cost components using the current catalog."""

    @property
    @abstractmethod
    def catalog_info(self) -> dict:
        """Metadata about the loaded catalog."""
