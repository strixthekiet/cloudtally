from __future__ import annotations

import logging
import subprocess
import time

from cloudtally.models import Resource, ResourceCost
from cloudtally.pricing.catalog import PricingCatalog, seed_gcp_catalog
from cloudtally.providers.base import CloudProvider
from cloudtally.providers.gcp.mapper import GCPMapper

log = logging.getLogger("cloudtally.provider.gcp")

CRM_BASE = "https://cloudresourcemanager.googleapis.com/v1"
DISCOVERY_TTL = 3600.0


class GCPProvider(CloudProvider):
    name = "gcp"
    display_name = "Google Cloud"

    def __init__(self, mode: str, projects: list[str], api_key: str | None = None,
                 include_unpriced: bool = False, currency: str = "USD"):
        self.mode = mode
        self.configured_projects = projects  # optional pin; empty = auto-discover
        self.api_key = api_key
        self.include_unpriced = include_unpriced
        self.currency = currency.upper()
        self.catalog: PricingCatalog = seed_gcp_catalog(self.currency)
        self.mapper = GCPMapper(self.catalog)
        self.project_errors: dict[str, str] = {}
        self._session = None
        self._discovered: list[str] = []
        self._discovered_at = 0.0

    @property
    def projects(self) -> list[str]:
        return self.configured_projects or self._discovered

    def _live_session(self):
        if self._session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            self._session = AuthorizedSession(creds)
        return self._session

    def build_catalog(self) -> None:
        self.catalog_fallback = False  # poller retries sooner when True
        if self.mode != "live":
            self.catalog = seed_gcp_catalog(self.currency)
            self.mapper = GCPMapper(self.catalog)
            return
        try:
            from cloudtally.pricing.gcp_live import fetch_gcp_catalog_entries

            if self.api_key:
                import requests

                entries = fetch_gcp_catalog_entries(
                    requests.Session(), api_key=self.api_key, currency=self.currency)
            else:
                entries = fetch_gcp_catalog_entries(
                    self._live_session(), currency=self.currency)
            catalog = PricingCatalog(entries, source="gcp-catalog-api", currency=self.currency)
            backfilled = catalog.merge_missing_from(seed_gcp_catalog(self.currency))
            log.info("live catalog: %d entries (%d backfilled from seed)", len(catalog), backfilled)
            self.catalog = catalog
        except Exception:
            log.exception("live catalog fetch failed; falling back to seed snapshot")
            self.catalog = seed_gcp_catalog(self.currency)
            self.catalog_fallback = True
        self.mapper = GCPMapper(self.catalog)

    def _discover_projects(self) -> list[str]:
        """ACTIVE projects visible to the credentials: Cloud Resource Manager
        API first, `gcloud projects list` as fallback."""
        try:
            session = self._live_session()
            out: list[str] = []
            token = None
            while True:
                params: dict = {"pageSize": 100}
                if token:
                    params["pageToken"] = token
                resp = session.get(f"{CRM_BASE}/projects", params=params, timeout=60)
                resp.raise_for_status()
                page = resp.json()
                out.extend(
                    p["projectId"] for p in page.get("projects", [])
                    if p.get("lifecycleState") == "ACTIVE"
                )
                token = page.get("nextPageToken")
                if not token:
                    break
            if out:
                return out
        except Exception:
            log.exception("project discovery via Resource Manager API failed; trying gcloud")

        try:
            r = subprocess.run(
                ["gcloud", "projects", "list", "--format=value(projectId)"],
                capture_output=True, text=True, timeout=60,
            )
            projects = [l.strip() for l in r.stdout.splitlines() if l.strip()]
            if r.returncode == 0 and projects:
                return projects
        except Exception:
            log.exception("gcloud fallback failed")

        raise RuntimeError(
            "could not discover GCP projects — check ADC credentials, "
            "or pin gcp.projects in config.yaml"
        )

    def _effective_projects(self) -> list[str]:
        if self.configured_projects:
            return self.configured_projects
        if not self._discovered or time.time() - self._discovered_at > DISCOVERY_TTL:
            self._discovered = self._discover_projects()
            self._discovered_at = time.time()
            log.info("auto-discovered %d projects: %s",
                     len(self._discovered), ", ".join(self._discovered))
        return self._discovered

    def fetch_resources(self, ts: float) -> list[Resource]:
        if self.mode != "live":
            from cloudtally.providers.gcp.demo_fleet import generate

            return generate(ts)

        from cloudtally.providers.gcp.live_inventory import fetch_project_resources

        session = self._live_session()
        projects = self._effective_projects()
        out: list[Resource] = []
        errors: dict[str, str] = {}
        for project in projects:
            try:
                out.extend(fetch_project_resources(session, project, self.include_unpriced))
            except Exception as exc:  # one bad project must not kill the poll
                errors[project] = str(exc)[:200]
                log.warning("inventory failed for %s: %s", project, exc)
        self.project_errors = errors
        if errors and not out:
            raise RuntimeError(
                f"inventory failed for all {len(projects)} projects; "
                f"first error: {next(iter(errors.values()))}"
            )
        return out

    def price(self, resource: Resource) -> ResourceCost:
        return self.mapper.price(resource)

    @property
    def catalog_info(self) -> dict:
        return {
            "source": self.catalog.source,
            "entries": len(self.catalog),
            "currency": self.catalog.currency,
            "captured_at": self.catalog.captured_at,
            "age_seconds": round(time.time() - self.catalog.captured_at),
        }
