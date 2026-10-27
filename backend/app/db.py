from typing import AsyncGenerator
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.config import settings
from app.models import Base

# Clean sqlite / postgres connection string
db_url = settings.DATABASE_URL
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif db_url.startswith("sqlite://"):
    db_url = db_url.replace("sqlite://", "sqlite+aiosqlite://", 1)

# Configure engine
connect_args = {}
if "sqlite" in db_url:
    connect_args["check_same_thread"] = False

engine = create_async_engine(
    db_url,
    echo=False,
    connect_args=connect_args,
    pool_pre_ping=True,
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Initializes tables and applies additive migrations to experience memory."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_experience_memory)


def _migrate_experience_memory(sync_conn) -> None:
    """Adds structured memory columns to databases created by older WebMind builds."""
    inspector = inspect(sync_conn)
    if "experience_memories" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("experience_memories")}
    additions = {
        "schema_version": "INTEGER DEFAULT 1",
        "embedding_model": "VARCHAR(128) DEFAULT 'legacy'",
        "domain": "VARCHAR(253)",
        "page_url_pattern": "VARCHAR(500) DEFAULT ''",
        "page_context": "JSON DEFAULT '{}'",
        "action_attempted": "JSON DEFAULT '{}'",
        "failure": "JSON",
        "successful_alternative": "JSON",
        "verified_workflow": "JSON DEFAULT '[]'",
        "reliability_score": "FLOAT DEFAULT 0.5",
        "success_count": "INTEGER DEFAULT 0",
        "failure_count": "INTEGER DEFAULT 0",
        "retrieval_count": "INTEGER DEFAULT 0",
        "last_used_at": "DATETIME",
        "updated_at": "DATETIME",
        "dedupe_key": "VARCHAR(64)",
    }
    for name, sql_type in additions.items():
        if name not in existing:
            sync_conn.execute(text(f"ALTER TABLE experience_memories ADD COLUMN {name} {sql_type}"))
    sync_conn.execute(text(
        "UPDATE experience_memories SET updated_at = created_at WHERE updated_at IS NULL"
    ))
    sync_conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_experience_memories_domain ON experience_memories (domain)"
    ))
    sync_conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_experience_memories_dedupe_key "
        "ON experience_memories (dedupe_key)"
    ))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for obtaining an async database session."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
