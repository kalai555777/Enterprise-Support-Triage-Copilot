"""Orchestrator FastAPI app (Phase 4.6, tasks 4.6.1).

Wraps the Phase 4.4 LangGraph engine in an HTTP/SSE skin on port 8002:

- ``POST /tickets``                 — register a ticket, return a ``ticket_id`` (no run yet)
- ``GET  /tickets/{id}/stream``     — drive the graph, emit one SSE event per node
                                      transition, then a terminal ``done`` event
- ``GET  /healthz``                 — dependency-free liveness probe

The stream **reuses ``astream_ticket`` verbatim** (the single streaming code path
established in Phase 4.4) rather than re-implementing ``graph.astream`` — so what the SSE
client sees is exactly what ``run_ticket`` sees. The graph runs lazily when the stream is
opened (``POST`` only registers the ticket), which maps one HTTP request to one graph run.

This module adds *only* an HTTP boundary — no node, edge, or graph change. State between
``POST`` and the stream lives in an in-process registry, mirroring the scope of the graph's
in-process ``MemorySaver`` (both are non-durable, single-worker — see the spec § 2).
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from sse_starlette.sse import EventSourceResponse

from estc.services.orchestrator.app.schemas import CreateTicketRequest, CreateTicketResponse
from estc.services.orchestrator.graph.build import astream_ticket, graph
from estc.services.orchestrator.graph.observability import configure_tracing
from estc.shared.schemas.agent_state import AgentState


@dataclass
class TicketRecord:
    """One submitted ticket awaiting / running through the graph.

    ``status`` advances ``pending -> running -> done | error``. The record is the bridge
    between ``POST /tickets`` (which stores it) and the stream ``GET`` (which runs it).
    """

    text: str
    company_id: str
    status: str = "pending"


# Process-lifetime registry, keyed by ticket_id. Non-durable by design (same scope as the
# graph's MemorySaver); a fresh POST always mints a new uuid, so ids never collide.
_TICKETS: dict[str, TicketRecord] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Enable LangSmith tracing once at startup. Idempotent; off without a key (never raises)."""
    configure_tracing()
    yield


app = FastAPI(title="ESTC Orchestrator", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe — no dependency calls, no model load, so it answers before warmup."""
    return {"status": "ok"}


@app.post("/tickets", response_model=CreateTicketResponse, status_code=201)
async def create_ticket(req: CreateTicketRequest) -> CreateTicketResponse:
    """Register a ticket and return its id. The graph is *not* run here — open the stream."""
    ticket_id = uuid.uuid4().hex
    _TICKETS[ticket_id] = TicketRecord(text=req.text, company_id=req.company_id or "unknown")
    return CreateTicketResponse(ticket_id=ticket_id, status="pending")


def _final_state(ticket_id: str) -> dict[str, Any]:
    """Read the fully-merged terminal state from the checkpointer, keyed by thread_id.

    Normalizes the Pydantic-state shape (LangGraph may surface an ``AgentState`` instance
    or a field-keyed dict) — the same guard ``run_ticket`` uses.
    """
    values = graph.get_state({"configurable": {"thread_id": ticket_id}}).values
    final = values if isinstance(values, AgentState) else AgentState(**values)
    return final.model_dump()


def _sse(event: str, payload: dict[str, Any]) -> dict[str, str]:
    """Format one SSE frame. ``default=str`` coerces any non-JSON-native stray value."""
    return {"event": event, "data": json.dumps(payload, default=str)}


@app.get("/tickets/{ticket_id}/stream")
async def stream_ticket(ticket_id: str) -> EventSourceResponse:
    """Drive the registered ticket through the graph, emitting one event per node transition.

    Yields: ``open`` → ``node`` (one per transition: classify, one worker, supervisor_review)
    → ``done`` (the merged final ``AgentState``). A normal run is ≥ 4 ``data:`` frames. On a
    node failure, an ``error`` frame closes the stream cleanly. Re-opening a finished ticket
    replays a single ``done`` frame (the graph is not re-run).
    """
    rec = _TICKETS.get(ticket_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="unknown ticket_id")

    async def event_gen() -> AsyncIterator[dict[str, str]]:
        yield _sse("open", {"ticket_id": ticket_id, "status": "running"})

        # Re-open of a completed ticket: replay the terminal state, don't re-run the graph.
        if rec.status == "done":
            yield _sse("done", {"event": "done", "ticket_id": ticket_id, "state": _final_state(ticket_id)})
            return

        rec.status = "running"
        try:
            async for node_name, update in astream_ticket(ticket_id, rec.text, rec.company_id):
                yield _sse(
                    "node",
                    {"event": "node", "node": node_name, "ticket_id": ticket_id, "update": update},
                )
            rec.status = "done"
            yield _sse("done", {"event": "done", "ticket_id": ticket_id, "state": _final_state(ticket_id)})
        except Exception as exc:  # a node raised (classifier 5xx, MCP/DB error) — surface in-band
            rec.status = "error"
            yield _sse("error", {"event": "error", "ticket_id": ticket_id, "error": str(exc)})

    return EventSourceResponse(event_gen(), headers={"X-Accel-Buffering": "no"})
