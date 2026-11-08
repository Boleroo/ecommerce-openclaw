import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class SQLiteAuditStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS tool_executions (
                    trace_id TEXT PRIMARY KEY,
                    tool TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    user_id TEXT,
                    tenant_id TEXT,
                    state TEXT NOT NULL,
                    success INTEGER,
                    error_code TEXT,
                    recoverable INTEGER,
                    requests TEXT NOT NULL DEFAULT '[]'
                )
            """)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def start(self, trace_id, tool):
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO tool_executions (trace_id, tool, started_at, state) VALUES (?, ?, ?, ?)",
                (trace_id, tool, datetime.now(timezone.utc).isoformat(), "started"),
            )

    def finish(self, observation, identity, requests):
        with self._connect() as connection:
            cursor = connection.execute("""
                UPDATE tool_executions SET finished_at=?, user_id=?, tenant_id=?, state=?,
                    success=?, error_code=?, recoverable=?, requests=? WHERE trace_id=?
            """, (
                datetime.now(timezone.utc).isoformat(),
                identity.user_id if identity else None, identity.tenant_id if identity else None,
                "completed", observation.success,
                observation.error.code if observation.error else None,
                observation.error.recoverable if observation.error else None,
                json.dumps(requests), observation.trace_id,
            ))
            if cursor.rowcount != 1:
                raise RuntimeError("Missing execution audit record")

    def checkpoint(self, trace_id, identity, requests):
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE tool_executions SET user_id=?, tenant_id=?, requests=? WHERE trace_id=? AND state='started'",
                (identity.user_id, identity.tenant_id, json.dumps(requests), trace_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Missing pending execution audit record")

    def get(self, trace_id):
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM tool_executions WHERE trace_id=?", (trace_id,)).fetchone()
            if row is None:
                return None
            record = dict(row)
            record["requests"] = json.loads(record["requests"])
            return record
