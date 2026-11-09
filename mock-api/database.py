from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from models import Base


def init_database(app):
    url = make_url(app.config["DATABASE_URL"])
    if url.get_backend_name() != "sqlite":
        raise ValueError("Phase 1 supports SQLite databases only")
    if url.database and url.database != ":memory:":
        Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    options = {"connect_args": {"check_same_thread": False, "timeout": 10}}
    if url.database in (None, "", ":memory:"):
        options["poolclass"] = StaticPool
    engine = create_engine(url, **options)

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, connection_record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        user_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(users)")
        }
        if "role" not in user_columns:
            connection.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN role VARCHAR NOT NULL DEFAULT 'operator'"
            )
        additions = {
            "approval_requests": {
                "operation_id": "VARCHAR", "resource_id": "VARCHAR",
                "request_digest": "VARCHAR", "approval_level": "VARCHAR",
                "approver_id": "VARCHAR REFERENCES users(id)", "decided_at": "VARCHAR",
                "result": "JSON", "failure_code": "VARCHAR",
            },
            "idempotency_records": {
                "operation_id": "VARCHAR", "initiator_id": "VARCHAR",
                "response_status": "INTEGER",
            },
        }
        for table, columns in additions.items():
            existing = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, definition in columns.items():
                if name not in existing:
                    connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS approval_operation_id ON approval_requests(operation_id)"
        )
        connection.exec_driver_sql(
            "UPDATE approval_requests SET status='FAILED', failure_code='APPROVAL_INVALID' "
            "WHERE request_digest IS NULL"
        )
    app.extensions["database"] = engine
    app.extensions["session_factory"] = sessionmaker(bind=engine, expire_on_commit=False)
