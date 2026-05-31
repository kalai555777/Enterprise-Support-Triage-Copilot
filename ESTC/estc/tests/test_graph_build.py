"""Phase 4.4 graph-wiring tests (tasks 4.4.1-4.4.2; AC-T1 .. AC-T8).

The topology tests are fully offline and DB-independent. The ``run_ticket`` integration
test is also offline: the *real* module-level ``graph`` is driven, but because each node
resolves its dependencies from its own module globals at call time, monkeypatching
``classify.httpx`` (deterministic ``bug`` intent, no network) and ``bug_agent.aretrieve``
(skip the bge/Chroma load) — together with the conftest-forced GitHub mock — lets the
compiled graph run end-to-end with zero live infra. The literal 4.4.2 "against the seeded
DB" assertion lives in ``test_run_ticket_live_seeded_db`` and is skip-guarded.
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import httpx
import pytest

from estc.services.orchestrator.graph import build
from estc.services.orchestrator.graph.build import astream_ticket, graph, run_ticket
from estc.services.orchestrator.graph.nodes import bug_agent as bug_mod
from estc.services.orchestrator.graph.nodes import classify as classify_mod
from estc.shared.schemas.agent_state import AgentState

_WORKERS = ("billing_agent", "bug_agent", "feature_agent", "lockout_agent")
_CANONICAL = "I am getting a 500 error when pulling the API, my company ID is 9422"


@pytest.fixture(autouse=True)
def _force_template_path(monkeypatch):
    """Strip LLM keys and reset the cached chat model so the offline template path runs."""
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    from estc.services.orchestrator.graph import llm

    llm._chat_model.cache_clear()
    yield
    llm._chat_model.cache_clear()


@pytest.fixture
def offline_bug_run(monkeypatch):
    """Make the real graph runnable offline for a ``bug`` ticket:
    - classify -> deterministic {"intent":"bug"} via an httpx.MockTransport (no network)
    - bug_agent.aretrieve -> [] (skip the bge embedder / Chroma store)
    The GitHub MCP stays in its file-backed mock mode (forced by conftest.py).
    """

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"intent": "bug", "confidence": 0.85, "latency_ms": 3.0})

    def _client_factory(*_args, **_kwargs) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url="http://test")

    # Swap only classify's bound ``httpx`` name (not the global module) for the mock client.
    monkeypatch.setattr(classify_mod, "httpx", SimpleNamespace(AsyncClient=_client_factory))

    async def _no_retrieval(*_args, **_kwargs):
        return []

    monkeypatch.setattr(bug_mod, "aretrieve", _no_retrieval)


# --- AC-T1 -----------------------------------------------------------------
def test_mermaid_shows_six_nodes():
    mermaid = graph.get_graph().draw_mermaid()
    for node in ("classify", *_WORKERS, "supervisor_review"):
        assert node in mermaid


# --- AC-T2 / AC-B2 ---------------------------------------------------------
def test_edges_route_through_supervisor():
    pairs = {(e.source, e.target) for e in graph.get_graph().edges}
    assert ("__start__", "classify") in pairs                      # entry
    for worker in _WORKERS:
        assert ("classify", worker) in pairs                       # conditional fan-out
        assert (worker, "supervisor_review") in pairs              # fan-in to the gate
        assert (worker, "__end__") not in pairs                    # never bypass supervisor
    assert ("supervisor_review", "__end__") in pairs               # terminal


# --- AC-T3 -----------------------------------------------------------------
async def test_graph_has_memory_checkpointer(offline_bug_run):
    assert graph.checkpointer is not None
    ticket_id = "e2e-ckpt-001"
    await run_ticket(ticket_id, _CANONICAL, "9422")
    snapshot = graph.get_state({"configurable": {"thread_id": ticket_id}})
    assert snapshot.values  # run is resumable/inspectable by thread_id


# --- AC-T5 -----------------------------------------------------------------
async def test_run_ticket_offline_streams(offline_bug_run):
    events = [name async for name, _update in astream_ticket("e2e-stream-001", _CANONICAL, "9422")]
    assert events[0] == "classify"
    assert events[1] == "bug_agent"
    assert events[2] == "supervisor_review"
    assert len(events) >= 3


# --- AC-T4 (offline) / AC-T6 -----------------------------------------------
async def test_run_ticket_offline_returns_populated_state(offline_bug_run):
    state = await run_ticket("e2e-state-001", _CANONICAL, "9422")
    assert isinstance(state, AgentState)
    assert state.intent == "bug"
    assert state.agent_draft_response
    assert state.confidence_score > 0
    # Logs accumulate across nodes (classify -> worker -> supervisor) with no reducer.
    assert state.execution_logs[0].startswith("classified:")
    assert "bug_drafted" in state.execution_logs
    assert state.execution_logs[-1] in {"AUTO_APPROVED", "ESCALATE"}


# --- AC-T4 (latency) -------------------------------------------------------
async def test_run_ticket_under_10s(offline_bug_run):
    start = time.perf_counter()
    await run_ticket("e2e-timing-001", _CANONICAL, "9422")
    assert time.perf_counter() - start < 10.0  # measures execution, not the cold import


# --- 4.4.2 literal verify (skip-guarded live integration) ------------------
@pytest.mark.skipif(
    os.getenv("ESTC_E2E_LIVE") != "1",
    reason="live e2e: needs classifier-api + seeded Postgres (9422) + Chroma; set ESTC_E2E_LIVE=1",
)
async def test_run_ticket_live_seeded_db():
    start = time.perf_counter()
    state = await run_ticket("e2e-live-001", _CANONICAL, "9422")
    assert time.perf_counter() - start < 10.0
    assert state.intent in {"billing", "bug", "feature", "lockout"}
    assert state.agent_draft_response
    assert state.confidence_score > 0
    assert state.execution_logs[-1] in {"AUTO_APPROVED", "ESCALATE"}
