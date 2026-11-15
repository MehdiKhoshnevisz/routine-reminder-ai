from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.base import Base

if settings.serverless:
    # Serverless instances are frozen between requests and may run each request on a new event loop,
    # so don't keep pooled connections around (use the provider's pooled URL, e.g. Neon "-pooler").
    # prepare_threshold=None: server-side prepared statements break behind PgBouncer-style poolers.
    engine = create_async_engine(
        settings.DATABASE_URL, poolclass=NullPool, connect_args={"prepare_threshold": None}
    )
else:
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

_db_ready = False

_MIGRATIONS = [
    "ALTER TABLE agent_histories ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()",
]


async def get_db() -> AsyncIterator[AsyncSession]:
    await init_db()
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Creates missing tables. Runs once per process (on startup locally, on first request when serverless)."""
    global _db_ready
    if _db_ready:
        return
    import app.models  # noqa: F401  (register models)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight migrations for tables that already existed before a column was added.
        for stmt in _MIGRATIONS:
            await conn.execute(text(stmt))
    _db_ready = True
