import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

logger = logging.getLogger("verba.db")

_SQLITE_FALLBACK_PATH = Path(__file__).resolve().parents[2] / "verba.db"
_SQLITE_FALLBACK_URL = f"sqlite:///{_SQLITE_FALLBACK_PATH.as_posix()}"


def _normalize_database_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def _build_engine():
    url = _normalize_database_url(settings.database_url) or _SQLITE_FALLBACK_URL
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})

    pool_kwargs: dict = {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 10,
        "pool_recycle": 300,
        "pool_timeout": 30,
    }
    if url.startswith("postgresql") or url.startswith("mysql"):
        pool_kwargs["connect_args"] = {"connect_timeout": 10}

    candidate = create_engine(url, **pool_kwargs)
    try:
        with candidate.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return candidate
    except OperationalError as exc:
        logger.warning(
            "Could not connect to configured DATABASE_URL (%s). "
            "Falling back to local SQLite database at %s.",
            exc,
            _SQLITE_FALLBACK_PATH,
        )
        candidate.dispose()
        return create_engine(_SQLITE_FALLBACK_URL, connect_args={"check_same_thread": False})


engine = _build_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
