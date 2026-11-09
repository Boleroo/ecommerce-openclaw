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
        user_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(users)")
        }
        if "role" not in user_columns:
            connection.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN role VARCHAR NOT NULL DEFAULT 'operator'"
            )
    app.extensions["database"] = engine
    app.extensions["session_factory"] = sessionmaker(bind=engine, expire_on_commit=False)
