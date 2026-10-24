from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from app.db.models import Base


class Database:
    def __init__(self, path: Path):
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.engine = create_engine(f"sqlite:///{path.as_posix()}",
                                    connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(self.engine, "connect")
        def configure(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=30000")

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        Base.metadata.create_all(self.engine)

    @contextmanager
    def transaction(self):
        with self.lock, self.sessions.begin() as session:
            yield session
