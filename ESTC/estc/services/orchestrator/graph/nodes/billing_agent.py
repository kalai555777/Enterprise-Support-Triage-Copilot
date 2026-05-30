"""`billing_agent` node (Phase 4.3, task 4.3.3).

Pulls subscription status from the Postgres MCP (read-only tool), retrieves billing
knowledge-base context, and drafts a reply that surfaces the customer's tier. The
draft is grounded in ``retrieved_context`` (design.md Component C). Tolerates an
unknown ``company_id`` (MCP returns ``None``) by drafting with no account facts.
"""

from __future__ import annotations

from estc.services.mcp_postgres.server import get_subscription_status
from estc.services.orchestrator.graph.llm import draft_reply
from estc.services.orchestrator.rag.retriever import KBIndex, aretrieve
from estc.shared.schemas.agent_state import AgentState


async def billing_agent(state: AgentState) -> dict[str, object]:
    sub = await get_subscription_status(state.company_id)
    hits = await aretrieve(state.raw_issue_text, index=KBIndex.BILLING)
    context = [h.content for h in hits]
    facts = {"tier": sub.subscription_tier} if sub else {}
    draft, confidence = await draft_reply(
        intent="billing",
        issue_text=state.raw_issue_text,
        context=context,
        facts=facts,
    )
    return {
        "retrieved_context": context,
        "agent_draft_response": draft,
        "confidence_score": confidence,
        "execution_logs": state.execution_logs + ["billing_drafted"],
    }
