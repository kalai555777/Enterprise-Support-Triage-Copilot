"""Session-state schema + small pure helpers for the Streamlit ops center (Phase 5).

The UI keeps the operator's *view* in ``st.session_state``, keyed by ``ticket_id`` — the
inbound list, each ticket's latest ``AgentState`` (from the SSE ``done`` frame), its streamed
timeline rows, the selected ticket, and the operator name. It is per-session (per browser tab)
and non-durable; a refresh re-hydrates known ids via ``orchestrator_client.get_state``.

This module imports nothing from the backend — only ``streamlit`` + stdlib — so it is safe to
import both as ``estc.services.ui.state`` (tests) and as a flat ``state`` module (container).
"""

from __future__ import annotations

from typing import Any

import streamlit as st

# Confidence-band thresholds (spec 5.3.1): green ≥ 80, amber 60–79, red < 60.
_GREEN_MIN = 80
_AMBER_MIN = 60


def confidence_band(score: float) -> tuple[int, str]:
    """Map a 0–1 confidence to ``(percent, color)`` where color ∈ {green, orange, red}."""
    pct = round(score * 100)
    if pct >= _GREEN_MIN:
        return pct, "green"
    if pct >= _AMBER_MIN:
        return pct, "orange"
    return pct, "red"


# Canonical enterprise_customers vocab (design.md § 3) — used to recover the customer's tier /
# account status for the escalation row (5.4.2). AgentState has no dedicated fields for these,
# so we parse them defensively out of the worker draft + retrieved context, "—" when absent.
_TIERS = ("Enterprise", "Growth", "Free")
_STATUSES = ("Active", "Delinquent", "Locked")


def parse_customer_facts(state: dict[str, Any]) -> tuple[str, str]:
    """Best-effort (tier, account_status) from a ticket's draft + retrieved context."""
    haystack = (state.get("agent_draft_response") or "") + " " + " ".join(
        state.get("retrieved_context") or []
    )
    tier = next((t for t in _TIERS if t in haystack), "—")
    status = next((s for s in _STATUSES if s in haystack), "—")
    return tier, status


def init_session() -> None:
    """Seed the ``st.session_state`` keys this app relies on (idempotent)."""
    st.session_state.setdefault("operator", "operator-1")
    # tickets: { ticket_id: {company_id, text, status, state: dict|None, timeline: list, error} }
    st.session_state.setdefault("tickets", {})
    st.session_state.setdefault("selected", None)


def add_ticket(ticket_id: str, text: str, company_id: str | None) -> None:
    """Register a freshly-submitted ticket in the session view and select it."""
    st.session_state["tickets"][ticket_id] = {
        "company_id": company_id or "unknown",
        "text": text,
        "status": "pending",
        "state": None,
        "timeline": [],
        "error": None,
    }
    st.session_state["selected"] = ticket_id


def active_tickets() -> list[tuple[str, dict[str, Any]]]:
    """(id, record) pairs not yet closed — the sidebar 'Active' group."""
    return [(tid, r) for tid, r in st.session_state["tickets"].items() if r["status"] != "closed"]


def closed_tickets() -> list[tuple[str, dict[str, Any]]]:
    """(id, record) pairs the operator has approved/closed — the sidebar 'Closed' group."""
    return [(tid, r) for tid, r in st.session_state["tickets"].items() if r["status"] == "closed"]


def escalation_tickets() -> list[tuple[str, dict[str, Any]]]:
    """(id, record) pairs whose latest state requires manual verification (5.4.1)."""
    out = []
    for tid, r in st.session_state["tickets"].items():
        state = r.get("state") or {}
        if state.get("requires_escalation") and r["status"] != "closed" and not r.get("claimed"):
            out.append((tid, r))
    return out
