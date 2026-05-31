"""Request/response models for the orchestrator FastAPI app (Phase 4.6, task 4.6.1).

``company_id`` is optional: the canonical exit-gate ticket (4.7.1) POSTs ``text`` only,
and its ``bug`` path resolves context from GitHub, not Postgres — so a missing company
id never blocks a run. The handler substitutes ``"unknown"`` when it is omitted.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CreateTicketRequest(BaseModel):
    """Body of ``POST /tickets``."""

    text: str
    company_id: Optional[str] = None


class CreateTicketResponse(BaseModel):
    """Reply to ``POST /tickets`` — the id to open the SSE stream with."""

    ticket_id: str
    status: str
