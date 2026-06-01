"""Real-time agent map (Phase 5, tasks 5.2.1 / 5.2.2).

Renders the live triage timeline in the 'AI Analysis' column. A selected pending ticket opens
the orchestrator SSE stream and the timeline advances one row per ``node`` event
(``classify → worker → supervisor_review``); each row shows a status icon and the per-node
duration in **milliseconds**, measured client-side as the wall-clock delta between received
events (the SSE feed carries no server timing — adding it would change the graph). On the
``done`` frame the merged ``AgentState`` is cached into session so an outer rerun never
re-streams; on ``error`` the in-progress row is marked failed.

The streaming region is an ``st.fragment`` so it reruns in isolation from the rest of the page.
"""

from __future__ import annotations

import time
from typing import Any

import streamlit as st

try:  # qualified import wins under pytest/AppTest (repo root on path); flat in the container
    from estc.services.ui import orchestrator_client as oc
except ModuleNotFoundError:  # pragma: no cover - container path
    import orchestrator_client as oc  # type: ignore[no-redefine]

_ICON = {"pending": "⏳", "done": "✅", "failed": "❌"}


def _row_text(row: dict[str, Any]) -> str:
    icon = _ICON.get(row["status"], "•")
    ms = f"{row['ms']} ms" if row.get("ms") is not None else "—"
    return f"{icon}  **{row['node']}**  ·  {ms}"


def _hydrate_from_logs(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a retrospective timeline (no live timing) from a finished run's execution_logs."""
    logs = (rec.get("state") or {}).get("execution_logs", [])
    nodes: list[str] = []
    for entry in logs:
        if entry.startswith("classified:"):
            nodes.append("classify")
        elif entry.endswith("_drafted"):
            nodes.append(entry.replace("_drafted", "_agent"))
        elif entry in ("AUTO_APPROVED", "ESCALATE"):
            nodes.append("supervisor_review")
    return [{"node": n, "status": "done", "ms": None} for n in nodes]


@st.fragment
def render(ticket_id: str) -> None:
    """Render (and, if pending, drive) the agent timeline for one ticket."""
    rec = st.session_state["tickets"].get(ticket_id)
    if rec is None:
        st.info("Select a ticket to see its agent map.")
        return

    # Finished/closed ticket: show the cached or log-derived timeline, no re-stream (ec.1).
    if rec["status"] in ("done", "closed", "error"):
        rows = rec.get("timeline") or _hydrate_from_logs(rec)
        for row in rows:
            st.markdown(_row_text(row))
        if rec["status"] == "error" and rec.get("error"):
            st.error(f"Run failed: {rec['error']}")
        return

    # Pending: open the SSE stream and advance the timeline live.
    rows: list[dict[str, Any]] = []
    placeholder = st.container()
    t_prev = time.perf_counter()
    try:
        for frame in oc.stream_ticket(ticket_id):
            event = frame.get("event")
            now = time.perf_counter()
            if event == "node":
                ms = round((now - t_prev) * 1000)
                rows.append({"node": frame["node"], "status": "done", "ms": ms})
                t_prev = now
                with placeholder:
                    for row in rows:
                        st.markdown(_row_text(row))
            elif event == "done":
                rec["state"] = frame.get("state")
                rec["status"] = "done"
                rec["timeline"] = rows
            elif event == "error":
                if rows:
                    rows[-1]["status"] = "failed"
                rec["status"] = "error"
                rec["error"] = frame.get("error")
                rec["timeline"] = rows
    except Exception as exc:  # transport failure mid-stream (orchestrator down / read timeout)
        rec["status"] = "error"
        rec["error"] = str(exc)
        st.warning(f"Stream interrupted: {exc}")
