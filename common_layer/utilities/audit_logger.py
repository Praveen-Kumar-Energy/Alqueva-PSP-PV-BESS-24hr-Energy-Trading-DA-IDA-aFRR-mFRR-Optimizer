"""
audit_logger.py — append-only audit trail (spec FR-1.5, INV-8).

Every trading decision (solve, check, approval, submit, reject, position change)
writes one immutable JSON line. The file is opened in append mode only; records
are never edited or deleted. Each record carries a CET timestamp so the trail
lines up with market gate deadlines.

Trail location: <repo_root>/runtime/audit/audit_<YYYY-MM-DD>.jsonl
"""
from __future__ import annotations

import json
import os
import datetime as dt
from typing import Any

from common_layer.utilities.timezone_utils import now_market


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, os.pardir, os.pardir))


class AuditLogger:
    """Append-only JSON-lines audit writer."""

    def __init__(self, audit_dir: str | None = None):
        self.audit_dir = audit_dir or os.path.join(_repo_root(), "runtime", "audit")
        os.makedirs(self.audit_dir, exist_ok=True)

    def _path_for(self, day: dt.date) -> str:
        return os.path.join(self.audit_dir, f"audit_{day.isoformat()}.jsonl")

    def log(self, event: str, **fields: Any) -> dict:
        """Write one audit record. Returns the record written."""
        ts = now_market()
        record = {
            "timestamp_cet": ts.isoformat(),
            "event": event,
            **fields,
        }
        path = self._path_for(ts.date())
        # 'a' mode — file is never truncated; each call appends exactly one JSON line.
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        return record
