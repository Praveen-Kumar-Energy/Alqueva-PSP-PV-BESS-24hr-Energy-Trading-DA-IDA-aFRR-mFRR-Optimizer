"""
logging_utils.py — one consistent logger for the whole system.

get_logger(name) returns a module logger with a single stream handler and a
fixed format. Idempotent: repeated calls never stack duplicate handlers.
"""
from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_configured: set = set()

# Make the console UTF-8 so output never turns to mojibake on a Windows (cp1252)
# terminal. Safe no-op where stdout cannot be reconfigured.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if name not in _configured:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False           # avoid double prints via root
        _configured.add(name)
    return logger
