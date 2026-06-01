"""Request/response models for the orchestrator FastAPI app (Phase 4.6, task 4.6.1).

``company_id`` is optional: the canonical exit-gate ticket (4.7.1) POSTs ``text`` only,
and its ``bug`` path resolves context from GitHub, not Postgres — so a missing company
id never blocks a run. The handler substitutes ``"unknown"`` when it is omitted.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class CreateTicketRequest(BaseModel):
    """Body of ``POST /tickets``."""

    text: str
    company_id: Optional[str] = None


class CreateTicketResponse(BaseModel):
    """Reply to ``POST /tickets`` — the id to open the SSE stream with."""

    ticket_id: str
    status: str


# --- Operator-action models (Phase 5; the controls deferred from Phase 4.6) ---------------
# These back the Streamlit draft panel (5.3) and escalation queue (5.4). Each acts on the
# existing in-process registry + MemorySaver; none triggers a graph re-run.


class ModifyDraftRequest(BaseModel):
    """Body of ``PATCH /tickets/{id}`` — the operator's edited draft (5.3.3)."""

    draft_text: str


class ClaimRequest(BaseModel):
    """Body of ``POST /tickets/{id}/claim`` — the operator claiming an escalation (5.4.2)."""

    operator: str


class ApproveResponse(BaseModel):
    """Reply to ``POST /tickets/{id}/approve`` — the ticket is closed (5.3.2)."""

    ticket_id: str
    status: str  # "closed"


class TicketStateResponse(BaseModel):
    """Current registry status + merged ``AgentState`` for a ticket.

    Returned by ``GET /tickets/{id}``, ``PATCH /tickets/{id}``, and the claim endpoint so the
    UI can re-hydrate (after a refresh) or re-render the draft/confidence after an action.
    """

    ticket_id: str
    status: str  # pending | running | done | closed | error
    state: dict[str, Any]  # AgentState.model_dump()
