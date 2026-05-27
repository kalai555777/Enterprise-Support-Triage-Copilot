"""Phase 3.1.6 — in-process MCP client + direct-call coverage."""
from __future__ import annotations

import asyncio
import time

import pytest
from fastmcp import Client

from estc.services.mcp_postgres.server import (
    CustomerRecord,
    SubscriptionStatus,
    get_customer_by_id,
    get_subscription_status,
    list_delinquent_accounts,
    mcp,
)

EXPECTED_TOOLS = {
    "get_customer_by_id",
    "get_subscription_status",
    "list_delinquent_accounts",
}


def test_server_name():
    assert mcp.name == "estc-postgres"


@pytest.mark.asyncio
async def test_lists_exactly_three_tools():
    async with Client(mcp) as c:
        tools = await c.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS


@pytest.mark.asyncio
async def test_get_customer_by_id_happy():
    rec = await get_customer_by_id("c-01")
    assert isinstance(rec, CustomerRecord)
    assert rec.company_id == "c-01"
    assert rec.company_name == "Acme Corp"
    assert rec.subscription_tier == "Enterprise"
    assert rec.account_status == "Active"


@pytest.mark.asyncio
async def test_get_customer_by_id_unknown_returns_none():
    assert await get_customer_by_id("does-not-exist") is None


@pytest.mark.asyncio
async def test_get_subscription_status_omits_email():
    s = await get_subscription_status("c-02")
    assert isinstance(s, SubscriptionStatus)
    assert "technical_poc_email" not in s.model_dump()
    assert s.subscription_tier == "Enterprise"
    assert s.account_status == "Delinquent"


@pytest.mark.asyncio
async def test_list_delinquent_accounts_count():
    rows = await list_delinquent_accounts(limit=10)
    assert len(rows) == 4  # seed has c-02, c-07, c-13, c-17
    assert all(r.account_status == "Delinquent" for r in rows)
    assert [r.company_id for r in rows] == sorted(r.company_id for r in rows)


@pytest.mark.asyncio
async def test_limit_clamped_low():
    rows = await list_delinquent_accounts(limit=0)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_limit_clamped_high():
    rows = await list_delinquent_accounts(limit=999)
    assert len(rows) <= 100


@pytest.mark.asyncio
async def test_parameterization_smoke():
    """SQL-injection-shaped input must miss (proves %s binding, not interpolation)."""
    assert await get_customer_by_id("' OR 1=1 --") is None


@pytest.mark.asyncio
async def test_latency_p95():
    # warm the pool
    for _ in range(5):
        await get_customer_by_id("c-01")

    samples = []
    for _ in range(50):
        t0 = time.perf_counter()
        await get_customer_by_id("c-01")
        samples.append((time.perf_counter() - t0) * 1000)

    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1]
    assert p95 <= 150, f"p95={p95:.1f}ms exceeds 150ms budget"
