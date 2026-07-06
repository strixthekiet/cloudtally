"""Offline smoke tests — demo mode needs no credentials or network."""
from __future__ import annotations

import json

import pytest

from cloudtally import __version__
from cloudtally.cli import _build, main
from cloudtally.config import Config
from cloudtally.models import CONFIDENCE_ORDER

MISSING_CONFIG = "/nonexistent/config.yaml"


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    """One demo inventory+pricing pass recorded to a throwaway SQLite db."""
    db = tmp_path_factory.mktemp("data") / "history.db"
    cfg = Config(mode="demo", db_path=str(db))
    _provider, _store, engine = _build(cfg)
    engine.poll_once(force_detail=True)
    return str(db)


def test_default_config():
    cfg = Config.load(MISSING_CONFIG)
    assert cfg.mode == "demo"
    assert cfg.currency == "USD"
    assert cfg.poll_seconds == 15.0
    assert cfg.gcp_projects == []


def test_config_rejects_bad_mode():
    with pytest.raises(ValueError):
        Config.load(MISSING_CONFIG, {"mode": "prod"})


def test_config_rejects_bad_currency():
    with pytest.raises(ValueError):
        Config.load(MISSING_CONFIG, {"currency": "XYZ"})


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_demo_poll_prices_fleet(tmp_path):
    cfg = Config(mode="demo", db_path=str(tmp_path / "h.db"))
    _provider, store, engine = _build(cfg)
    summary = engine.poll_once(force_detail=True)
    assert summary["resource_count"] > 0
    assert summary["priced_count"] > 0
    assert summary["total_hourly"] > 0
    assert set(summary["confidence_counts"]) == set(CONFIDENCE_ORDER)
    snap = store.latest_snapshot()
    assert snap is not None
    assert snap["resources"], "force_detail must store per-resource costs"


def test_cli_report(demo_db, capsys):
    rc = main(["report", "-c", MISSING_CONFIG, "--db", demo_db,
               "--by", "project", "--format", "json"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows and all("hourly" in r for r in rows)


def test_cli_resources_filter(demo_db, capsys):
    rc = main(["resources", "-c", MISSING_CONFIG, "--db", demo_db,
               "--confidence", "exact", "--format", "json"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows and all(r["confidence"] == "exact" for r in rows)


def test_cli_history(demo_db, capsys):
    rc = main(["history", "-c", MISSING_CONFIG, "--db", demo_db,
               "--format", "json"])
    assert rc == 0
    points = json.loads(capsys.readouterr().out)
    assert points and all("total_hourly" in p for p in points)


def test_cli_validate(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = main(["validate"])
    assert rc == 0
    assert "ok" in capsys.readouterr().out
