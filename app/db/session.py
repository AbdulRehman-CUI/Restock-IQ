from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from app.db.models import Base


class Database:
    def __init__(self, path: Path, url: str | None = None):
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        if url:
            if url.startswith("postgres://"):
                url = "postgresql+psycopg://" + url[len("postgres://"):]
            elif url.startswith("postgresql://"):
                url = "postgresql+psycopg://" + url[len("postgresql://"):]
            self.engine = create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=2,
                                        connect_args={"connect_timeout": 10})
        else:
            self.engine = create_engine(f"sqlite:///{path.as_posix()}",
                                        connect_args={"check_same_thread": False, "timeout": 30})

            @event.listens_for(self.engine, "connect")
            def configure(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA busy_timeout=30000")

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        # Serialize schema initialization across simultaneous cold starts.
        from sqlalchemy import text
        with self.engine.begin() as connection:
            if self.engine.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(73190512)"))
            Base.metadata.create_all(connection)

    @contextmanager
    def transaction(self):
        with self.lock, self.sessions.begin() as session:
            yield session
