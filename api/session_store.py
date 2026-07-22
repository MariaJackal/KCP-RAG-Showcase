"""SQLite-backed session persistence.

Each user (identified by JWT sub claim) gets a JSON-serialized session dict
that mirrors the in-memory ``state`` dict used by ``session_manager.py``.
"""

import sqlite3
import threading

from api.session_serde import deserialize, empty_state, serialize


class SessionStore:
    def __init__(self, db_path: str = "sessions.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        conn = self._conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                user_id    TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

    def get(self, user_id: str) -> dict:
        row = self._conn().execute(
            "SELECT state_json FROM sessions WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return empty_state()
        return deserialize(row[0])

    def save(self, user_id: str, state: dict):
        self._conn().execute(
            """INSERT INTO sessions (user_id, state_json, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE
               SET state_json = excluded.state_json,
                   updated_at = excluded.updated_at""",
            (user_id, serialize(state)),
        )
        self._conn().commit()

    def delete(self, user_id: str):
        self._conn().execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self._conn().commit()
