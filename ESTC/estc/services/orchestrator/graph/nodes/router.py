"""`route_by_intent` conditional-edge function (Phase 4.3, task 4.3.2).

Pure, synchronous, no I/O: maps ``state.intent`` to the name of the next node.
An unrecognized or ``None`` intent falls back to ``billing_agent`` (mirroring the
classifier's own catch-all default), so the graph never dead-ends on an
unroutable state.
"""

from __future__ import annotations

from estc.shared.schemas.agent_state import AgentState

_ROUTE = {
    "billing": "billing_agent",
    "bug": "bug_agent",
    "feature": "feature_agent",
    "lockout": "lockout_agent",
}


def route_by_intent(state: AgentState) -> str:
    return _ROUTE.get(state.intent or "", "billing_agent")
