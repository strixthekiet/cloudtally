"""Current fleet state in memory, cost history in SQLite."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from cloudtally.models import CONFIDENCE_ORDER, HOURS_PER_MONTH, ResourceCost

RESOURCE_SNAP_MIN_INTERVAL = 300.0
RESOURCE_SNAP_RETENTION_DAYS = 30


class Store:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS snapshots (
                 ts INTEGER PRIMARY KEY,
                 total_hourly REAL NOT NULL,
                 by_service TEXT NOT NULL,
                 resource_count INTEGER NOT NULL,
                 priced_count INTEGER NOT NULL,
                 breakdown TEXT
               )"""
        )
        # migrate pre-breakdown databases in place
        cols = {row[1] for row in self._db.execute("PRAGMA table_info(snapshots)")}
        if "breakdown" not in cols:
            self._db.execute("ALTER TABLE snapshots ADD COLUMN breakdown TEXT")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS resource_snapshots (
                 ts INTEGER NOT NULL,
                 id TEXT NOT NULL,
                 data TEXT NOT NULL,
                 PRIMARY KEY (ts, id)
               )"""
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_resource_snapshots_ts ON resource_snapshots(ts)"
        )
        self._db.commit()
        self._lock = threading.Lock()
        self.costs: list[ResourceCost] = []
        self.last_sync: float | None = None
        self.last_error: str | None = None
        row = self._db.execute("SELECT MAX(ts) FROM resource_snapshots").fetchone()
        self._last_resource_snap = float(row[0] or 0)
        self._last_purge = 0.0

    def update(self, costs: list[ResourceCost], ts: float,
               force_detail: bool = False) -> dict:
        costs = sorted(costs, key=lambda c: -c.monthly)
        with self._lock:
            self.costs = costs
            self.last_sync = ts
            self.last_error = None
        summary = self.summary()
        self._add_snapshot(ts, summary)
        self._add_resource_snapshot(ts, costs, force=force_detail)
        return summary

    def record_error(self, message: str) -> None:
        with self._lock:
            self.last_error = message

    def summary(self) -> dict:
        with self._lock:
            costs = list(self.costs)
            last_sync = self.last_sync
            last_error = self.last_error

        def group(key_fn) -> list[dict]:
            agg: dict[str, dict] = {}
            for c in costs:
                k = key_fn(c)
                slot = agg.setdefault(k, {"name": k, "hourly": 0.0, "count": 0})
                slot["hourly"] += c.hourly
                slot["count"] += 1
            out = sorted(agg.values(), key=lambda s: -s["hourly"])
            for s in out:
                s["hourly"] = round(s["hourly"], 4)
                s["monthly"] = round(s["hourly"] * HOURS_PER_MONTH, 2)
            return out

        # service x project split, so the UI can scope the time series
        # by any filter combination
        breakdown: dict[str, dict[str, float]] = {}
        for c in costs:
            svc = breakdown.setdefault(c.resource.service, {})
            svc[c.resource.project] = round(svc.get(c.resource.project, 0.0) + c.hourly, 6)

        total_hourly = sum(c.hourly for c in costs)
        confidence_counts = {k: 0 for k in CONFIDENCE_ORDER}
        for c in costs:
            confidence_counts[c.confidence] = confidence_counts.get(c.confidence, 0) + 1
        priced = confidence_counts["exact"] + confidence_counts["estimated"]

        return {
            "generated_at": time.time(),
            "last_sync": last_sync,
            "last_error": last_error,
            "total_hourly": round(total_hourly, 4),
            "total_monthly": round(total_hourly * HOURS_PER_MONTH, 2),
            "resource_count": len(costs),
            "priced_count": priced,
            "confidence_counts": confidence_counts,
            "by_service": group(lambda c: c.resource.service),
            "by_project": group(lambda c: c.resource.project),
            "by_region": group(lambda c: c.resource.region),
            "breakdown": breakdown,
        }

    def resources(self, service: str | None = None, confidence: str | None = None,
                  q: str | None = None) -> list[dict]:
        with self._lock:
            costs = list(self.costs)
        out = []
        needle = (q or "").lower()
        for c in costs:
            r = c.resource
            if service and r.service != service:
                continue
            if confidence and c.confidence != confidence:
                continue
            if needle and needle not in f"{r.name} {r.project} {r.asset_type} {r.region}".lower():
                continue
            out.append(c.to_dict())
        return out

    def _add_snapshot(self, ts: float, summary: dict) -> None:
        by_service = {s["name"]: round(s["hourly"], 4) for s in summary["by_service"]}
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?)",
                (int(ts), summary["total_hourly"], json.dumps(by_service),
                 summary["resource_count"], summary["priced_count"],
                 json.dumps(summary["breakdown"])),
            )
            self._db.commit()

    def _add_resource_snapshot(self, ts: float, costs: list[ResourceCost],
                               force: bool = False) -> None:
        if not force and ts - self._last_resource_snap < RESOURCE_SNAP_MIN_INTERVAL - 1:
            return
        rows = [(int(ts), c.resource.id, json.dumps(c.to_dict())) for c in costs]
        with self._lock:
            self._db.executemany(
                "INSERT OR REPLACE INTO resource_snapshots VALUES (?,?,?)", rows)
            if ts - self._last_purge > 3600:
                cutoff = int(time.time() - RESOURCE_SNAP_RETENTION_DAYS * 86400)
                self._db.execute("DELETE FROM resource_snapshots WHERE ts < ?", (cutoff,))
                self._last_purge = ts
            self._db.commit()
        self._last_resource_snap = ts

    def snapshot_at(self, ts: float, tolerance: float = 1800.0) -> dict | None:
        """Per-resource costs at the stored capture nearest to ``ts``."""
        with self._lock:
            below = self._db.execute(
                "SELECT MAX(ts) FROM resource_snapshots WHERE ts <= ?", (int(ts),)
            ).fetchone()[0]
            above = self._db.execute(
                "SELECT MIN(ts) FROM resource_snapshots WHERE ts >= ?", (int(ts),)
            ).fetchone()[0]
        candidates = [t for t in (below, above) if t is not None]
        if not candidates:
            return None
        chosen = min(candidates, key=lambda t: abs(t - ts))
        if abs(chosen - ts) > tolerance:
            return None
        with self._lock:
            rows = self._db.execute(
                "SELECT data FROM resource_snapshots WHERE ts = ?", (chosen,)
            ).fetchall()
        resources = [json.loads(d) for (d,) in rows]
        resources.sort(key=lambda r: -r["monthly"])
        return {
            "ts": chosen,
            "total_hourly": round(sum(r["hourly"] for r in resources), 4),
            "resources": resources,
        }

    def latest_snapshot(self) -> dict | None:
        """Per-resource costs from the most recent stored capture."""
        with self._lock:
            row = self._db.execute("SELECT MAX(ts) FROM resource_snapshots").fetchone()
        if row[0] is None:
            return None
        return self.snapshot_at(float(row[0]), tolerance=float("inf"))

    def history(self, hours: float = 24.0, max_points: int = 700) -> list[dict]:
        cutoff = int(time.time() - hours * 3600)
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, total_hourly, by_service, breakdown FROM snapshots "
                "WHERE ts >= ? ORDER BY ts", (cutoff,),
            ).fetchall()
        if len(rows) > max_points:
            stride = len(rows) / max_points
            rows = [rows[int(i * stride)] for i in range(max_points)]
        return [
            {
                "ts": ts,
                "total_hourly": total,
                "by_service": json.loads(svc),
                "breakdown": json.loads(bd) if bd else None,
            }
            for ts, total, svc, bd in rows
        ]

    def snapshot_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
