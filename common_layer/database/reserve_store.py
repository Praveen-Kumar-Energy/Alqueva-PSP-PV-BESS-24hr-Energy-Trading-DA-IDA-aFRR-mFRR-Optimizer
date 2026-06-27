"""
reserve_store.py — committed reserve capacity offers per product.

Parallel to PositionStore but for reserve: it holds the up/down MW and cap prices
offered per (delivery_date, product, hour). mFRR reads the aFRR commitment from
here so it only offers headroom aFRR did not already take (PR-11 across products).

DB location: <repo_root>/runtime/db/reserve.db
"""
from __future__ import annotations

import os
import sqlite3
from typing import Dict


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, os.pardir, os.pardir))


class ReserveStore:
    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_dir = os.path.join(_repo_root(), "runtime", "db")
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, "reserve.db")
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
                CREATE TABLE IF NOT EXISTS reserve (
                    delivery_date TEXT    NOT NULL,
                    product       TEXT    NOT NULL,
                    hour          INTEGER NOT NULL,
                    up_mw         REAL    NOT NULL,
                    dn_mw         REAL    NOT NULL,
                    cap_up_eur_mw REAL    NOT NULL,
                    cap_dn_eur_mw REAL    NOT NULL,
                    updated_at    TEXT    DEFAULT (datetime('now')),
                    PRIMARY KEY (delivery_date, product, hour)
                )
                """
            )

    def save_reserve(self, delivery_date: str, product: str,
                     offers: Dict[int, dict]) -> None:
        """offers = {hour: {up_mw, dn_mw, cap_up_eur_mw, cap_dn_eur_mw}}."""
        rows = [
            (delivery_date, product, int(h),
             float(o["up_mw"]), float(o["dn_mw"]),
             float(o.get("cap_up_eur_mw", 0.0)), float(o.get("cap_dn_eur_mw", 0.0)))
            for h, o in offers.items()
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO reserve (delivery_date, product, hour, up_mw, dn_mw,
                                     cap_up_eur_mw, cap_dn_eur_mw)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(delivery_date, product, hour)
                DO UPDATE SET up_mw=excluded.up_mw, dn_mw=excluded.dn_mw,
                              cap_up_eur_mw=excluded.cap_up_eur_mw,
                              cap_dn_eur_mw=excluded.cap_dn_eur_mw,
                              updated_at=datetime('now')
                """, rows,
            )

    def load_reserve(self, delivery_date: str, product: str) -> Dict[int, dict]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT hour, up_mw, dn_mw, cap_up_eur_mw, cap_dn_eur_mw FROM reserve "
                "WHERE delivery_date=? AND product=? ORDER BY hour",
                (delivery_date, product),
            )
            return {
                r["hour"]: {"up_mw": r["up_mw"], "dn_mw": r["dn_mw"],
                            "cap_up_eur_mw": r["cap_up_eur_mw"],
                            "cap_dn_eur_mw": r["cap_dn_eur_mw"]}
                for r in cur.fetchall()
            }

    def reserved_up(self, delivery_date: str, product: str) -> Dict[int, float]:
        return {h: o["up_mw"] for h, o in self.load_reserve(delivery_date, product).items()}

    def reserved_dn(self, delivery_date: str, product: str) -> Dict[int, float]:
        return {h: o["dn_mw"] for h, o in self.load_reserve(delivery_date, product).items()}
