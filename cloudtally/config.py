"""Configuration. Precedence: CLI flags > CLOUDTALLY_* env vars > config.yaml > defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_TRUTHY = ("1", "true", "yes", "on")


def _read_config_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            f"{path} exists but PyYAML is not installed — pip install pyyaml"
        ) from exc
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data


def _pick(env_key: str, yaml_value, default):
    env = os.environ.get(env_key)
    if env is not None and env != "":
        return env
    if yaml_value is not None:
        return yaml_value
    return default


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in _TRUTHY


def _as_projects(v) -> list[str]:
    if isinstance(v, (list, tuple)):
        return [str(p).strip() for p in v if str(p).strip()]
    return [p.strip() for p in str(v).split(",") if p.strip()]


@dataclass
class Config:
    mode: str = "demo"
    gcp_projects: list[str] = field(default_factory=list)
    gcp_api_key: str | None = None
    include_unpriced: bool = False
    currency: str = "USD"
    poll_seconds: float = 15.0
    catalog_refresh_seconds: float = 86400.0
    db_path: str = ""
    config_file: str = ""

    @classmethod
    def load(cls, config_path: str | None = None,
             overrides: dict | None = None) -> "Config":
        """``overrides`` are already-typed values from CLI flags; a key that is
        present (even falsy) wins over env/yaml/defaults."""
        ov = overrides or {}
        cfg_path = Path(
            config_path
            or os.environ.get("CLOUDTALLY_CONFIG", "")
            or Path.cwd() / "config.yaml"
        )
        y = _read_config_file(cfg_path)
        gcp = y.get("gcp") or {}

        mode = str(ov.get("mode") or _pick("CLOUDTALLY_MODE", y.get("mode"), "demo")).lower()
        if mode not in ("demo", "live"):
            raise ValueError(f"mode must be demo|live, got '{mode}'")

        # empty in live mode = auto-discover all visible projects
        if "projects" in ov:
            projects = _as_projects(ov["projects"])
        else:
            projects = _as_projects(_pick("CLOUDTALLY_GCP_PROJECTS", gcp.get("projects"), []))

        default_poll = 15.0 if mode == "demo" else 300.0
        api_key = _pick("CLOUDTALLY_GCP_API_KEY", gcp.get("api_key"), None)

        from cloudtally.pricing.fx import SUPPORTED_CURRENCIES

        currency = str(
            ov.get("currency") or _pick("CLOUDTALLY_CURRENCY", y.get("currency"), "USD")
        ).upper()
        if currency not in SUPPORTED_CURRENCIES:
            raise ValueError(
                f"currency '{currency}' not supported; one of {', '.join(SUPPORTED_CURRENCIES)}"
            )

        include_unpriced = (
            bool(ov["include_unpriced"]) if "include_unpriced" in ov
            else _as_bool(_pick("CLOUDTALLY_INCLUDE_UNPRICED", gcp.get("include_unpriced"), False))
        )
        poll_seconds = float(
            ov.get("poll_seconds")
            or _pick("CLOUDTALLY_POLL_SECONDS", y.get("poll_seconds"), default_poll)
        )
        db_path = str(
            ov.get("db_path")
            or _pick("CLOUDTALLY_DB", y.get("db_path"),
                     str(Path.cwd() / "data" / f"history-{mode}.db"))
        )

        return cls(
            mode=mode,
            gcp_projects=projects,
            gcp_api_key=str(api_key) if api_key else None,
            currency=currency,
            include_unpriced=include_unpriced,
            poll_seconds=poll_seconds,
            catalog_refresh_seconds=float(
                _pick("CLOUDTALLY_CATALOG_REFRESH_SECONDS", y.get("catalog_refresh_seconds"), 86400)
            ),
            db_path=db_path,
            config_file=str(cfg_path) if y else "",
        )
