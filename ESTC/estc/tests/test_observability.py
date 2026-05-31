"""Phase 4.5 observability/eval wiring tests (tasks 4.5.0-4.5.3; AC-T1/T3/T4/T6).

Fully offline: the tracing bootstrap contract, the gold-fixture integrity, the Ragas
dataset-assembly shape (graph monkeypatched), and the graceful-skip path are all proven
without a LangSmith key, a judge LLM, or any live orchestrator dependency. The live
child-run assertion (AC-T2) is opt-in via ``ESTC_E2E_LIVE=1`` (needs network + a running
classifier/Postgres) and otherwise skips.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import pytest

from estc.services.orchestrator.graph import observability
from estc.shared.schemas.agent_state import AgentState
from estc.tests.eval import ragas_eval
from estc.tests.eval._ragas_compat import ensure_ragas_importable

_FIXTURE = Path(__file__).parent / "eval" / "fixtures" / "eval_tickets.jsonl"
_TRACE_ENV = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_PROJECT",
              "LANGCHAIN_PROJECT", "LANGSMITH_API_KEY")


@pytest.fixture(autouse=True)
def _restore_trace_env():
    """Snapshot/restore tracing env so configure_tracing() side effects don't leak."""
    saved = {k: os.environ.get(k) for k in _TRACE_ENV}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# --- AC-T1 -----------------------------------------------------------------
def test_configure_tracing_off_without_key(monkeypatch):
    monkeypatch.setattr(observability, "Settings",
                        lambda: _FakeSettings(tracing=False, key=None))
    assert observability.configure_tracing() is False
    assert os.environ.get("LANGSMITH_TRACING") == "false"


def test_configure_tracing_on_with_key(monkeypatch):
    monkeypatch.setattr(observability, "Settings",
                        lambda: _FakeSettings(tracing=True, key="ls-dummy"))
    assert observability.configure_tracing() is True
    assert os.environ.get("LANGSMITH_TRACING") == "true"
    assert os.environ.get("LANGSMITH_PROJECT") == "estc-dev"


def test_configure_tracing_off_when_key_but_flag_disabled(monkeypatch):
    # Key present but the toggle off => inactive (the real .env shape on this box).
    monkeypatch.setattr(observability, "Settings",
                        lambda: _FakeSettings(tracing=False, key="ls-dummy"))
    assert observability.configure_tracing() is False


class _FakeSettings:
    def __init__(self, tracing: bool, key):
        self.LANGSMITH_TRACING = tracing
        self.LANGSMITH_API_KEY = key
        self.LANGSMITH_PROJECT = "estc-dev"


# --- AC-T3 -----------------------------------------------------------------
def test_fixture_integrity():
    rows = [json.loads(l) for l in _FIXTURE.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 20
    assert all(r["question"] and r["ground_truth"] and r["company_id"] for r in rows)
    assert set(r["intent"] for r in rows) == {"billing", "bug", "feature", "lockout"}
    assert all(c == 5 for c in Counter(r["intent"] for r in rows).values())


# --- AC-T4 -----------------------------------------------------------------
async def test_collect_samples_shape(monkeypatch):
    from estc.services.orchestrator.graph import build

    async def _fake_run_ticket(ticket_id, text, company_id):
        return AgentState(
            ticket_id=ticket_id, raw_issue_text=text, company_id=company_id,
            intent="bug", agent_draft_response="a grounded draft",
            retrieved_context=["chunk-1"], confidence_score=0.85,
            execution_logs=["classified:bug", "bug_drafted", "AUTO_APPROVED"],
        )

    monkeypatch.setattr(build, "run_ticket", _fake_run_ticket)
    samples = await ragas_eval._collect_samples()
    assert len(samples) == 20
    for s in samples:
        assert set(s) >= {"question", "answer", "contexts", "ground_truth"}
        assert isinstance(s["question"], str) and s["question"]
        assert isinstance(s["contexts"], list)
        assert isinstance(s["ground_truth"], str) and s["ground_truth"]


# --- AC-T6 -----------------------------------------------------------------
def test_eval_skips_without_judge(monkeypatch, tmp_path):
    monkeypatch.setattr(ragas_eval, "_build_judge", lambda: None)
    results = ragas_eval.RESULTS
    if results.exists():
        results.unlink()
    assert ragas_eval.main() == 0          # clean skip, not a failure
    assert not results.exists()            # no bogus CSV written


def test_ragas_importable_via_shim():
    ensure_ragas_importable()
    import ragas  # noqa: F401  — must not raise (Risk 1 repaired)


# --- AC-T2 (live, opt-in) --------------------------------------------------
@pytest.mark.skipif(os.getenv("ESTC_E2E_LIVE") != "1",
                    reason="live trace: needs LANGSMITH key + network + running orchestrator deps")
async def test_langsmith_child_runs():
    import time

    import langsmith

    from estc.services.orchestrator.graph.build import run_ticket

    assert observability.configure_tracing() is True
    await run_ticket("trace-e2e-001", "I am getting a 500 error pulling the API", "c-01")
    client = langsmith.Client()
    time.sleep(5)  # tracing flush is async/eventually-consistent
    runs = list(client.list_runs(project_name="estc-dev", limit=10))
    assert runs, "no runs recorded in estc-dev"
