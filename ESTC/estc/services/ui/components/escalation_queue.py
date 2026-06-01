"""Escalation queue (Phase 5, tasks 5.4.1 / 5.4.2).

The 'Escalation Queue' column lists every session ticket whose latest state has
``requires_escalation == True`` under 'Requires Manual Verification'. Each row shows the
customer's subscription tier + account status (parsed from the draft/context) and a **Claim**
button that assigns the ticket to the current operator — appending ``CLAIMED_BY:<operator>`` to
the run's ``execution_logs`` — and then drops the row from the queue.
"""

from __future__ import annotations

import streamlit as st

try:  # qualified import wins under pytest/AppTest; flat in the container
    from estc.services.ui import orchestrator_client as oc, state as ui_state
except ModuleNotFoundError:  # pragma: no cover - container path
    import orchestrator_client as oc  # type: ignore[no-redefine]
    import state as ui_state  # type: ignore[no-redefine]


def render() -> None:
    """Render the 'Requires Manual Verification' queue."""
    st.markdown("**Requires Manual Verification**")
    rows = ui_state.escalation_tickets()
    if not rows:
        st.caption("No tickets awaiting manual verification.")
        return

    operator = st.session_state.get("operator", "operator-1")
    for ticket_id, rec in rows:
        tier, status = ui_state.parse_customer_facts(rec.get("state") or {})
        with st.container(border=True):
            st.markdown(f"`{ticket_id[:8]}` · company **{rec['company_id']}**")
            st.caption(f"Tier: {tier}  ·  Account: {status}")
            if st.button("Claim", key=f"claim_{ticket_id}"):
                try:
                    oc.claim(ticket_id, operator)
                    rec["claimed"] = True
                    st.toast(f"{operator} claimed {ticket_id[:8]}")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Claim failed: {exc}")
