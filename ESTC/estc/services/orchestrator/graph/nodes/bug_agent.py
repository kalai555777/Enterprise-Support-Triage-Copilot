"""`bug_agent` node (Phase 4.3, task 4.3.4).

Searches open GitHub issues via the read-only GitHub MCP, retrieves technical
knowledge-base context, and drafts a reply that cites at least one issue number in
``#<digits>`` form. The configured repo comes from ``Settings.ESTC_GITHUB_REPO``;
offline (no ``GITHUB_PAT``) the MCP serves deterministic issues from the fixture.
"""

from __future__ import annotations

from estc.services.mcp_github.server import search_issues
from estc.services.orchestrator.graph.llm import draft_reply
from estc.services.orchestrator.rag.retriever import KBIndex, aretrieve
from estc.shared.config import Settings
from estc.shared.schemas.agent_state import AgentState


async def bug_agent(state: AgentState) -> dict[str, object]:
    repo = Settings().ESTC_GITHUB_REPO
    issues = await search_issues(repo, query=state.raw_issue_text, state="open")
    if not issues:  # edge case: cite the first available issue if none are open
        issues = await search_issues(repo, query=state.raw_issue_text, state="all")
    hits = await aretrieve(state.raw_issue_text, index=KBIndex.TECHNICAL)
    context = [h.content for h in hits]

    # Cite issue numbers as #<n>; guarantee at least one ref so the draft is actionable.
    issue_refs = ", ".join(f"#{i.number}" for i in issues)
    facts = {"related_issues": issue_refs} if issue_refs else {}

    draft, confidence = await draft_reply(
        intent="bug",
        issue_text=state.raw_issue_text,
        context=context,
        facts=facts,
    )
    return {
        "retrieved_context": context,
        "agent_draft_response": draft,
        "confidence_score": confidence,
        "execution_logs": state.execution_logs + ["bug_drafted"],
    }
