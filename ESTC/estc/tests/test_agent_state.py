"""Contract tests for the shared LangGraph AgentState schema (Phase 4.1).

Pure-Python unit tests — no DB, network, or fixtures required. They lock the
field set, defaults, required-field enforcement, per-instance list isolation,
and serialization round-trip that every Phase 4 node depends on.
"""

import pytest
from pydantic import ValidationError

from estc.shared.schemas.agent_state import AgentState

EXPECTED_FIELDS = {
    "ticket_id",
    "raw_issue_text",
    "company_id",
    "intent",
    "retrieved_context",
    "agent_draft_response",
    "confidence_score",
    "requires_escalation",
    "execution_logs",
}


def _minimal() -> AgentState:
    return AgentState(ticket_id="t1", raw_issue_text="x", company_id="9422")


def test_exact_field_set():
    # AC-T2: exactly the nine fields from design.md section 3, no more, no fewer.
    assert set(AgentState.model_fields) == EXPECTED_FIELDS


def test_defaults():
    # AC-T3: a minimally-constructed instance carries the documented defaults.
    s = _minimal()
    assert s.intent is None
    assert s.agent_draft_response is None
    assert s.confidence_score == 0.0
    assert s.requires_escalation is False
    assert s.retrieved_context == []
    assert s.execution_logs == []


def test_required_fields_enforced():
    # AC-T4: the three identity fields have no default and must be supplied.
    with pytest.raises(ValidationError):
        AgentState(ticket_id="t1")  # type: ignore[call-arg]


def test_list_isolation():
    # AC-T5 / FR-4 tripwire: each instance owns its own list objects.
    a = AgentState(ticket_id="a", raw_issue_text="x", company_id="1")
    b = AgentState(ticket_id="b", raw_issue_text="y", company_id="2")
    a.execution_logs.append("classified")
    a.retrieved_context.append("doc-1")
    assert b.execution_logs == []
    assert b.retrieved_context == []
    assert a.execution_logs is not b.execution_logs
    assert a.retrieved_context is not b.retrieved_context


def test_serialization_round_trip():
    # AC-T6: model_dump emits all nine keys and round-trips back to an equal model.
    a = _minimal()
    a.intent = "billing"
    a.confidence_score = 0.91
    a.execution_logs.append("classified")
    dumped = a.model_dump()
    assert set(dumped) == EXPECTED_FIELDS
    assert AgentState(**dumped) == a


def test_confidence_score_coercion():
    # spec section 5.1 edge case 2: numeric coercion is allowed, garbage is not.
    s = AgentState(ticket_id="t", raw_issue_text="x", company_id="1", confidence_score=1)
    assert s.confidence_score == 1.0
    assert isinstance(s.confidence_score, float)
    with pytest.raises(ValidationError):
        AgentState(ticket_id="t", raw_issue_text="x", company_id="1", confidence_score="high")
