"""cloudtally — cloud-custodian-style CLI.

  cloudtally run        one inventory + pricing pass, record history
  cloudtally watch      poll continuously
  cloudtally report     aggregates from the latest capture
  cloudtally resources  per-resource costs (with time travel via --at)
  cloudtally history    cost time series
  cloudtally validate   resolve and check the configuration
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from cloudtally import __version__
from cloudtally.config import Config
from cloudtally.engine import Engine, backfill_demo_history
from cloudtally.models import CONFIDENCE_ORDER
from cloudtally.providers.gcp.provider import GCPProvider
from cloudtally.store import Store

log = logging.getLogger("cloudtally")


# ---------------------------------------------------------------- helpers

def _load_config(args) -> Config:
    overrides: dict = {}
    if getattr(args, "mode", None):
        overrides["mode"] = args.mode
    if getattr(args, "projects", None):
        overrides["projects"] = args.projects
    if getattr(args, "currency", None):
        overrides["currency"] = args.currency
    if getattr(args, "include_unpriced", None):
        overrides["include_unpriced"] = True
    if getattr(args, "db", None):
        overrides["db_path"] = args.db
    if getattr(args, "interval", None):
        overrides["poll_seconds"] = args.interval
    return Config.load(args.config, overrides)


def _build(cfg: Config) -> tuple[GCPProvider, Store, Engine]:
    provider = GCPProvider(
        mode=cfg.mode,
        projects=cfg.gcp_projects,
        api_key=cfg.gcp_api_key,
        include_unpriced=cfg.include_unpriced,
        currency=cfg.currency,
    )
    store = Store(cfg.db_path)
    return provider, store, Engine(provider, store, cfg.catalog_refresh_seconds)


def _table(headers: list[str], rows: list[list], aligns: str = "") -> str:
    rows = [[str(c) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
              for i, h in enumerate(headers)]
    aligns = aligns or "l" * len(headers)

    def fmt(cells):
        return "  ".join(
            c.rjust(widths[i]) if aligns[i] == "r" else c.ljust(widths[i])
            for i, c in enumerate(cells)
        ).rstrip()

    return "\n".join([fmt(headers)] + [fmt(r) for r in rows])


def _emit(headers: list[str], rows: list[list], fmt: str, aligns: str = "") -> None:
    if fmt == "json":
        keys = [h.lower() for h in headers]
        print(json.dumps([dict(zip(keys, r)) for r in rows], indent=2))
    elif fmt == "csv":
        w = csv.writer(sys.stdout)
        w.writerow(headers)
        w.writerows(rows)
    else:
        print(_table(headers, rows, aligns))


def _parse_when(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        raise SystemExit(f"error: --at must be a unix timestamp or ISO time, got '{s}'")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _latest(store: Store, cfg: Config) -> dict:
    snap = store.latest_snapshot()
    if snap is None:
        raise SystemExit(
            f"error: no data in {cfg.db_path} — run `cloudtally run` first")
    return snap


# ---------------------------------------------------------------- commands

def cmd_run(args) -> int:
    cfg = _load_config(args)
    provider, store, engine = _build(cfg)
    if cfg.mode == "demo":
        backfill_demo_history(provider, store)
    summary = engine.poll_once(force_detail=True)

    if args.output_dir:
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(json.dumps(summary, indent=2))
        resources = store.resources()
        (out / "resources.json").write_text(json.dumps(resources, indent=2))
        log.info("wrote %s and %s", out / "summary.json", out / "resources.json")

    if args.format == "json":
        print(json.dumps(summary, indent=2))
        return 0

    cc = summary["confidence_counts"]
    print(f"mode={cfg.mode} currency={cfg.currency} "
          f"resources={summary['resource_count']} priced={summary['priced_count']} "
          f"({' '.join(f'{k}={cc[k]}' for k in CONFIDENCE_ORDER)})")
    if provider.project_errors:
        for p, e in provider.project_errors.items():
            print(f"warning: project {p} skipped: {e}", file=sys.stderr)
    print(f"total: {summary['total_hourly']:,.4f} {cfg.currency}/hr"
          f"  ≈ {summary['total_monthly']:,.2f} {cfg.currency}/mo\n")
    rows = [[s["name"], f"{s['hourly']:,.4f}", f"{s['monthly']:,.2f}", s["count"]]
            for s in summary["by_service"]]
    print(_table(["SERVICE", "HOURLY", "MONTHLY", "COUNT"], rows, "lrrr"))
    return 0


def cmd_watch(args) -> int:
    cfg = _load_config(args)
    provider, store, engine = _build(cfg)
    if cfg.mode == "demo":
        backfill_demo_history(provider, store)
    print(f"watching: mode={cfg.mode} interval={cfg.poll_seconds:g}s "
          f"db={cfg.db_path} (Ctrl+C to stop)", file=sys.stderr)
    while True:
        try:
            s = engine.poll_once()
            print(f"{_iso(s['last_sync'])}  total={s['total_hourly']:,.4f} "
                  f"{cfg.currency}/hr  resources={s['resource_count']} "
                  f"priced={s['priced_count']}")
        except Exception as exc:
            store.record_error(str(exc))
            log.error("poll failed: %s", exc)
        try:
            time.sleep(cfg.poll_seconds)
        except KeyboardInterrupt:
            return 0


def cmd_report(args) -> int:
    cfg = _load_config(args)
    store = Store(cfg.db_path)
    snap = _latest(store, cfg)

    key = {"service": "service", "project": "project",
           "region": "region", "confidence": "confidence"}[args.by]
    agg: dict[str, dict] = {}
    for r in snap["resources"]:
        slot = agg.setdefault(r[key], {"hourly": 0.0, "monthly": 0.0, "count": 0})
        slot["hourly"] += r["hourly"]
        slot["monthly"] += r["monthly"]
        slot["count"] += 1

    print(f"as of {_iso(snap['ts'])}  "
          f"total={snap['total_hourly']:,.4f} {cfg.currency}/hr", file=sys.stderr)
    rows = [[name, f"{v['hourly']:,.4f}", f"{v['monthly']:,.2f}", v["count"]]
            for name, v in sorted(agg.items(), key=lambda kv: -kv[1]["hourly"])]
    _emit([args.by.upper(), "HOURLY", "MONTHLY", "COUNT"], rows, args.format, "lrrr")
    return 0


def cmd_resources(args) -> int:
    cfg = _load_config(args)
    store = Store(cfg.db_path)
    if args.at:
        ts = _parse_when(args.at)
        snap = store.snapshot_at(ts)
        if snap is None:
            raise SystemExit(f"error: no capture within 30 minutes of {args.at}")
        print(f"pinned to capture at {_iso(snap['ts'])}", file=sys.stderr)
    else:
        snap = _latest(store, cfg)

    needle = (args.query or "").lower()
    out = []
    for r in snap["resources"]:
        if args.service and r["service"] != args.service:
            continue
        if args.confidence and r["confidence"] != args.confidence:
            continue
        if needle and needle not in (
                f"{r['name']} {r['project']} {r['asset_type']} {r['region']}".lower()):
            continue
        out.append(r)
    if args.limit:
        out = out[:args.limit]

    if args.format == "json":
        print(json.dumps(out, indent=2))
        return 0
    rows = [[r["name"], r["service"], r["project"], r["region"],
             f"{r['hourly']:,.4f}", f"{r['monthly']:,.2f}", r["confidence"]]
            for r in out]
    _emit(["NAME", "SERVICE", "PROJECT", "REGION", "HOURLY", "MONTHLY", "CONFIDENCE"],
          rows, args.format, "llllrrl")
    return 0


def cmd_history(args) -> int:
    cfg = _load_config(args)
    store = Store(cfg.db_path)
    points = store.history(hours=args.hours)
    if not points:
        raise SystemExit(
            f"error: no data in {cfg.db_path} — run `cloudtally run` first")
    if args.format == "json":
        print(json.dumps(points, indent=2))
        return 0
    rows = [[_iso(p["ts"]), f"{p['total_hourly']:,.4f}"] for p in points]
    _emit(["TIME", f"HOURLY ({cfg.currency})"], rows, args.format, "lr")
    return 0


def cmd_validate(args) -> int:
    if args.config and not Path(args.config).exists():
        raise SystemExit(f"error: config file not found: {args.config}")
    cfg = _load_config(args)
    print(f"config file:  {cfg.config_file or '(none — env/defaults only)'}")
    print(f"mode:         {cfg.mode}")
    print(f"currency:     {cfg.currency}")
    print(f"projects:     {', '.join(cfg.gcp_projects) or '(auto-discover)'}")
    print(f"include_unpriced: {cfg.include_unpriced}")
    print(f"poll_seconds: {cfg.poll_seconds:g}")
    print(f"db:           {cfg.db_path}")
    print("ok")
    return 0


# ---------------------------------------------------------------- parser

def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("-c", "--config", metavar="FILE",
                   help="config file (default: ./config.yaml or $CLOUDTALLY_CONFIG)")
    p.add_argument("--db", metavar="PATH", help="SQLite history db path")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")


def _add_poll_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mode", choices=["demo", "live"], help="override mode")
    p.add_argument("--project", dest="projects", action="append", metavar="ID",
                   help="pin a GCP project (repeatable; default: auto-discover)")
    p.add_argument("--currency", metavar="CUR", help="pricing currency (e.g. USD, EUR)")
    p.add_argument("--include-unpriced", action="store_true", default=None,
                   help="inventory all asset types, not just priced ones")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cloudtally",
        description="Real-time cloud cost from live inventory × rate catalog.")
    parser.add_argument("--version", action="version",
                        version=f"cloudtally {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="one inventory + pricing pass")
    _add_common(p)
    _add_poll_flags(p)
    p.add_argument("-s", "--output-dir", metavar="DIR",
                   help="write summary.json and resources.json here")
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("watch", help="poll continuously (Ctrl+C to stop)")
    _add_common(p)
    _add_poll_flags(p)
    p.add_argument("--interval", type=float, metavar="SECONDS",
                   help="poll interval (default: config poll_seconds)")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("report", help="aggregates from the latest capture")
    _add_common(p)
    p.add_argument("--by", choices=["service", "project", "region", "confidence"],
                   default="service")
    p.add_argument("--format", choices=["table", "json", "csv"], default="table")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("resources", help="per-resource costs")
    _add_common(p)
    p.add_argument("--service", help="filter by service name")
    p.add_argument("--confidence", choices=CONFIDENCE_ORDER)
    p.add_argument("-q", "--query", help="substring match on name/project/type/region")
    p.add_argument("--at", metavar="TIME",
                   help="time travel: unix timestamp or ISO time of a stored capture")
    p.add_argument("--limit", type=int, metavar="N")
    p.add_argument("--format", choices=["table", "json", "csv"], default="table")
    p.set_defaults(func=cmd_resources)

    p = sub.add_parser("history", help="cost time series")
    _add_common(p)
    p.add_argument("--hours", type=float, default=24.0)
    p.add_argument("--format", choices=["table", "json", "csv"], default="table")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("validate", help="resolve and check the configuration")
    _add_common(p)
    _add_poll_flags(p)
    p.set_defaults(func=cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else (
            logging.INFO if args.command in ("run", "watch") else logging.WARNING),
        stream=sys.stderr,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except (ValueError, RuntimeError) as exc:
        if args.verbose:
            raise
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
