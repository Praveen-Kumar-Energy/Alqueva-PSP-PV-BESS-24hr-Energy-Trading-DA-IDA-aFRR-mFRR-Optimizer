"""
realtime_store.py — delivery actuals and reserve activations (Phase 4 → Phase 5).

Two SQLite-backed stores written during delivery (Phase 4) and read at settlement
(Phase 5):

    DeliveryStore     per-ISP scheduled vs actual net MW → imbalance settlement
    ActivationStore   per-ISP aFRR/mFRR activated energy (up/dn MW) → reserve settlement

DB location: <repo_root>/runtime/db/realtime.db
"""
from __future__ import annotations

import os
import sqlite3
from typing import Dict, List


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, os.pardir, os.pardir))


def _db_path(db_path: str | None) -> str:
    if db_path:
        return db_path
    db_dir = os.path.join(_repo_root(), "runtime", "db")
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "realtime.db")


class DeliveryStore:
    """Per-ISP scheduled vs actual net power."""

    def __init__(self, db_path: str | None = None):
        self.db_path = _db_path(db_path)
        with self._c() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery (
                    delivery_date TEXT NOT NULL,
                    isp INTEGER NOT NULL,
                    hour INTEGER NOT NULL,
                    scheduled_mw REAL NOT NULL,
                    actual_mw REAL NOT NULL,
                    PRIMARY KEY (delivery_date, isp)
                )
                """
            )

    def _c(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, delivery_date: str, rows: List[dict]) -> None:
        """rows = [{isp, hour, scheduled_mw, actual_mw}]."""
        data = [(delivery_date, int(r["isp"]), int(r["hour"]),
                 float(r["scheduled_mw"]), float(r["actual_mw"])) for r in rows]
        with self._c() as conn:
            conn.executemany(
                """INSERT INTO delivery (delivery_date, isp, hour, scheduled_mw, actual_mw)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(delivery_date, isp) DO UPDATE SET
                       hour=excluded.hour, scheduled_mw=excluded.scheduled_mw,
                       actual_mw=excluded.actual_mw""", data)

    def load(self, delivery_date: str) -> List[dict]:
        with self._c() as conn:
            cur = conn.execute(
                "SELECT isp, hour, scheduled_mw, actual_mw FROM delivery "
                "WHERE delivery_date=? ORDER BY isp", (delivery_date,))
            return [dict(r) for r in cur.fetchall()]


class ActivationStore:
    """Per-ISP aFRR/mFRR activated energy written by the delivery monitor.

    Separate up_price_eur_mwh / dn_price_eur_mwh columns are required because
    up-regulation (scarcity) and down-regulation (surplus) carry different energy
    prices — applying a single price to (up_mw + dn_mw) would misstate revenue.

    energy_price_eur_mwh is a legacy column kept for backward compatibility with
    older databases; it mirrors up_price when up > 0, otherwise dn_price.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = _db_path(db_path)
        with self._c() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS activations (
                    delivery_date        TEXT    NOT NULL,
                    product              TEXT    NOT NULL,
                    isp                  INTEGER NOT NULL,
                    hour                 INTEGER NOT NULL,
                    up_mw                REAL    NOT NULL,
                    dn_mw                REAL    NOT NULL,
                    energy_price_eur_mwh REAL    NOT NULL DEFAULT 0,
                    up_price_eur_mwh     REAL    NOT NULL DEFAULT 0,
                    dn_price_eur_mwh     REAL    NOT NULL DEFAULT 0,
                    eff_isp_h            REAL    NOT NULL DEFAULT 0.25,
                    PRIMARY KEY (delivery_date, product, isp)
                )
                """
            )
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add new columns to databases created before this schema version."""
        cur = conn.execute("PRAGMA table_info(activations)")
        existing = {r[1] for r in cur.fetchall()}
        for col, defval in [
            ("up_price_eur_mwh", "0"),
            ("dn_price_eur_mwh", "0"),
            ("eff_isp_h", "0.25"),
        ]:
            if col not in existing:
                conn.execute(
                    f"ALTER TABLE activations ADD COLUMN {col} REAL NOT NULL DEFAULT {defval}")

    def _c(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, delivery_date: str, product: str, rows: List[dict]) -> None:
        """rows = [{isp, hour, up_mw, dn_mw, up_price_eur_mwh, dn_price_eur_mwh, eff_isp_h}]."""
        data = []
        for r in rows:
            up_p = float(r["up_price_eur_mwh"])
            dn_p = float(r["dn_price_eur_mwh"])
            legacy_p = up_p if float(r["up_mw"]) > 1e-9 else dn_p
            eff = float(r.get("eff_isp_h", 0.25))
            data.append((
                delivery_date, product, int(r["isp"]), int(r["hour"]),
                float(r["up_mw"]), float(r["dn_mw"]),
                legacy_p, up_p, dn_p, eff,
            ))
        with self._c() as conn:
            # Replace-all semantics: the activation list for (date, product) is the
            # complete output of one pipeline run. ISPs absent from this run were not
            # activated; stale rows from a prior run must not persist.
            conn.execute(
                "DELETE FROM activations WHERE delivery_date=? AND product=?",
                (delivery_date, product))
            conn.executemany(
                """INSERT INTO activations
                   (delivery_date, product, isp, hour, up_mw, dn_mw,
                    energy_price_eur_mwh, up_price_eur_mwh, dn_price_eur_mwh, eff_isp_h)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", data)

    def load(self, delivery_date: str, product: str) -> List[dict]:
        with self._c() as conn:
            cur = conn.execute(
                "SELECT isp, hour, up_mw, dn_mw, "
                "energy_price_eur_mwh, up_price_eur_mwh, dn_price_eur_mwh, eff_isp_h "
                "FROM activations WHERE delivery_date=? AND product=? ORDER BY isp",
                (delivery_date, product))
            return [dict(r) for r in cur.fetchall()]
