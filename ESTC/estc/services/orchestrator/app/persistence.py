"""Durable ticket registry + Postgres checkpoint wiring for the orchestrator.

The orchestrator keeps two pieces of per-ticket state: the LangGraph checkpoint
(the merged ``AgentState`` — draft, confidence, logs, escalation) and a small
registry record (submitted text, company, status, approved flag). Both are durable
when ``ESTC_PERSIST_POSTGRES`` is on: the graph via langgraph's ``AsyncPostgresSaver``
and the registry via the ``tickets`` table here. With persistence off, an in-memory
store is used (the offline/CI/test default) — behaviourally identical, just non-durable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol


@dataclass
class TicketRecord:
    """One submitted ticket. ``status`` advances pending -> running -> done|error|closed."""

    text: str
    company_id: str
    status: str = "pending"
    approved: bool = False  # set by POST /tickets/{id}/approve (operator closed the ticket)


class TicketStore(Protocol):
    async def create(self, ticket_id: str, text: str, company_id: str) -> None: ...
    async def get(self, ticket_id: str) -> Optional[TicketRecord]: ...
    async def save(self, ticket_id: str, rec: TicketRecord) -> None: ...


class InMemoryTicketStore:
    """Process-lifetime registry. Non-durable; the default when persistence is off."""

    def __init__(self) -> None:
        self._d: dict[str, TicketRecord] = {}

    async def create(self, ticket_id: str, text: str, company_id: str) -> None:
        self._d[ticket_id] = TicketRecord(text=text, company_id=company_id)

    async def get(self, ticket_id: str) -> Optional[TicketRecord]:
        return self._d.get(ticket_id)

    async def save(self, ticket_id: str, rec: TicketRecord) -> None:
        self._d[ticket_id] = rec


class PostgresTicketStore:
    """Durable registry backed by a ``tickets`` table in the shared Postgres."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def setup(self) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS tickets (
                       ticket_id  text PRIMARY KEY,
                       text       text NOT NULL,
                       company_id text NOT NULL,
                       status     text NOT NULL DEFAULT 'pending',
                       approved   boolean NOT NULL DEFAULT false,
                       created_at timestamptz NOT NULL DEFAULT now()
                   )"""
            )

    async def create(self, ticket_id: str, text: str, company_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO tickets (ticket_id, text, company_id) VALUES (%s, %s, %s)",
                (ticket_id, text, company_id),
            )

    async def get(self, ticket_id: str) -> Optional[TicketRecord]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT text, company_id, status, approved FROM tickets WHERE ticket_id = %s",
                (ticket_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return TicketRecord(text=row[0], company_id=row[1], status=row[2], approved=row[3])

    async def save(self, ticket_id: str, rec: TicketRecord) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE tickets SET status = %s, approved = %s WHERE ticket_id = %s",
                (rec.status, rec.approved, ticket_id),
            )


async def build_postgres_persistence(settings: Any) -> tuple[Any, Any, PostgresTicketStore]:
    """Open a connection pool and set up the AsyncPostgresSaver + ticket store.

    Returns ``(pool, saver, store)``. Imports are local so the orchestrator boots without
    ``langgraph-checkpoint-postgres`` installed when persistence is disabled.
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    conninfo = (
        f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )
    # autocommit is required for the saver's / store's CREATE TABLE migrations.
    pool = AsyncConnectionPool(conninfo, open=False, kwargs={"autocommit": True})
    await pool.open()
    saver = AsyncPostgresSaver(pool)
    await saver.setup()
    store = PostgresTicketStore(pool)
    await store.setup()
    return pool, saver, store
