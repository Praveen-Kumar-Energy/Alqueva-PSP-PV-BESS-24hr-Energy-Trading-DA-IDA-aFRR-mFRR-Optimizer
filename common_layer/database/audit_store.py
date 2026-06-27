"""
audit_store.py — read/query the append-only audit trail written by AuditLogger.

The trail is JSON-lines per day. This reader never writes — it only loads and
filters records for inspection, settlement reconciliation, or the demo.
"""
from __future__ import annotations

import json
import os
import datetime as dt
from typing import List, Optional


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, os.pardir, os.pardir))


class AuditStore:
    def __init__(self, audit_dir: Optional[str] = None):
        self.audit_dir = audit_dir or os.path.join(_repo_root(), "runtime", "audit")

    def _path_for(self, day: dt.date) -> str:
        return os.path.join(self.audit_dir, f"audit_{day.isoformat()}.jsonl")

    def read_day(self, day: dt.date, event: Optional[str] = None) -> List[dict]:
        """Return all records for a day, optionally filtered by event name."""
        path = self._path_for(day)
        if not os.path.isfile(path):
            return []
        out: List[dict] = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if event is None or rec.get("event") == event:
                    out.append(rec)
        return out
