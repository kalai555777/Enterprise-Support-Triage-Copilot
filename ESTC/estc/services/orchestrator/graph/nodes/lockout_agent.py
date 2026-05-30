"""`lockout_agent` node (Phase 4.3, task 4.3.6).

Account-lockout / security path. Pulls the full customer record from the Postgres
MCP, drafts an identity-verification explainer, and sets ``requires_escalation``
to ``True`` UNCONDITIONALLY (regardless of confidence) — lockouts always require a
human verification step before any auto-approval. Tolerates an unknown company id.
"""

from __future__ import annotations

from estc.services.mcp_postgres.server import get_customer_by_id
from estc.services.orchestrator.graph.llm import draft_reply
from estc.shared.schemas.agent_state import AgentState


async def lockout_agent(state: AgentState) -> dict[str, object]:
    rec = await get_customer_by_id(state.company_id)
    facts = (
        {"company": rec.company_name, "account_status": rec.account_status} if rec else {}
    )
    draft, confidence = await draft_reply(
        intent="lockout",
        issue_text=state.raw_issue_text,
        context=[],
        facts=facts,
    )
    return {
        "agent_draft_response": draft,
        "confidence_score": max(0.0, confidence),
        "requires_escalation": True,
        "execution_logs": state.execution_logs + ["lockout_escalated"],
    }
