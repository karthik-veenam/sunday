import json
import sqlite3
import threading
from datetime import datetime

DB_PATH = "/home/djpi/sunday/sunday.db"


class Memory:
    def __init__(self, db_path: str = DB_PATH):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init()

    def _init(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS action_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    tool_name   TEXT    NOT NULL,
                    inputs      TEXT,
                    result      TEXT,
                    user_text   TEXT
                );
                CREATE TABLE IF NOT EXISTS conversation_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    role        TEXT    NOT NULL,
                    content     TEXT    NOT NULL
                );
                CREATE TABLE IF NOT EXISTS patterns (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    insight     TEXT    NOT NULL,
                    category    TEXT
                );
                CREATE TABLE IF NOT EXISTS presence_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    state       TEXT    NOT NULL
                );
            """)
            self._conn.commit()

    def log_action(self, tool_name: str, inputs: dict, result: str, user_text: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO action_log (timestamp, tool_name, inputs, result, user_text) VALUES (?,?,?,?,?)",
                (datetime.now().isoformat(), tool_name, json.dumps(inputs), result, user_text),
            )
            self._conn.commit()

    def log_message(self, role: str, content: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversation_log (timestamp, role, content) VALUES (?,?,?)",
                (datetime.now().isoformat(), role, content),
            )
            self._conn.commit()

    def recent_actions(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM action_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def actions_since(self, since_iso: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM action_log WHERE timestamp >= ? ORDER BY id ASC", (since_iso,)
            ).fetchall()
        return [dict(r) for r in rows]

    def save_insight(self, insight: str, category: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO patterns (timestamp, insight, category) VALUES (?,?,?)",
                (datetime.now().isoformat(), insight, category),
            )
            self._conn.commit()

    def last_interaction_time(self) -> str | None:
        """ISO timestamp of most recent non-reflection action, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT timestamp FROM action_log WHERE tool_name != 'reflection' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row["timestamp"] if row else None

    def last_actions_per_device(self) -> list[dict]:
        """Most recent action for each unique device (by inputs JSON)."""
        with self._lock:
            rows = self._conn.execute("""
                SELECT tool_name, inputs, result, timestamp
                FROM action_log
                WHERE tool_name IN ('control_device', 'send_google_assistant_command')
                  AND id IN (
                    SELECT MAX(id) FROM action_log
                    WHERE tool_name IN ('control_device', 'send_google_assistant_command')
                    GROUP BY inputs
                  )
                ORDER BY timestamp DESC
                LIMIT 30
            """).fetchall()
        return [dict(r) for r in rows]

    def get_insights(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM patterns ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


    def log_presence(self, state: str) -> None:
        """Log a presence state change: 'home' or 'away'."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO presence_log (timestamp, state) VALUES (?,?)",
                (datetime.now().isoformat(), state),
            )
            self._conn.commit()

    def recent_presence(self, limit: int = 20) -> list[dict]:
        """Return recent presence state changes, newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM presence_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def last_seen_home(self) -> str | None:
        """ISO timestamp of most recent 'home' entry, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT timestamp FROM presence_log WHERE state='home' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row["timestamp"] if row else None


# Global singleton
_memory: Memory | None = None


def init(db_path: str = DB_PATH) -> None:
    global _memory
    _memory = Memory(db_path)


def get() -> Memory:
    if _memory is None:
        raise RuntimeError("Memory not initialised — call memory.init() first")
    return _memory
