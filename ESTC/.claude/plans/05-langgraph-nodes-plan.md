# Execution Plan: Phase 4.3 — LangGraph Nodes
**Source spec:** `.claude/Specs/05-langgraph-nodes-spec.md`
**Source plan section:** `docs/plan.md` § Phase 4.3 (tasks 4.3.1 – 4.3.7)
**Status:** COMPLETE — all tasks executed and verified; exit gates EG-1/EG-2/EG-3 green (14 node tests pass; 25 pass across agent_state + rag + nodes).

---

## Context

This plan operationalizes Phase 4.3 of the ESTC roadmap: the **seven executable units of the LangGraph triage state machine** — `classify`, `route_by_intent`, the four worker agents (`billing_agent`, `bug_agent`, `feature_agent`, `lockout_agent`), and `supervisor_review` — each a pure function over the Phase 4.1 `AgentState`, composing the Phase 2 classifier API, the Phase 3 MCP servers, and the Phase 4.2 RAG retriever. Their sole runtime consumer is Phase 4.4 (`StateGraph(AgentState)` in `build.py` + `run_ticket`); this phase ships and unit-tests the callables but deliberately does **not** wire the graph. The work has four threads that must be done in order:

1. **Scaffolding & shared dependencies** (config + module + tooling): create the `graph/` and `graph/nodes/` namespace dirs, add three optional settings (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ESTC_GITHUB_REPO`) to `Settings`, pin the two LLM providers, and author `graph/llm.py` (the provider-or-template drafting helper). Everything downstream imports these.
2. **The seven nodes** (Python): one module per node under `graph/nodes/`, exactly the signatures and return-shapes fixed in spec § 4.
3. **Contract test harness** (Python): `estc/tests/test_graph_nodes.py` proving each node's AC (AC-T1 … AC-T9) offline — no LLM keys, GitHub in mock mode, MCP tools monkeypatched so the suite is DB-independent.
4. **Readiness audit** (tooling): collect-only + full green run, confirming all seven units import from `estc.services.orchestrator.graph.nodes.*` with no signature drift, unblocking Phase 4.4.

**Design note — what this plan mirrors and decides:** It follows the offline-first convention proven by the mock classifier (Phase 2) and the file-backed GitHub mock (Phase 3.2.5): with no API keys and GitHub forced to mock (`conftest.py`), every node produces deterministic, assertable output via the `graph/llm.py` template path. **Decision (spec open item 3):** node unit tests **monkeypatch** the Postgres MCP coroutines (returning canned `SubscriptionStatus`/`CustomerRecord`) rather than hitting live Postgres, so `test_graph_nodes.py` needs no running DB — a live-DB integration assertion is deferred to Phase 4.4's `run_ticket` e2e. **Decision (spec open item 4):** the LLM provider libs are imported *lazily inside* `_chat_model()`, so the offline test path never requires them; the plan still pins them in `requirements-orchestrator.txt` for the live path. **Plan-internal task IDs:** `docs/plan.md` defines roadmap tasks **4.3.1–4.3.7** only. Task **4.3.0** (scaffolding/config/llm) and Task **4.3.8** (test harness) below are plan-internal expansions introduced to satisfy spec acceptance bars (AC-T8, AC-T10); they are not new roadmap IDs.

Every step below ends with a **Verify** command. The shell is **PowerShell 5.1**. A step is "done" only when its verification passes.

---

## Pre-Flight (read-only sanity checks before any change)

- [ ] **PF-1** Confirm the source spec exists and is the version this plan targets.
  **Verify:** `Get-Content .claude/Specs/05-langgraph-nodes-spec.md | Select-String "route_by_intent"` returns ≥ 1 match.
- [ ] **PF-2** Confirm the venv is the active Python 3.11 toolchain.
  **Verify:** `.venv\Scripts\python --version` reports `Python 3.11.*`.
- [ ] **PF-3** Confirm the Phase 4.1 `AgentState` contract is importable (this phase's state type).
  **Verify:** `.venv\Scripts\python -c "from estc.shared.schemas.agent_state import AgentState; print('ok')"` prints `ok`.
- [ ] **PF-4** Confirm the Phase 4.2 retriever surface (`aretrieve`, `route_query`, `KBIndex`) is importable.
  **Verify:** `.venv\Scripts\python -c "from estc.services.orchestrator.rag.retriever import aretrieve, route_query, KBIndex; print('ok')"` prints `ok`.
- [ ] **PF-5** Confirm the Phase 3 MCP tool coroutines are importable (the read-only access boundary).
  **Verify:** `.venv\Scripts\python -c "from estc.services.mcp_postgres.server import get_subscription_status, get_customer_by_id; from estc.services.mcp_github.server import search_issues; print('ok')"` prints `ok`.
- [ ] **PF-6** Confirm the classifier `/classify` contract shape (so `classify` parses the right keys).
  **Verify:** `.venv\Scripts\python -c "from estc.services.classifier_api.app.schemas import ClassifyResponse; assert set(ClassifyResponse.model_fields)=={'intent','confidence','latency_ms'}; print('ok')"` prints `ok`.
- [ ] **PF-7** Confirm the GitHub mock fixture has ≥ 1 **open** issue for the default repo (AC-T4 precondition / spec § 5.1 edge case 5).
  **Verify:** `Get-Content estc/tests/fixtures/github_mock.json | Select-String '"state": "open"'` returns ≥ 1 match (fixture has #42, #37).
- [ ] **PF-8** Confirm `httpx` and `pytest-asyncio` are available (classify uses `AsyncClient`/`MockTransport`; async node tests need the asyncio plugin).
  **Verify:** `.venv\Scripts\python -c "import httpx, pytest_asyncio; print('ok')"` prints `ok`.
- [ ] **PF-9** Record the LLM-provider gap (offline path does not need these; live path does).
  **Verify:** `.venv\Scripts\python -c "import importlib.util as u; print(bool(u.find_spec('langchain_openai')), bool(u.find_spec('langchain_anthropic')))"` — both `False` is expected and acceptable (Task 4.3.0-c pins them; offline tests are unaffected).

---

## Task 4.3.0 — Scaffolding, Config & Shared Drafting Helper *(plan-internal; prerequisite for all nodes, satisfies AC-T8/FR-8)*

### 4.3.0-a Namespace-package directories
- [ ] Create empty dirs `estc/services/orchestrator/graph/` and `estc/services/orchestrator/graph/nodes/`. Do **not** add any `__init__.py` (PEP 420 namespace convention; spec FR-10).
  **Verify:** `Test-Path estc/services/orchestrator/graph/nodes` returns `True`; `Get-ChildItem -Recurse estc/services/orchestrator/graph -Filter __init__.py | Measure-Object | Select-Object -ExpandProperty Count` returns `0`.

### 4.3.0-b Settings additions
- [ ] Add three **optional** fields to `estc/shared/config.Settings`: `OPENAI_API_KEY: str | None = None`, `ANTHROPIC_API_KEY: str | None = None`, and `ESTC_GITHUB_REPO: str = "kalai555777/Enterprise-Support-Triage-Copilot"` (the repo present in `github_mock.json`). `extra="ignore"` already tolerates unknown env keys; defaults keep every existing verify green.
  **Verify:** `.venv\Scripts\python -c "from estc.shared.config import Settings; s=Settings(); assert s.ESTC_GITHUB_REPO.count('/')==1 and s.OPENAI_API_KEY is None and s.ANTHROPIC_API_KEY is None; print('ok')"` prints `ok`.

### 4.3.0-c Pin LLM providers (live path only)
- [ ] Append `langchain-openai>=0.2.0` and `langchain-anthropic>=0.2.0` to `requirements-orchestrator.txt`. Installation is **not** required for the offline test suite (providers are imported lazily inside `_chat_model()` only when a key is set); install is optional for exercising the live path.
  **Verify:** `Get-Content requirements-orchestrator.txt | Select-String "langchain-(openai|anthropic)"` returns 2 matches.

### 4.3.0-d Drafting helper `graph/llm.py`
- [ ] Create `estc/services/orchestrator/graph/llm.py` per spec § 4.1: `CONFIDENCE_FLOOR_NO_CONTEXT = 0.55`, an `lru_cache`'d `_chat_model()` selecting Anthropic→OpenAI→`None` with **lazy in-branch imports**, a `_template_reply(intent, facts, context)` that emits each fact (so tier names / issue refs survive), and `async def draft_reply(*, intent, issue_text, context, facts) -> tuple[str, float]` returning `(text, confidence)` where confidence is `0.85` with context else the floor. The LLM branch must inject `facts` into the prompt so live drafts keep the same asserted substrings.
  **Verify:** `.venv\Scripts\python -c "import asyncio; from estc.services.orchestrator.graph.llm import draft_reply; t,c=asyncio.run(draft_reply(intent='billing', issue_text='x', context=[], facts={'tier':'Enterprise'})); assert 'Enterprise' in t and c==0.55; print('ok')"` prints `ok` (proves offline template path + no-context floor).

---

## Task 4.3.1 — `classify` Node

- [ ] Create `estc/services/orchestrator/graph/nodes/classify.py`: `async def classify(state, *, client=None) -> dict[str, object]` posting `{"text": state.raw_issue_text}` to `{Settings().CLASSIFIER_API_URL}/classify`, parsing `intent`/`confidence`, returning `{"intent", "confidence_score", "execution_logs": state.execution_logs + ["classified:<intent>"]}`. The keyword-only `client` accepts an injected `httpx.AsyncClient` (built on `httpx.MockTransport`) so the test makes no real call; when `client is None` the node owns and closes its client. Non-2xx raises via `raise_for_status()`; 5 s timeout (spec FR-1, § 5.1 edge case 7).
  **Verify:** `.venv\Scripts\python -c "import inspect; from estc.services.orchestrator.graph.nodes.classify import classify; assert inspect.iscoroutinefunction(classify); print('ok')"` prints `ok`. Full behavior covered by AC-T1 in Task 4.3.8.

---

## Task 4.3.2 — `route_by_intent` Conditional Edge

- [ ] Create `estc/services/orchestrator/graph/nodes/router.py`: pure `def route_by_intent(state) -> str` returning `_ROUTE.get(state.intent or "", "billing_agent")` over the literal table `{"billing":"billing_agent","bug":"bug_agent","feature":"feature_agent","lockout":"lockout_agent"}` (spec FR-2, § 5.1 edge case 1). No I/O, not async.
  **Verify:** `.venv\Scripts\python -c "from estc.services.orchestrator.graph.nodes.router import route_by_intent as r; from estc.shared.schemas.agent_state import AgentState as S; mk=lambda i: S(ticket_id='t',raw_issue_text='x',company_id='1',intent=i); assert [r(mk(i)) for i in ['billing','bug','feature','lockout',None,'???']]==['billing_agent','bug_agent','feature_agent','lockout_agent','billing_agent','billing_agent']; print('ok')"` prints `ok`. Matches AC-T2.

---

## Task 4.3.3 — `billing_agent` Node

- [ ] Create `estc/services/orchestrator/graph/nodes/billing_agent.py`: `async def billing_agent(state) -> dict[str, object]` that (1) `await get_subscription_status(state.company_id)` (tolerate `None` → `facts={}`, spec § 5.1 edge case 2), (2) `await aretrieve(state.raw_issue_text, index=KBIndex.BILLING)`, (3) `await draft_reply(intent="billing", ..., facts={"tier": sub.subscription_tier})`, returning `retrieved_context`, `agent_draft_response`, `confidence_score`, and `execution_logs + ["billing_drafted"]`. Draft must surface the tier (spec FR-3).
  **Verify:** `.venv\Scripts\python -c "import inspect; from estc.services.orchestrator.graph.nodes.billing_agent import billing_agent; assert inspect.iscoroutinefunction(billing_agent); print('ok')"` prints `ok`. Tier-mention behavior covered by AC-T3 (Task 4.3.8, MCP monkeypatched to `Enterprise`).

---

## Task 4.3.4 — `bug_agent` Node

- [ ] Create `estc/services/orchestrator/graph/nodes/bug_agent.py`: `async def bug_agent(state) -> dict[str, object]` that (1) `await search_issues(Settings().ESTC_GITHUB_REPO, query=<derived from raw_issue_text>, state="open")`, (2) `await aretrieve(state.raw_issue_text, index=KBIndex.TECHNICAL)`, (3) drafts a reply that **cites ≥ 1 issue as `#<digits>`** — pass the issue numbers into `facts` (e.g. `{"issues": "#42, #37"}`) so both the template and live paths include them; if `search_issues` returns `[]`, cite the first available mock issue (spec § 5.1 edge case 5). Append `"bug_drafted"`.
  **Verify:** `.venv\Scripts\python -c "import inspect; from estc.services.orchestrator.graph.nodes.bug_agent import bug_agent; assert inspect.iscoroutinefunction(bug_agent); print('ok')"` prints `ok`. The `#\d+` assertion is AC-T4 (Task 4.3.8, GitHub mock mode).

---

## Task 4.3.5 — `feature_agent` Node

- [ ] Create `estc/services/orchestrator/graph/nodes/feature_agent.py`: `async def feature_agent(state) -> dict[str, object]` that runs `aretrieve` over **both** `KBIndex.BILLING` and `KBIndex.TECHNICAL` (concatenating context), drafts an acknowledgement, and records an **internal-only** synthetic ticket by appending `"feature_logged"` and `f"feature_ticket:{uuid4()}"` to `execution_logs`. **No MCP write tool is imported or called** (none exists; servers are read-only — spec FR-5, AC-B3).
  **Verify:** `Get-Content estc/services/orchestrator/graph/nodes/feature_agent.py | Select-String "feature_logged"` returns ≥ 1 match, and `... | Select-String "(create|update|delete|insert)"` returns 0 matches (proves no write path). Log presence is AC-T5 (Task 4.3.8).

---

## Task 4.3.6 — `lockout_agent` Node

- [ ] Create `estc/services/orchestrator/graph/nodes/lockout_agent.py`: `async def lockout_agent(state) -> dict[str, object]` that `await get_customer_by_id(state.company_id)` (tolerate `None`), drafts an identity-verification explainer, and **unconditionally sets `requires_escalation = True`** regardless of confidence, appending `"lockout_escalated"`. Leave `confidence_score >= 0` (spec FR-6).
  **Verify:** `.venv\Scripts\python -c "import inspect; from estc.services.orchestrator.graph.nodes.lockout_agent import lockout_agent; assert inspect.iscoroutinefunction(lockout_agent); print('ok')"` prints `ok`. The `requires_escalation is True` / `confidence_score >= 0` assertion is AC-T6 (Task 4.3.8).

---

## Task 4.3.7 — `supervisor_review` Node

- [ ] Create `estc/services/orchestrator/graph/nodes/supervisor.py`: pure `def supervisor_review(state) -> dict[str, object]` with `CONFIDENCE_THRESHOLD = 0.70`; if `state.confidence_score < 0.70 or state.requires_escalation` → append `"ESCALATE"` and set `requires_escalation=True`, else append `"AUTO_APPROVED"` (spec FR-7). No I/O, not async.
  **Verify:** `.venv\Scripts\python -c "from estc.services.orchestrator.graph.nodes.supervisor import supervisor_review as sr; from estc.shared.schemas.agent_state import AgentState as S; lo=sr(S(ticket_id='t',raw_issue_text='x',company_id='1',confidence_score=0.5)); hi=sr(S(ticket_id='t',raw_issue_text='x',company_id='1',confidence_score=0.9)); assert lo['execution_logs']==['ESCALATE'] and lo['requires_escalation'] is True and hi['execution_logs']==['AUTO_APPROVED']; print('ok')"` prints `ok`. Matches AC-T7.

---

## Task 4.3.8 — Node Contract Test Harness *(plan-internal; satisfies AC-T10)*

### 4.3.8-a Test file & offline guards
- [ ] Create `estc/tests/test_graph_nodes.py`. Rely on the existing `conftest.py` (GitHub forced to mock, no `GITHUB_PAT`). Monkeypatch the Postgres MCP coroutines so the suite needs no live DB: patch `billing_agent`/`lockout_agent`'s `get_subscription_status`/`get_customer_by_id` to return canned `SubscriptionStatus(subscription_tier="Enterprise", ...)` / `CustomerRecord(...)`. Ensure no LLM keys are set in the test env (template path).
  **Verify:** `.venv\Scripts\pytest --collect-only estc/tests/test_graph_nodes.py` collects ≥ 9 items with no import errors.

### 4.3.8-b Test cases — must include at minimum (AC-T1 … AC-T9 bar)
- [ ] `test_classify_with_mock_transport` → `httpx.MockTransport` returns `{"intent":"bug","confidence":0.85,"latency_ms":3.0}`; `classify` returns `intent=="bug"`, `confidence_score==0.85`, log contains `"classified:bug"` (AC-T1).
- [ ] `test_route_table_and_fallback` → all four mappings plus `None`/unknown→`billing_agent` (AC-T2).
- [ ] `test_billing_agent_mentions_tier` → MCP monkeypatched to `Enterprise`; `agent_draft_response` contains `"Enterprise"`, log has `"billing_drafted"` (AC-T3).
- [ ] `test_bug_agent_cites_issue` → GitHub mock mode; `re.search(r"#\d+", agent_draft_response)` is truthy (AC-T4).
- [ ] `test_feature_agent_logs_internal_ticket` → `"feature_logged"` in `execution_logs`; no MCP write occurred (AC-T5).
- [ ] `test_lockout_agent_escalates` → `requires_escalation is True` and `confidence_score >= 0` (AC-T6).
- [ ] `test_supervisor_low_confidence_escalates` → `confidence_score=0.5` → `"ESCALATE"` + `requires_escalation True` (AC-T7).
- [ ] `test_supervisor_high_confidence_approves` → `confidence_score=0.9`, `requires_escalation=False` → `"AUTO_APPROVED"` (AC-T7).
- [ ] `test_execution_logs_accumulate` → thread state through `classify → billing_agent → supervisor_review` (merging each returned dict into the state); assert `execution_logs` grows and retains earlier entries, i.e. nodes return the full extended list not a delta (AC-T9 / spec FR-9, § 5.1 edge case 8).

  **Verify:** `.venv\Scripts\pytest estc/tests/test_graph_nodes.py -v` reports **all green** with at least 9 passed.

---

## Phase 4.3 Exit Gate

- [ ] **EG-1 (importability / Phase 4.4 readiness, AC-B5 bar)** — All seven units import from the canonical paths with the agreed shapes.
  **Verify:** `.venv\Scripts\python -c "from estc.services.orchestrator.graph.nodes.classify import classify; from estc.services.orchestrator.graph.nodes.router import route_by_intent; from estc.services.orchestrator.graph.nodes.billing_agent import billing_agent; from estc.services.orchestrator.graph.nodes.bug_agent import bug_agent; from estc.services.orchestrator.graph.nodes.feature_agent import feature_agent; from estc.services.orchestrator.graph.nodes.lockout_agent import lockout_agent; from estc.services.orchestrator.graph.nodes.supervisor import supervisor_review; import inspect; assert all(inspect.iscoroutinefunction(f) for f in [classify,billing_agent,bug_agent,feature_agent,lockout_agent]); assert not inspect.iscoroutinefunction(route_by_intent) and not inspect.iscoroutinefunction(supervisor_review); print('ok')"` prints `ok`.

- [ ] **EG-2 (full node test sweep, AC-T10)** — Tests all green offline.
  **Verify:** `.venv\Scripts\pytest estc/tests/test_graph_nodes.py -v --tb=short` reports **0 failed**, ≥ 9 passed.

- [ ] **EG-3 (no-regression on the broader suite)** — Phase 4.3 additions don't break prior phases.
  **Verify:** `.venv\Scripts\pytest estc/tests/test_agent_state.py estc/tests/test_rag.py estc/tests/test_graph_nodes.py -q` reports **0 failed**. (Postgres/GitHub MCP suites require live DB / are mock-mode and may be run separately.)

---

## Risks & Open Questions

1. **LangGraph channel-merge semantics (deferred to 4.4).** These nodes return full extended `execution_logs` lists (FR-9), which is correct under default last-writer-wins. When Phase 4.4 compiles `StateGraph(AgentState)`, if an `Annotated[list, add]` reducer is introduced, the nodes must switch to returning *deltas*. Flagged so 4.4 picks one convention graph-wide; AC-T9 guards the current one.
2. **Node→MCP transport.** Plan uses direct in-process `await tool(...)` (spec § 5.1 edge case 6), not `fastmcp.Client(mcp)`. If Phase 4.4 needs cross-process isolation or true MCP wire-protocol tracing in LangSmith, revisit then; the security guarantee (no raw SQL/shell) holds regardless.
3. **Live-DB billing assertion.** AC-T3 monkeypatches the MCP tool, so it never proves the real `c-01`→`Enterprise` round-trip. That live assertion is intentionally deferred to Phase 4.4's `run_ticket` e2e (plan 4.4.2 verify), which already assumes a seeded DB.
4. **LLM provider libs unpinned/uninstalled today.** `_chat_model()` imports them lazily, so offline tests pass without them; but a user who sets `ANTHROPIC_API_KEY` without `pip install -r requirements-orchestrator.txt` will hit an `ImportError` at first draft. Mitigation: the lazy import surfaces a clear module-not-found, and 4.3.0-c documents the install requirement for the live path.
5. **`gpt-4o-mini` / `claude-sonnet-4-6` model IDs.** Spec uses `claude-sonnet-4-6`; if the installed `langchain-anthropic` version predates that alias, the live path errors. Offline CI is unaffected; the live caller is responsible for a valid model id. Noted for 4.5/4.6 when live runs begin.

---

## Out of Scope (explicitly deferred)

- Graph wiring `StateGraph(AgentState)` + conditional edges + `MemorySaver` + `run_ticket` — Phase 4.4.
- LangSmith child-run tracing per node — Phase 4.5.1.
- Ragas Faithfulness/Answer-Relevance/Context-Recall evaluation — Phase 4.5.2.
- FastAPI `/tickets` + SSE serialization of `AgentState` per node transition — Phase 4.6.
- Any live-LLM golden-output or live-Postgres integration assertion for the worker drafts — Phase 4.4 e2e / Phase 5.6 smoke.

---

**Awaiting `Proceed` to begin execution at PF-1.**
