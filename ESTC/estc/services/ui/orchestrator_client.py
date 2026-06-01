"""Thin HTTP/SSE client for the orchestrator-app (Phase 5).

The Streamlit UI talks to the orchestrator **only** over this module — it never imports any
``estc.*`` backend type, so the contract is the HTTP/JSON wire shape (keeping the ui-client
image lean and torch-free). Calls are **synchronous** ``httpx`` to fit Streamlit's sync script
thread; ``stream_ticket`` consumes the Phase 4.6 SSE feed via ``httpx-sse`` and yields one
parsed frame per event.

``ORCHESTRATOR_URL`` resolves to ``http://orchestrator-app:8002`` inside the compose network
and ``http://localhost:8002`` for a host-local ``streamlit run``.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterator

import httpx
from httpx_sse import connect_sse

BASE = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8002")

# Short connect timeout; reads are bounded per call (the stream uses its own read timeout).
_TIMEOUT = httpx.Timeout(5.0, read=30.0)


def create_ticket(text: str, company_id: str | None) -> dict[str, Any]:
    """``POST /tickets`` — register a ticket, return ``{ticket_id, status}``."""
    r = httpx.post(f"{BASE}/tickets", json={"text": text, "company_id": company_id}, timeout=10.0)
    r.raise_for_status()
    return r.json()


def stream_ticket(ticket_id: str, read_timeout: float = 30.0) -> Iterator[dict[str, Any]]:
    """Drive the ticket's SSE stream, yielding parsed frames in order.

    Each yielded dict is ``{"event": <open|node|done|error>, **payload}``. A normal run is
    ``open → node(classify) → node(worker) → node(supervisor_review) → done`` (≥ 4 frames).
    """
    timeout = httpx.Timeout(5.0, read=read_timeout)
    with httpx.Client(timeout=timeout) as client:
        with connect_sse(client, "GET", f"{BASE}/tickets/{ticket_id}/stream") as event_source:
            for sse in event_source.iter_sse():
                payload = json.loads(sse.data) if sse.data else {}
                yield {"event": sse.event, **payload}


def get_state(ticket_id: str) -> dict[str, Any]:
    """``GET /tickets/{id}`` — current ``{ticket_id, status, state}`` for UI re-hydration."""
    r = httpx.get(f"{BASE}/tickets/{ticket_id}", timeout=10.0)
    r.raise_for_status()
    return r.json()


def approve(ticket_id: str) -> dict[str, Any]:
    """``POST /tickets/{id}/approve`` — close the ticket; returns ``{ticket_id, status}``."""
    r = httpx.post(f"{BASE}/tickets/{ticket_id}/approve", timeout=10.0)
    r.raise_for_status()
    return r.json()


def modify(ticket_id: str, draft_text: str) -> dict[str, Any]:
    """``PATCH /tickets/{id}`` — persist the edited draft, return the re-scored state."""
    r = httpx.patch(f"{BASE}/tickets/{ticket_id}", json={"draft_text": draft_text}, timeout=10.0)
    r.raise_for_status()
    return r.json()


def claim(ticket_id: str, operator: str) -> dict[str, Any]:
    """``POST /tickets/{id}/claim`` — append ``CLAIMED_BY:<operator>`` to the logs."""
    r = httpx.post(f"{BASE}/tickets/{ticket_id}/claim", json={"operator": operator}, timeout=10.0)
    r.raise_for_status()
    return r.json()
