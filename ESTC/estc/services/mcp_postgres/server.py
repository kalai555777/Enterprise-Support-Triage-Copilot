"""Read-only FastMCP server fronting enterprise_customers (Phase 3.1, async)."""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Literal, Optional

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastmcp import FastMCP
from pydantic import BaseModel
from psycopg_pool import AsyncConnectionPool

SubscriptionTier = Literal["Enterprise", "Growth", "Free"]
AccountStatus = Literal["Active", "Delinquent", "Locked"]


class CustomerRecord(BaseModel):
    company_id: str
    company_name: str
    subscription_tier: SubscriptionTier
    account_status: AccountStatus
    technical_poc_email: str


class SubscriptionStatus(BaseModel):
    company_id: str
    subscription_tier: SubscriptionTier
    account_status: AccountStatus


mcp = FastMCP("estc-postgres")

_pool: AsyncConnectionPool | None = None


def _conninfo() -> str:
    return (
        f"host={os.environ['POSTGRES_HOST']} "
        f"port={os.environ.get('POSTGRES_PORT', '5432')} "
        f"dbname={os.environ['POSTGRES_DB']} "
        f"user={os.environ['POSTGRES_READER_USER']} "
        f"password={os.environ['POSTGRES_READER_PASSWORD']}"
    )


async def _get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        pool = AsyncConnectionPool(
            conninfo=_conninfo(), min_size=1, max_size=4, open=False
        )
        await pool.open()
        _pool = pool
    return _pool


@mcp.tool
async def get_customer_by_id(company_id: str) -> Optional[CustomerRecord]:
    """Fetch the full enterprise_customers row for a given company_id."""
    pool = await _get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT company_id, company_name, subscription_tier, "
            "account_status, technical_poc_email "
            "FROM enterprise_customers WHERE company_id = %s",
            (company_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return CustomerRecord(
        company_id=row[0],
        company_name=row[1],
        subscription_tier=row[2],
        account_status=row[3],
        technical_poc_email=row[4],
    )


@mcp.tool
async def get_subscription_status(company_id: str) -> Optional[SubscriptionStatus]:
    """Return only subscription_tier + account_status for a company_id."""
    rec = await get_customer_by_id(company_id)
    if rec is None:
        return None
    return SubscriptionStatus(
        company_id=rec.company_id,
        subscription_tier=rec.subscription_tier,
        account_status=rec.account_status,
    )


@mcp.tool
async def list_delinquent_accounts(limit: int = 10) -> list[CustomerRecord]:
    """List up to `limit` (1..100) customers whose account_status='Delinquent'."""
    limit = max(1, min(100, limit))
    pool = await _get_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT company_id, company_name, subscription_tier, "
            "account_status, technical_poc_email "
            "FROM enterprise_customers "
            "WHERE account_status = 'Delinquent' "
            "ORDER BY company_id LIMIT %s",
            (limit,),
        )
        rows = await cur.fetchall()
    return [
        CustomerRecord(
            company_id=r[0],
            company_name=r[1],
            subscription_tier=r[2],
            account_status=r[3],
            technical_poc_email=r[4],
        )
        for r in rows
    ]


async def main() -> None:
    await _get_pool()
    try:
        await mcp.run_async()
    finally:
        if _pool is not None:
            await _pool.close()


if __name__ == "__main__":
    asyncio.run(main())
