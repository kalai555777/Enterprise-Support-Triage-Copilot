"""Phase 4.4 graph wiring (tasks 4.4.1-4.4.2).

Assembles the six triage nodes into a single ``StateGraph(AgentState)``:

    START -> classify -> route_by_intent -> {billing | bug | feature | lockout}
          -> supervisor_review -> END

``classify`` fans out through the ``route_by_intent`` conditional edge (4.3.2); every
worker funnels back into the single ``supervisor_review`` compliance gate (4.3.7).
The compiled graph carries a ``MemorySaver`` checkpointer so a run can be resumed /
inspected by ``thread_id`` (the ticket id). ``run_ticket`` is the async entrypoint that
drives a ticket through the graph while streaming one event per node transition.

This module adds *only* edges + the entrypoint — no node body or signature changes.
In particular it deliberately does NOT add an ``execution_logs`` reducer: the per-run
path is strictly linear (classify -> one worker -> supervisor_review), so the Phase 4.3
"return the full extended list" convention accumulates correctly; a reducer would
double-append.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from estc.services.orchestrator.graph.nodes.billing_agent import billing_agent
from estc.services.orchestrator.graph.nodes.bug_agent import bug_agent
from estc.services.orchestrator.graph.nodes.classify import classify
from estc.services.orchestrator.graph.nodes.feature_agent import feature_agent
from estc.services.orchestrator.graph.nodes.lockout_agent import lockout_agent
from estc.services.orchestrator.graph.nodes.router import route_by_intent
from estc.services.orchestrator.graph.nodes.supervisor import supervisor_review
from estc.shared.schemas.agent_state import AgentState

# Worker nodes reachable from classify; each routes back into the single supervisor gate.
_WORKERS = ("billing_agent", "bug_agent", "feature_agent", "lockout_agent")


def build_graph(checkpointer: Optional[BaseCheckpointSaver] = None) -> Any:
    """Wire and compile the triage state machine.

    ``route_by_intent`` already returns the worker node name verbatim, so the
    conditional-edge mapping is the identity over ``_WORKERS`` — passing it explicitly
    keeps the rendered Mermaid readable and documents the only legal dispatch targets.
    Pass a ``checkpointer`` to inject an alternative saver; ``None`` uses a fresh
    ``MemorySaver``.
    """
    builder = StateGraph(AgentState)

    builder.add_node("classify", classify)
    builder.add_node("billing_agent", billing_agent)
    builder.add_node("bug_agent", bug_agent)
    builder.add_node("feature_agent", feature_agent)
    builder.add_node("lockout_agent", lockout_agent)
    builder.add_node("supervisor_review", supervisor_review)

    builder.add_edge(START, "classify")
    builder.add_conditional_edges("classify", route_by_intent, {n: n for n in _WORKERS})
    for worker in _WORKERS:
        builder.add_edge(worker, "supervisor_review")
    builder.add_edge("supervisor_review", END)

    return builder.compile(checkpointer=checkpointer or MemorySaver())


# Module-level compiled graph (the 4.4.1 verify target). One MemorySaver backs every
# run; runs are isolated by the thread_id supplied in the run config.
graph = build_graph()


async def astream_ticket(
    ticket_id: str,
    text: str,
    company_id: str,
    *,
    config: Optional[dict[str, Any]] = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Drive one ticket through the graph, yielding ``(node_name, state_update)`` per
    node transition. This is the per-event feed the Phase 4.6 SSE endpoint will consume.
    """
    initial = AgentState(ticket_id=ticket_id, raw_issue_text=text, company_id=company_id)
    cfg = config or {"configurable": {"thread_id": ticket_id}}
    async for chunk in graph.astream(initial, config=cfg, stream_mode="updates"):
        for node_name, update in chunk.items():
            yield node_name, (update or {})


async def run_ticket(ticket_id: str, text: str, company_id: str) -> AgentState:
    """Async entrypoint (task 4.4.2): run a ticket end-to-end and return the final
    ``AgentState``. Consumes the node-event stream (so the result path and the SSE path
    share code), then reads the fully-merged state back from the checkpointer by
    ``thread_id`` — not the last streamed delta, which would be only the final node's
    partial update.
    """
    cfg = {"configurable": {"thread_id": ticket_id}}
    async for _node, _update in astream_ticket(ticket_id, text, company_id, config=cfg):
        pass
    snapshot = graph.get_state(cfg)
    values = snapshot.values
    # Pydantic-state graphs may surface either the model instance or a field-keyed dict.
    return values if isinstance(values, AgentState) else AgentState(**values)
