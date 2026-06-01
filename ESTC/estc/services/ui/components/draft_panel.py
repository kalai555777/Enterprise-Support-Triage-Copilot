"""Draft panel (Phase 5, tasks 5.3.1 / 5.3.2 / 5.3.3).

Renders the agent's drafted reply in the 'Draft' column with a colored confidence badge, an
**Approve Draft** button (closes the ticket), and a **Modify & Override** flow that PATCHes the
edited text and re-renders the re-scored confidence. The Approve control is shown **only** for
non-escalation tickets, so an escalation can never be auto-approved (5.4.1 invariant).
"""

from __future__ import annotations

import streamlit as st

try:  # qualified import wins under pytest/AppTest; flat in the container
    from estc.services.ui import orchestrator_client as oc, state as ui_state
except ModuleNotFoundError:  # pragma: no cover - container path
    import orchestrator_client as oc  # type: ignore[no-redefine]
    import state as ui_state  # type: ignore[no-redefine]


def _badge(score: float) -> str:
    pct, color = ui_state.confidence_band(score)
    return (
        f"<span style='background:{color};color:white;padding:3px 10px;"
        f"border-radius:12px;font-weight:600'>Confidence: {pct}%</span>"
    )


def render(ticket_id: str) -> None:
    """Render the draft + confidence badge + Approve / Modify controls for one ticket."""
    rec = st.session_state["tickets"].get(ticket_id)
    if rec is None:
        st.info("No ticket selected.")
        return

    state = rec.get("state")
    if not state:
        st.info("Draft will appear once the agent run completes.")
        return

    st.markdown(_badge(float(state.get("confidence_score", 0.0))), unsafe_allow_html=True)
    st.code(state.get("agent_draft_response") or "(empty draft)", language="markdown")

    if rec["status"] == "closed":
        st.success("✓ Approved and closed.")
        return

    escalate = bool(state.get("requires_escalation"))
    if escalate:
        st.warning("Escalation required — approve is disabled; work this in the queue →")
    else:
        # Approve only offered for non-escalation tickets (auto-approval gate, ec.5).
        if st.button("✅ Approve Draft", key=f"approve_{ticket_id}"):
            try:
                oc.approve(ticket_id)
                rec["status"] = "closed"
                st.toast(f"Ticket {ticket_id} approved & closed")
                st.rerun()
            except Exception as exc:
                st.error(f"Approve failed: {exc}")

    with st.expander("✏️ Modify & Override"):
        edited = st.text_area(
            "Edited draft",
            value=state.get("agent_draft_response") or "",
            key=f"edit_{ticket_id}",
        )
        if st.button("Save & re-evaluate", key=f"save_{ticket_id}"):
            try:
                resp = oc.modify(ticket_id, edited)
                rec["state"] = resp.get("state", rec["state"])
                st.toast("Draft updated; confidence re-evaluated")
                st.rerun()
            except Exception as exc:
                st.warning(f"Could not re-evaluate (kept prior draft): {exc}")
