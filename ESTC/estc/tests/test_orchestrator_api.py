"""Phase 4.6 orchestrator FastAPI tests (tasks 4.6.1; AC-T1 .. AC-T7).

Fully offline: the real module-level ``graph`` is driven through the HTTP/SSE app, but the
``offline_bug_run`` fixture (ported from ``test_graph_build.py``) monkeypatches
``classify.httpx`` to a deterministic ``bug`` intent and stubs ``bug_agent.aretrieve``, so
no classifier, Postgres, or Chroma is needed. The GitHub MCP stays in file-backed mock mode
(forced by ``conftest.py``). The stream is finite, so ``TestClient.get`` reads it to EOF.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from estc.services.orchestrator.app.main import app
from estc.services.orchestrator.graph.nodes import bug_agent as bug_mod
from estc.services.orchestrator.graph.nodes import classify as classify_mod

client = TestClient(app)

_CANONICAL = "I am getting a 500 error when pulling the API, my company ID is 9422"


@pytest.fixture(autouse=True)
def _reset_sse_exit_event():
    """sse-starlette caches a module-global ``anyio.Event`` for graceful shutdown. TestClient
    spins a fresh event loop per request, so that cached event binds to a stale loop and the
    second streaming test errors. Reset it to None before each test → it re-creates lazily on
    the current loop (sse.py:194). (Plan Risk #1 — TestClient + async.)"""
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit = False
    AppStatus.should_exit_event = None
    yield


@pytest.fixture(autouse=True)
def _force_template_path(monkeypatch):
    """Strip LLM keys and reset the cached chat model so the offline template draft runs."""
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    from estc.services.orchestrator.graph import llm

    llm._chat_model.cache_clear()
    yield
    llm._chat_model.cache_clear()


@pytest.fixture
def offline_bug_run(monkeypatch):
    """Make the real graph run a deterministic ``bug`` ticket with zero live infra:
    classify -> {"intent":"bug"} via an httpx.MockTransport; bug_agent.aretrieve -> []."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"intent": "bug", "confidence": 0.85, "latency_ms": 3.0})

    def _client_factory(*_args, **_kwargs) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url="http://test")

    monkeypatch.setattr(classify_mod, "httpx", SimpleNamespace(AsyncClient=_client_factory))

    async def _no_retrieval(*_args, **_kwargs):
        return []

    monkeypatch.setattr(bug_mod, "aretrieve", _no_retrieval)


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into ``(event_name, data_json)`` pairs (one per ``data:`` frame)."""
    events: list[tuple[str, dict]] = []
    event = "message"
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            events.append((event, json.loads(line[len("data:"):].strip())))
        elif line == "":
            event = "message"
    return events


def _run_stream(text: str = _CANONICAL, company_id: str | None = "9422") -> list[tuple[str, dict]]:
    """POST a ticket, open its stream, return the parsed SSE events."""
    body = {"text": text}
    if company_id is not None:
        body["company_id"] = company_id
    ticket_id = client.post("/tickets", json=body).json()["ticket_id"]
    resp = client.get(f"/tickets/{ticket_id}/stream")
    assert resp.status_code == 200
    return _parse_sse(resp.text)


# --- AC-T5 -----------------------------------------------------------------
def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# --- AC-T1 -----------------------------------------------------------------
def test_create_ticket_returns_id():
    r = client.post("/tickets", json={"text": "x", "company_id": "9422"})
    assert r.status_code == 201
    body = r.json()
    assert body["ticket_id"] and body["status"] == "pending"


def test_create_ticket_optional_company():
    r = client.post("/tickets", json={"text": "no company id supplied"})
    assert r.status_code == 201
    assert r.json()["ticket_id"]


# --- AC-T2 (the literal 4.6.1 verify) --------------------------------------
def test_stream_emits_min_four_events(offline_bug_run):
    events = _run_stream()
    assert len(events) >= 4, f"expected >= 4 data frames, got {len(events)}"


# --- AC-T3 / AC-T4 ---------------------------------------------------------
def test_stream_node_order_and_done(offline_bug_run):
    events = _run_stream()
    node_names = [d["node"] for ev, d in events if ev == "node"]
    assert node_names == ["classify", "bug_agent", "supervisor_review"]
    done = [d for ev, d in events if ev == "done"]
    assert len(done) == 1
    state = done[0]["state"]
    assert state["intent"] == "bug"
    assert state["agent_draft_response"]
    assert state["confidence_score"] > 0


# --- AC-T6 -----------------------------------------------------------------
def test_stream_unknown_ticket_404():
    assert client.get("/tickets/does-not-exist/stream").status_code == 404
