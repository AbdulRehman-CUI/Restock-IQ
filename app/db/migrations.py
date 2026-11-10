"""Additive, transactionally versioned migrations for SQLite and PostgreSQL.

No existing business table is altered. Downgrades retain new tables and data;
rollback the application only. Never delete the ledger on a populated database.
"""
from datetime import datetime, timezone
from sqlalchemy import select, text
from app.db.market import SchemaVersion, MARKET_TABLES, IMMUTABLE_TABLES, FXRefresh


def upgrade(connection):
    SchemaVersion.__table__.create(connection, checkfirst=True)
    versions = set(connection.scalars(select(SchemaVersion.version)))
    if any(version > 1 for version in versions):
        raise RuntimeError("Database migration version is newer than this application")
    if 1 in versions:
        return
    for table in MARKET_TABLES:
        table.create(connection, checkfirst=True)
    for table in IMMUTABLE_TABLES:
        if connection.dialect.name == "sqlite":
            for operation in ("UPDATE", "DELETE"):
                connection.execute(text(
                    f"CREATE TRIGGER IF NOT EXISTS {table.name}_no_{operation.lower()} "
                    f"BEFORE {operation} ON {table.name} BEGIN SELECT RAISE(ABORT, 'Market records are immutable'); END"))
        else:
            connection.execute(text(f"CREATE TRIGGER {table.name}_immutable BEFORE UPDATE OR DELETE ON {table.name} "
                                    "FOR EACH ROW EXECUTE FUNCTION restockiq_reject_mutation()"))
    connection.execute(FXRefresh.__table__.insert().values(id="exchange-rate-api", lease_until="", next_attempt_at=""))
    connection.execute(SchemaVersion.__table__.insert().values(version=1, applied_at=datetime.now(timezone.utc).isoformat()))
