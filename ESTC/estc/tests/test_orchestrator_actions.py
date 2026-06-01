"""Phase 5 orchestrator operator-action tests (AC-T10).

Covers the endpoints deferred from Phase 4.6 and added for the Streamlit ops center:
``POST /tickets/{id}/approve``, ``PATCH /tickets/{id}`` (classifier re-score),
``POST /tickets/{id}/claim``, ``GET /tickets/{id}``.

Fully offline: a ticket is driven to a terminal state through the real graph using the same
``offline_bug_run`` monkeypatch trick as ``test_orchestrator_api.py`` (deterministic ``bug``
intent, stubbed retrieval, GitHub mock via conftest), and the ``PATCH`` re-score patches the
orchestrator's classifier call to an ``httpx.MockTransport`` — no classifier/Postgres/Chroma.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from estc.services.orchestrator.app import main as main_mod
from estc.services.orchestrator.app.main import app
from estc.services.orchestrator.graph.nodes import bug_agent as bug_mod
from estc.services.orchestrator.graph.nodes import classify as classify_mod

client = TestClient(app)

_CANONICAL = "I am getting a 500 error when pulling the API, my company ID is 9422"


@pytest.fixture(autouse=True)
def _reset_sse_exit_event():
    """Reset sse-starlette's cached shutdown event between TestClient loops (see API test)."""
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit = False
    AppStatus.should_exit_event = None
    yield


@pytest.fixture(autouse=True)
def _force_template_path(monkeypatch):
    """Strip LLM keys → the offline template draft runs (no cloud LLM call)."""
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    from estc.services.orchestrator.graph import llm

    llm._chat_model.cache_clear()
    yield
    llm._chat_model.cache_clear()


@pytest.fixture
def offline_bug_run(monkeypatch):
    """Real graph runs a deterministic ``bug`` ticket with zero live infra."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"intent": "bug", "confidence": 0.85, "latency_ms": 3.0})

    def _client_factory(*_args, **_kwargs) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url="http://test")

    monkeypatch.setattr(classify_mod, "httpx", SimpleNamespace(AsyncClient=_client_factory))

    async def _no_retrieval(*_args, **_kwargs):
        return []

    monkeypatch.setattr(bug_mod, "aretrieve", _no_retrieval)


def _drive(text: str = _CANONICAL, company_id: str | None = "9422") -> str:
    """POST a ticket and read its stream to completion; return the ticket_id (now terminal)."""
    body = {"text": text}
    if company_id is not None:
        body["company_id"] = company_id
    ticket_id = client.post("/tickets", json=body).json()["ticket_id"]
    resp = client.get(f"/tickets/{ticket_id}/stream")
    assert resp.status_code == 200
    return ticket_id


def _patch_classifier(monkeypatch, *, status: int = 200, confidence: float = 0.42):
    """Point the orchestrator's module-level httpx at a MockTransport for ``_classify_confidence``."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={})
        return httpx.Response(200, json={"intent": "bug", "confidence": confidence})

    def _factory(*_args, **_kwargs) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url="http://test")

    monkeypatch.setattr(
        main_mod, "httpx", SimpleNamespace(AsyncClient=_factory, HTTPError=httpx.HTTPError)
    )


# --- approve ---------------------------------------------------------------
def test_approve_closes_ticket():
    tid = client.post("/tickets", json={"text": "x"}).json()["ticket_id"]
    r = client.post(f"/tickets/{tid}/approve")
    assert r.status_code == 200 and r.json() == {"ticket_id": tid, "status": "closed"}
    assert client.post("/tickets/nope/approve").status_code == 404


# --- PATCH re-score --------------------------------------------------------
def test_patch_rescore_updates_confidence(offline_bug_run, monkeypatch):
    tid = _drive()
    _patch_classifier(monkeypatch, confidence=0.42)
    r = client.patch(f"/tickets/{tid}", json={"draft_text": "operator edited reply"})
    assert r.status_code == 200
    state = r.json()["state"]
    assert state["agent_draft_response"] == "operator edited reply"
    assert state["confidence_score"] == pytest.approx(0.42)
    assert client.patch("/tickets/nope", json={"draft_text": "x"}).status_code == 404


def test_patch_classifier_down_returns_502(offline_bug_run, monkeypatch):
    tid = _drive()
    _patch_classifier(monkeypatch, status=500)
    r = client.patch(f"/tickets/{tid}", json={"draft_text": "edited"})
    assert r.status_code == 502


# --- claim -----------------------------------------------------------------
def test_claim_appends_operator_log(offline_bug_run):
    tid = _drive()
    r = client.post(f"/tickets/{tid}/claim", json={"operator": "ana"})
    assert r.status_code == 200
    logs = r.json()["state"]["execution_logs"]
    assert logs[-1] == "CLAIMED_BY:ana"
    assert client.post("/tickets/nope/claim", json={"operator": "ana"}).status_code == 404


# --- get -------------------------------------------------------------------
def test_get_ticket_returns_state(offline_bug_run):
    tid = _drive()
    r = client.get(f"/tickets/{tid}")
    assert r.status_code == 200
    body = r.json()
    assert body["ticket_id"] == tid
    assert body["state"]["intent"] == "bug"
    assert body["state"]["agent_draft_response"]
    assert client.get("/tickets/nope").status_code == 404
