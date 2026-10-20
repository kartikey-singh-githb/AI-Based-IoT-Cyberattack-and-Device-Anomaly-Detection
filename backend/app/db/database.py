"""SQLAlchemy engine / session management for the P_310 SQLite database.

Design notes
------------
* Synchronous SQLAlchemy is used deliberately: SQLite is a local file store
  and async drivers would add complexity without real benefit here.
* ``check_same_thread=False`` plus a scoped session registry makes the
  session safe to use from FastAPI threadpool handlers and background tasks.
* ``init_db()`` is idempotent and creates every table on a fresh install.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.core.config import settings
from app.core.logging_config import get_logger

log = get_logger("db")

Base = declarative_base()

_engine = None
_SessionFactory: sessionmaker | None = None


def _build_engine():
    url = settings.database_url
    kwargs = {"future": True}

    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        kwargs["poolclass"] = None  # StaticPool-like behaviour via NullPool
    else:  # pragma: no cover - other engines are not used in this project
        kwargs["pool_pre_ping"] = True

    eng = create_engine(url, **kwargs)

    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _set_sqlite_pragma(dbapi_connection, _record):
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

    return eng


def get_engine():
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def _get_factory() -> sessionmaker:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_engine(), autoflush=False, autocommit=False,
            expire_on_commit=False, future=True,
        )
    return _SessionFactory


def init_db() -> None:
    """Create all tables (idempotent)."""
    # Import models so they register on the metadata before create_all.
    from app.db import models  # noqa: F401

    path = settings.database_path_absolute
    if path.parent and str(path.parent) not in ("", "."):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    Base.metadata.create_all(bind=get_engine())
    if url_is_sqlite(get_engine()):
        _ensure_missing_columns(get_engine())
    log.info("Database initialised at %s", settings.database_url)


def url_is_sqlite(engine) -> bool:
    return engine.dialect.name == "sqlite"


# Column defaults applied when adding a column that a legacy database file is
# missing.  Only used by the additive migration below.
_COLUMN_DDL_DEFAULTS: dict[tuple[str, str], str] = {
    ("anomaly_events", "origin"): "'detector'",
}


def _ensure_missing_columns(engine) -> None:
    """Additive migration: create columns introduced after a DB was created.

    ``create_all`` never alters an existing table, so a database file written
    by an older revision would otherwise fail on every query.  SQLite cannot
    add a NOT NULL column without a DEFAULT, hence the explicit map above.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            coltype = column.type.compile(engine.dialect)
            default = _COLUMN_DDL_DEFAULTS.get((table.name, column.name))
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {coltype}'
            if default is not None:
                ddl += f" DEFAULT {default}"
            elif column.nullable:
                pass
            else:
                log.warning(
                    "Skipping NOT NULL column %s.%s (no migration default)",
                    table.name, column.name,
                )
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(text(ddl))
                log.info("Migrated schema: %s", ddl)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("Schema migration failed (%s): %s", ddl, exc)


def reset_engine() -> None:
    """Dispose of cached engine/session factory (used by tests)."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of operations."""
    session = _get_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session."""
    session = _get_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
