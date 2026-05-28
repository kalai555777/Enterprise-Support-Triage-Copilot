from typing import List, Optional

from pydantic import BaseModel, Field


class AgentState(BaseModel):
    """Shared LangGraph state threaded through every node of the triage graph.

    Mirrors docs/design.md section 3 exactly. The first three fields are the
    required ticket identity supplied at graph entry (run_ticket, task 4.4.2);
    the rest are populated as the graph advances through
    classify -> router -> {billing | bug | feature | lockout} -> supervisor_review.
    """

    # --- Required ticket identity (graph inputs) ---
    ticket_id: str
    raw_issue_text: str
    company_id: str

    # --- Populated by classify / router (4.3.1-4.3.2) ---
    intent: Optional[str] = None

    # --- Populated by the worker agent nodes (4.3.3-4.3.6) ---
    retrieved_context: List[str] = Field(default_factory=list)
    agent_draft_response: Optional[str] = None
    confidence_score: float = 0.0
    requires_escalation: bool = False

    # --- Append-only audit trail, written by every node ---
    execution_logs: List[str] = Field(default_factory=list)
