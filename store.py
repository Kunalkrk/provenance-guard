import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "store.db"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS submissions (
            content_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            record TEXT NOT NULL
        )
        """
    )
    return conn


def save_submission(record):
    """record is the full decision dict; must include content_id, status, timestamp."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO submissions (content_id, status, created_at, record) VALUES (?, ?, ?, ?)",
            (
                record["content_id"],
                record["status"],
                record["timestamp"],
                json.dumps(record),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_submission(content_id):
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT record FROM submissions WHERE content_id = ?", (content_id,)
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else None


def update_status(content_id, status, extra=None):
    """Update a submission's status; optionally merge extra fields into its stored record."""
    record = get_submission(content_id)
    if record is None:
        return None
    record["status"] = status
    if extra:
        record.update(extra)
    save_submission(record)
    return record
