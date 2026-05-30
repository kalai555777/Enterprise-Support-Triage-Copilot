"""`supervisor_review` node (Phase 4.3, task 4.3.7).

Pure, synchronous compliance gate. Escalates when confidence is below the 0.70
threshold OR a worker already flagged escalation; otherwise auto-approves. This is
the single chokepoint where the auto-approve-vs-human-review verdict is recorded.
The 0.70 threshold is the tunable from docs/plan.md "Decisions Embedded in This Plan".
"""

from __future__ import annotations

from estc.shared.schemas.agent_state import AgentState

CONFIDENCE_THRESHOLD = 0.70


def supervisor_review(state: AgentState) -> dict[str, object]:
    escalate = state.confidence_score < CONFIDENCE_THRESHOLD or state.requires_escalation
    verdict = "ESCALATE" if escalate else "AUTO_APPROVED"
    return {
        "requires_escalation": bool(escalate),
        "execution_logs": state.execution_logs + [verdict],
    }
