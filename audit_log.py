import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "audit_log.db"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS log_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            content_id TEXT,
            payload TEXT NOT NULL
        )
        """
    )
    return conn


def log_entry(entry_type, content_id, payload):
    """payload is a dict of whatever this entry type needs to record."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO log_entries (timestamp, entry_type, content_id, payload) VALUES (?, ?, ?, ?)",
            (payload.get("timestamp"), entry_type, content_id, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()


def get_log(limit=50):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT entry_type, content_id, payload FROM log_entries ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    entries = []
    for entry_type, content_id, payload in rows:
        entry = json.loads(payload)
        entry["entry_type"] = entry_type
        entries.append(entry)
    return entries
