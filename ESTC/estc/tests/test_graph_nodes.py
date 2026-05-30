"""Phase 4.3 LangGraph node contract tests (tasks 4.3.1-4.3.7; AC-T1 .. AC-T9).

Runs fully offline and DB-independent:
- LLM keys are stripped so ``draft_reply`` uses the deterministic template path.
- The Postgres MCP coroutines are monkeypatched with canned records (no live DB).
- ``aretrieve`` is monkeypatched to avoid loading the bge embedder / Chroma store.
- The GitHub MCP runs in its real file-backed mock mode (forced by conftest.py).
"""

from __future__ import annotations

import re

import httpx
import pytest

from estc.services.mcp_postgres.server import CustomerRecord, SubscriptionStatus
from estc.services.orchestrator.graph.nodes import (
    billing_agent as billing_mod,
    bug_agent as bug_mod,
    feature_agent as feature_mod,
    lockout_agent as lockout_mod,
)
from estc.services.orchestrator.graph.nodes.billing_agent import billing_agent
from estc.services.orchestrator.graph.nodes.bug_agent import bug_agent
from estc.services.orchestrator.graph.nodes.classify import classify
from estc.services.orchestrator.graph.nodes.feature_agent import feature_agent
from estc.services.orchestrator.graph.nodes.lockout_agent import lockout_agent
from estc.services.orchestrator.graph.nodes.router import route_by_intent
from estc.services.orchestrator.graph.nodes.supervisor import supervisor_review
from estc.shared.schemas.agent_state import AgentState


def _state(**kw) -> AgentState:
    base = dict(ticket_id="t1", raw_issue_text="something is broken", company_id="c-01")
    base.update(kw)
    return AgentState(**base)


def _apply(state: AgentState, update: dict[str, object]) -> AgentState:
    """Merge a node's partial-update dict into the state (mimics LangGraph merge)."""
    return state.model_copy(update=update)


@pytest.fixture(autouse=True)
def _force_template_path(monkeypatch):
    """Strip LLM keys and reset the cached chat model so the template path is used."""
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    from estc.services.orchestrator.graph import llm

    llm._chat_model.cache_clear()
    yield
    llm._chat_model.cache_clear()


@pytest.fixture
def stub_retrieval(monkeypatch):
    """Make aretrieve a no-op (empty) in every worker module — keeps tests fast/offline."""

    async def _empty(*_args, **_kwargs):
        return []

    for mod in (billing_mod, bug_mod, feature_mod):
        monkeypatch.setattr(mod, "aretrieve", _empty)


@pytest.fixture
def stub_postgres(monkeypatch):
    """Canned Postgres MCP responses so billing/lockout need no live DB."""

    async def _sub(company_id):
        return SubscriptionStatus(
            company_id=company_id, subscription_tier="Enterprise", account_status="Active"
        )

    async def _cust(company_id):
        return CustomerRecord(
            company_id=company_id,
            company_name="Acme Corp",
            subscription_tier="Enterprise",
            account_status="Locked",
            technical_poc_email="poc@acme.com",
        )

    monkeypatch.setattr(billing_mod, "get_subscription_status", _sub)
    monkeypatch.setattr(lockout_mod, "get_customer_by_id", _cust)


# --- AC-T1 -----------------------------------------------------------------
async def test_classify_with_mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"intent": "bug", "confidence": 0.85, "latency_ms": 3.0})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    try:
        out = await classify(_state(), client=client)
    finally:
        await client.aclose()

    assert out["intent"] == "bug"
    assert out["confidence_score"] == 0.85
    assert "classified:bug" in out["execution_logs"]


# --- AC-T2 -----------------------------------------------------------------
@pytest.mark.parametrize(
    "intent,expected",
    [
        ("billing", "billing_agent"),
        ("bug", "bug_agent"),
        ("feature", "feature_agent"),
        ("lockout", "lockout_agent"),
        (None, "billing_agent"),
        ("nonsense", "billing_agent"),
    ],
)
def test_route_table_and_fallback(intent, expected):
    assert route_by_intent(_state(intent=intent)) == expected


# --- AC-T3 -----------------------------------------------------------------
async def test_billing_agent_mentions_tier(stub_postgres, stub_retrieval):
    out = await billing_agent(_state(raw_issue_text="charged twice"))
    assert "Enterprise" in out["agent_draft_response"]
    assert "billing_drafted" in out["execution_logs"]


# --- AC-T4 -----------------------------------------------------------------
async def test_bug_agent_cites_issue(stub_retrieval):
    out = await bug_agent(_state(raw_issue_text="500 error on /api/orders"))
    assert re.search(r"#\d+", out["agent_draft_response"])
    assert "bug_drafted" in out["execution_logs"]


# --- AC-T5 -----------------------------------------------------------------
async def test_feature_agent_logs_internal_ticket(stub_retrieval):
    out = await feature_agent(_state(raw_issue_text="please add dark mode"))
    assert "feature_logged" in out["execution_logs"]
    assert any(e.startswith("feature_ticket:") for e in out["execution_logs"])


# --- AC-T6 -----------------------------------------------------------------
async def test_lockout_agent_escalates(stub_postgres):
    out = await lockout_agent(_state(raw_issue_text="cannot log in"))
    assert out["requires_escalation"] is True
    assert out["confidence_score"] >= 0


# --- AC-T7 -----------------------------------------------------------------
def test_supervisor_low_confidence_escalates():
    out = supervisor_review(_state(confidence_score=0.5))
    assert out["execution_logs"] == ["ESCALATE"]
    assert out["requires_escalation"] is True


def test_supervisor_high_confidence_approves():
    out = supervisor_review(_state(confidence_score=0.9, requires_escalation=False))
    assert out["execution_logs"] == ["AUTO_APPROVED"]
    assert out["requires_escalation"] is False


# --- AC-T9 -----------------------------------------------------------------
async def test_execution_logs_accumulate(stub_postgres, monkeypatch):
    # Non-empty retrieval -> confidence stays high (0.85) so the supervisor auto-approves,
    # exercising the clean happy path while proving logs accumulate across nodes.
    from estc.services.orchestrator.rag.retriever import KBIndex, RetrievedChunk

    async def _one_hit(*_args, **_kwargs):
        return [
            RetrievedChunk(
                content="Refunds take 5 to 7 business days.",
                source="billing.md",
                index=KBIndex.BILLING,
                score=0.9,
            )
        ]

    monkeypatch.setattr(billing_mod, "aretrieve", _one_hit)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"intent": "billing", "confidence": 0.85, "latency_ms": 3.0}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    state = _state()
    try:
        state = _apply(state, await classify(state, client=client))
    finally:
        await client.aclose()
    state = _apply(state, await billing_agent(state))
    state = _apply(state, supervisor_review(state))

    assert state.execution_logs == ["classified:billing", "billing_drafted", "AUTO_APPROVED"]
    assert len(state.execution_logs) == 3  # accumulated, not overwritten
