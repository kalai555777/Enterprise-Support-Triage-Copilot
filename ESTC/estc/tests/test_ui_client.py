"""Phase 5 UI-client unit tests (AC-T11).

Fully offline: ``stream_ticket`` is exercised against a faked ``connect_sse`` (no orchestrator),
and the pure UI helpers (``confidence_band`` thresholds, ``parse_customer_facts``) are checked
directly. No Streamlit runtime / network is needed.
"""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace

from estc.services.ui import orchestrator_client as oc
from estc.services.ui import state as ui_state


def _fake_event_source(frames):
    """A stand-in for httpx-sse's EventSource: yields canned ServerSentEvent-like objects."""
    sse = [SimpleNamespace(event=ev, data=json.dumps(payload)) for ev, payload in frames]

    @contextlib.contextmanager
    def _cm(_client, _method, _url):
        yield SimpleNamespace(iter_sse=lambda: iter(sse))

    return _cm


# --- AC-T11: stream parsing ------------------------------------------------
def test_stream_ticket_parses_frames_in_order(monkeypatch):
    frames = [
        ("open", {"ticket_id": "t1", "status": "running"}),
        ("node", {"event": "node", "node": "classify", "ticket_id": "t1", "update": {}}),
        ("node", {"event": "node", "node": "bug_agent", "ticket_id": "t1", "update": {}}),
        ("node", {"event": "node", "node": "supervisor_review", "ticket_id": "t1", "update": {}}),
        ("done", {"event": "done", "ticket_id": "t1", "state": {"intent": "bug"}}),
    ]
    monkeypatch.setattr(oc, "connect_sse", _fake_event_source(frames))

    out = list(oc.stream_ticket("t1"))
    assert [f["event"] for f in out] == ["open", "node", "node", "node", "done"]
    assert [f["node"] for f in out if f["event"] == "node"] == [
        "classify",
        "bug_agent",
        "supervisor_review",
    ]
    assert out[-1]["state"]["intent"] == "bug"
    assert len(out) >= 4  # the 4.6.1 floor the UI relies on


# --- AC-T5: confidence band thresholds -------------------------------------
def test_confidence_band_thresholds():
    assert ui_state.confidence_band(0.85) == (85, "green")
    assert ui_state.confidence_band(0.80) == (80, "green")
    assert ui_state.confidence_band(0.79) == (79, "orange")
    assert ui_state.confidence_band(0.60) == (60, "orange")
    assert ui_state.confidence_band(0.59) == (59, "red")
    assert ui_state.confidence_band(0.0) == (0, "red")


# --- 5.4.2: tier / account-status parser -----------------------------------
def test_parse_customer_facts():
    state = {
        "agent_draft_response": "Your Enterprise account is currently Locked; please verify.",
        "retrieved_context": [],
    }
    assert ui_state.parse_customer_facts(state) == ("Enterprise", "Locked")

    # falls back to em-dash when the facts aren't recoverable
    assert ui_state.parse_customer_facts({"agent_draft_response": "no facts here"}) == ("—", "—")
