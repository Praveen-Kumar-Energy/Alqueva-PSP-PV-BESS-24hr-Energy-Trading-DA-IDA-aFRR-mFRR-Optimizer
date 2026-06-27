"""
position_store.py — committed market positions per gate (spec FR-1.4, INV-8).

SQLite-backed store. For each (delivery_date, gate, hour) it holds the committed
volume (MWh, + sell/generation, - buy/pump) and price. Guarantees:
  * what is read back equals what was written (FR-1.4),
  * positions are upserted per gate so re-running a gate is idempotent,
  * `committed_position` merges gates in chronological order to give the running
    net schedule (DA base, then each IDA overwrites only the hours it covers —
    e.g. IDA3 overwrites hours 12-24 only, INV-11).

DB location: <repo_root>/runtime/db/positions.db
"""
from __future__ import annotations

import os
import sqlite3
from typing import Dict

# Chronological gate order — determines which gate's schedule wins per hour.
GATE_ORDER = ["DA", "IDA1", "IDA2", "IDA3", "XBID"]


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, os.pardir, os.pardir))


class PositionStore:
    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_dir = os.path.join(_repo_root(), "runtime", "db")
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, "positions.db")
        self.db_path = db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    delivery_date TEXT    NOT NULL,
                    gate          TEXT    NOT NULL,
                    hour          INTEGER NOT NULL,
                    volume_mwh    REAL    NOT NULL,
                    price_eur_mwh REAL    NOT NULL,
                    updated_at    TEXT    DEFAULT (datetime('now')),
                    PRIMARY KEY (delivery_date, gate, hour)
                )
                """
            )

    # -- write --------------------------------------------------------------
    def save_position(self, delivery_date: str, gate: str,
                      position: Dict[int, dict]) -> None:
        """Upsert a gate's schedule. `position` = {hour: {volume_mwh, price_eur_mwh}}."""
        rows = [
            (delivery_date, gate, int(h),
             float(v["volume_mwh"]), float(v.get("price_eur_mwh", 0.0)))
            for h, v in position.items()
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO positions (delivery_date, gate, hour, volume_mwh, price_eur_mwh)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(delivery_date, gate, hour)
                DO UPDATE SET volume_mwh=excluded.volume_mwh,
                              price_eur_mwh=excluded.price_eur_mwh,
                              updated_at=datetime('now')
                """,
                rows,
            )

    # -- read ---------------------------------------------------------------
    def load_position(self, delivery_date: str, gate: str) -> Dict[int, dict]:
        """Return one gate's stored schedule: {hour: {volume_mwh, price_eur_mwh}}."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT hour, volume_mwh, price_eur_mwh FROM positions "
                "WHERE delivery_date=? AND gate=? ORDER BY hour",
                (delivery_date, gate),
            )
            return {
                row["hour"]: {"volume_mwh": row["volume_mwh"],
                              "price_eur_mwh": row["price_eur_mwh"]}
                for row in cur.fetchall()
            }

    def committed_position(self, delivery_date: str,
                           as_of_gate: str | None = None) -> Dict[int, float]:
        """Running net committed volume per hour up to and including `as_of_gate`.

        Applies gates in GATE_ORDER; each gate overwrites only the hours present
        in its stored schedule, so IDA3 (hours 12-24) leaves 1-11 untouched."""
        net: Dict[int, float] = {}
        for gate in GATE_ORDER:
            sched = self.load_position(delivery_date, gate)
            for h, v in sched.items():
                net[h] = v["volume_mwh"]          # later gate overwrites earlier
            if as_of_gate is not None and gate == as_of_gate:
                break
        return net
