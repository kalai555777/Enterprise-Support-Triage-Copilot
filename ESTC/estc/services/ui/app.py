"""ESTC Support Specialist Operations Center (Phase 5 — FR-4).

A Streamlit dashboard over the orchestrator-app: submit tickets, watch the real-time agent map,
read the draft + confidence, Approve / Modify, and work the escalation queue. Layout:

  Sidebar  — "Inbound Tickets" + a mock ingestion form (5.1)
  Column 1 — "AI Analysis"      : real-time agent map (5.2)
  Column 2 — "Draft"            : draft + confidence badge + Approve / Modify (5.3)
  Column 3 — "Escalation Queue" : "Requires Manual Verification" + Claim (5.4)

The UI talks to the backend only over ``orchestrator_client`` (HTTP/SSE). Imports are dual-path:
qualified ``estc.services.ui.*`` under tests/AppTest (repo root on path) and flat module names
inside the container image (where the ui/ dir is the working directory).
"""

from __future__ import annotations

try:  # qualified import wins under pytest/AppTest; flat in the container
    from estc.services.ui import orchestrator_client as oc, state as ui_state
    from estc.services.ui.components import agent_map, draft_panel, escalation_queue
except ModuleNotFoundError:  # pragma: no cover - container path
    import orchestrator_client as oc  # type: ignore[no-redefine]
    import state as ui_state  # type: ignore[no-redefine]
    from components import agent_map, draft_panel, escalation_queue  # type: ignore[no-redefine]

import streamlit as st

st.set_page_config(page_title="ESTC Operations Center", layout="wide")
ui_state.init_session()

st.title("🛠️ Support Specialist Operations Center")


def _sidebar() -> None:
    with st.sidebar:
        st.header("Inbound Tickets")

        with st.form("ingest", clear_on_submit=True):
            text = st.text_area("Issue text", placeholder="Describe the customer's issue…")
            company_id = st.text_input("Company ID", placeholder="e.g. 9422")
            submitted = st.form_submit_button("Submit")
        if submitted and text.strip():
            try:
                resp = oc.create_ticket(text, company_id or None)
                ui_state.add_ticket(resp["ticket_id"], text, company_id or None)
                st.toast(f"Ticket {resp['ticket_id']} created")
            except Exception as exc:
                st.error(f"Could not submit ticket: {exc}")

        st.divider()
        active = ui_state.active_tickets()
        closed = ui_state.closed_tickets()

        st.subheader("Active")
        if not active:
            st.caption("No active tickets.")
        for tid, rec in active:
            if st.button(f"📨 {tid[:8]} · {rec['company_id']}", key=f"sel_{tid}"):
                st.session_state["selected"] = tid

        st.subheader("Closed")
        if not closed:
            st.caption("No closed tickets.")
        for tid, rec in closed:
            st.markdown(f"✅ `{tid[:8]}` · {rec['company_id']}")


_sidebar()

col1, col2, col3 = st.columns(3)
selected = st.session_state.get("selected")

with col1:
    st.subheader("AI Analysis")
    if selected:
        agent_map.render(selected)
    else:
        st.info("Submit or select a ticket to begin.")

with col2:
    st.subheader("Draft")
    if selected:
        draft_panel.render(selected)
    else:
        st.info("No ticket selected.")

with col3:
    st.subheader("Escalation Queue")
    escalation_queue.render()
