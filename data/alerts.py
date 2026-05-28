"""SQLite-backed alert log for the dashboard.

Phase 1 ships only the scaffold — write/read helpers and schema. Phase 3 wires
in the regional alert firings (deficit, divergence elevated, salt cavern,
anomalous flow). Failures in this module never raise; the rest of the app must
continue running even if SQLite is unavailable.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import threading

_DB_PATH = os.path.join(os.path.dirname(__file__), "alerts.db")
_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    triggering_values TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_unresolved
    ON alerts (resolved, alert_type);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Idempotent — safe to call on import."""
    try:
        with _LOCK, _connect() as conn:
            conn.executescript(_SCHEMA)
    except Exception:
        pass


def log_alert(alert_type: str, level: str, message: str,
              triggering_values: dict | None = None) -> int | None:
    """Insert or update an alert row.

    If an unresolved row with the same ``alert_type`` already exists, its
    message/values are updated in place (prevents duplicate spam on every
    refresh tick). Otherwise a new row is inserted.

    Returns the row id, or None on failure.
    """
    payload = json.dumps(triggering_values or {}, default=str)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        with _LOCK, _connect() as conn:
            existing = conn.execute(
                "SELECT id FROM alerts WHERE alert_type = ? AND resolved = 0 "
                "ORDER BY id DESC LIMIT 1",
                (alert_type,),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE alerts SET timestamp = ?, level = ?, message = ?, "
                    "triggering_values = ? WHERE id = ?",
                    (now, level, message, payload, existing["id"]),
                )
                return int(existing["id"])
            cur = conn.execute(
                "INSERT INTO alerts (timestamp, alert_type, level, message, "
                "triggering_values) VALUES (?, ?, ?, ?, ?)",
                (now, alert_type, level, message, payload),
            )
            return int(cur.lastrowid)
    except Exception:
        return None


def mark_resolved(alert_type: str) -> int:
    """Mark all unresolved rows of ``alert_type`` as resolved. Returns row count."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        with _LOCK, _connect() as conn:
            cur = conn.execute(
                "UPDATE alerts SET resolved = 1, resolved_at = ? "
                "WHERE alert_type = ? AND resolved = 0",
                (now, alert_type),
            )
            return cur.rowcount or 0
    except Exception:
        return 0


def recent_alerts(limit: int = 50) -> list[dict]:
    """Return the most recent ``limit`` alerts as plain dicts (newest first)."""
    try:
        with _LOCK, _connect() as conn:
            rows = conn.execute(
                "SELECT id, timestamp, alert_type, level, message, "
                "triggering_values, resolved, resolved_at "
                "FROM alerts ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["triggering_values"] = json.loads(d["triggering_values"])
                except Exception:
                    pass
                out.append(d)
            return out
    except Exception:
        return []


init_db()
