"""`feature_agent` node (Phase 4.3, task 4.3.5).

Retrieves context from BOTH knowledge-base indices, drafts an acknowledgement, and
records an internal-only synthetic ticket. There is NO MCP write — the servers are
read-only (design.md Component B); the "ticket" is purely a breadcrumb appended to
``execution_logs`` (``feature_logged`` + an internal synthetic id).
"""

from __future__ import annotations

from uuid import uuid4

from estc.services.orchestrator.graph.llm import draft_reply
from estc.services.orchestrator.rag.retriever import KBIndex, aretrieve
from estc.shared.schemas.agent_state import AgentState


async def feature_agent(state: AgentState) -> dict[str, object]:
    billing_hits = await aretrieve(state.raw_issue_text, index=KBIndex.BILLING)
    tech_hits = await aretrieve(state.raw_issue_text, index=KBIndex.TECHNICAL)
    context = [h.content for h in billing_hits] + [h.content for h in tech_hits]

    draft, confidence = await draft_reply(
        intent="feature",
        issue_text=state.raw_issue_text,
        context=context,
        facts={},
    )
    synthetic_ticket_id = f"feature_ticket:{uuid4()}"
    return {
        "retrieved_context": context,
        "agent_draft_response": draft,
        "confidence_score": confidence,
        "execution_logs": state.execution_logs + ["feature_logged", synthetic_ticket_id],
    }
