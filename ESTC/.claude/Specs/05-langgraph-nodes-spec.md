# Architectural Specification: Phase 4.3 — LangGraph Nodes

**Status:** DRAFT / PROPOSED
**Associated Tasks:** Tasks 4.3.1 – 4.3.7 (`docs/plan.md` § Phase 4.3 — LangGraph Nodes)
**Target Files:**
- `estc/services/orchestrator/graph/nodes/classify.py` (new — `classify` node, task 4.3.1)
- `estc/services/orchestrator/graph/nodes/router.py` (new — `route_by_intent` conditional edge, task 4.3.2)
- `estc/services/orchestrator/graph/nodes/billing_agent.py` (new — `billing_agent` node, task 4.3.3)
- `estc/services/orchestrator/graph/nodes/bug_agent.py` (new — `bug_agent` node, task 4.3.4)
- `estc/services/orchestrator/graph/nodes/feature_agent.py` (new — `feature_agent` node, task 4.3.5)
- `estc/services/orchestrator/graph/nodes/lockout_agent.py` (new — `lockout_agent` node, task 4.3.6)
- `estc/services/orchestrator/graph/nodes/supervisor.py` (new — `supervisor_review` node, task 4.3.7)
- `estc/services/orchestrator/graph/llm.py` (new — shared LLM-drafting helper with deterministic offline fallback; supporting module)
- `estc/services/orchestrator/graph/` and `.../graph/nodes/` (new namespace-package directories; no `__init__.py`)
- `estc/tests/test_graph_nodes.py` (new — node contract + integration tests)

---


## 1. Executive Summary & Problem Statement

### 1.1 Objective & Context

This sub-phase delivers the **seven executable units of the LangGraph triage state machine** described in `docs/design.md` § Component D. Phase 4.1 produced the shared `AgentState` container; Phase 4.2 produced the semantic-routing RAG retriever over ChromaDB. Phase 4.3 is where those two artifacts, plus the Phase 2 classifier API and the Phase 3 MCP servers, are composed into the per-node business logic that actually triages a ticket:

```
classify ──▶ route_by_intent ──▶ { billing_agent | bug_agent | feature_agent | lockout_agent } ──▶ supervisor_review
```

Each node is a pure function over `AgentState`: it receives the current state, performs exactly one unit of work (an HTTP classifier call, an MCP tool call, a RAG retrieval, an LLM draft, or a compliance check), and returns a **partial state update** that LangGraph merges back into the channel. This phase writes the node bodies and their contract tests; it deliberately does **not** wire them into a `StateGraph` — that is Phase 4.4 (`build.py`, `run_ticket`). The split matters: nodes must be independently importable and unit-testable *before* the graph exists, so a defect localizes to one node rather than to the compiled graph.

Concretely, this phase realizes the field-ownership matrix locked by the Phase 4.1 spec (`04-langgraph-rag-spec.md` § 1.1):

- `classify` (4.3.1) writes `state.intent` and seeds `state.confidence_score` from the classifier's confidence.
- `route_by_intent` (4.3.2) reads `state.intent` and returns the name of the next node — it is a LangGraph *conditional-edge function*, not a state-mutating node.
- `billing_agent` / `bug_agent` / `feature_agent` / `lockout_agent` (4.3.3–4.3.6) populate `state.retrieved_context`, `state.agent_draft_response`, `state.confidence_score`, `state.requires_escalation`, and append breadcrumbs to `state.execution_logs`.
- `supervisor_review` (4.3.7) reads `state.confidence_score` and `state.requires_escalation` and appends `"ESCALATE"` or `"AUTO_APPROVED"` to `state.execution_logs`.

### 1.2 Core Problem Statement

The orchestrator must turn an unstructured support ticket into a *grounded, auditable draft reply with a confidence signal and an escalation decision*, while honoring three hard architectural rules established upstream: (a) intent classification is done by the **local PyTorch/FastAPI service**, never an LLM (`design.md` § Component A); (b) all transactional and engineering context is fetched **only through read-only MCP tool schemas**, never raw SQL or shell (`design.md` § Component B / § 2 security control); and (c) every drafted reply must be grounded in **retrieved knowledge-base context** (`design.md` § Component C, the RAG faithfulness mandate evaluated in Phase 4.5).

The challenge is that these nodes sit at the integration seam of four independently-built subsystems (classifier HTTP API, two async MCP servers, the Chroma retriever, and an external LLM) and must remain **deterministically testable offline** — the existing suite runs with no `GITHUB_PAT`, no LLM API keys, and the GitHub MCP forced into mock mode (`estc/tests/conftest.py`). Therefore every node needs a graceful, deterministic degradation path: a node may not hard-fail merely because an API key or a network dependency is absent, and its test-observable output (the customer's tier in the billing draft, a `#<issue-number>` in the bug draft, a `"feature_logged"` breadcrumb, an escalation flag) must be reproducible without any live external call.

---

## 2. System Boundaries & Constraints

### 2.1 Architectural Boundaries

- **Upstream Trigger / Consumer:**
  - The Phase 4.4 graph (`StateGraph(AgentState)` in `build.py`, task 4.4.1) is the sole runtime caller. It invokes `classify` first, uses `route_by_intent` as the conditional edge after `classify`, dispatches to exactly one worker agent, then calls `supervisor_review` as the terminal node. Phase 4.3 ships the callables; Phase 4.4 owns the edges.
  - The Phase 4.4 `run_ticket(ticket_id, text, company_id)` entrypoint (task 4.4.2) constructs the initial `AgentState` that these nodes consume.
- **Downstream Dependencies (what must be reachable at *runtime*, not at test time):**
  - **Classifier API** (`classify` node): `POST {CLASSIFIER_API_URL}/classify` → `ClassifyResponse{intent, confidence, latency_ms}` (Phase 2.3, `estc/services/classifier_api/app/schemas.py`). `CLASSIFIER_API_URL` comes from `estc.shared.config.Settings`.
  - **PostgreSQL MCP** (`billing_agent`, `lockout_agent`): the async tools `get_subscription_status(company_id)` and `get_customer_by_id(company_id)` from `estc.services.mcp_postgres.server` (Phase 3.1). Backed by the seeded `enterprise_customers` table.
  - **GitHub MCP** (`bug_agent`): the async tool `search_issues(repo, query, state)` from `estc.services.mcp_github.server` (Phase 3.2). Honors the existing mock-fallback when `GITHUB_PAT` is unset.
  - **RAG retriever** (`billing_agent`, `bug_agent`, `feature_agent`): `aretrieve(query, index, k)` / `route_query(query)` and the `KBIndex` enum from `estc.services.orchestrator.rag.retriever` (Phase 4.2), over the persisted `./chroma_db`.
  - **LLM ChatModel** (all four worker agents, via `graph/llm.py`): Claude Sonnet 4.6 (`langchain_anthropic`) when `ANTHROPIC_API_KEY` is set, else `gpt-4o-mini` (`langchain_openai`) when `OPENAI_API_KEY` is set, else a **deterministic template** that requires no network.
- **MCP access boundary:** Nodes reach the MCP servers through the FastMCP **in-process async tool coroutines** (direct `await tool(...)` import), the same surface the Phase 3 tests exercise (`test_mcp_postgres.py` calls `await get_customer_by_id("c-01")`). This is the protocol's typed-tool boundary — nodes never compose SQL, shell, or GitHub REST URLs themselves, satisfying `design.md` § 2's read-only control. (The alternative — spinning a `fastmcp.Client(mcp)` per call — is heavier and deferred; see § 5.1 edge case 6 and Risks.)

### 2.2 Technical & Operational Constraints

- **Async discipline (project-wide rule, recorded in memory `feedback_mcp_async`):** Every node that performs I/O — `classify` (HTTP), all four worker agents (MCP + RAG + LLM) — is declared `async def` and `await`s its dependencies. `route_by_intent` and `supervisor_review` are **pure synchronous functions** (no I/O) and are intentionally *not* async. The async RAG entrypoint `aretrieve` (not the blocking `retrieve`) is used inside coroutines so the event loop is never blocked.
- **Performance / Latency:** The end-to-end `run_ticket` budget is `< 10s` (plan task 4.4.2). Per-node soft budgets: `classify` < 200 ms (local API, `latency_ms` already < 50 ms target), each MCP call < 150 ms (Phase 3.1 p95 gate), each RAG retrieval < 1 s (embed + Chroma query), LLM draft dominated by the provider (seconds) — which is precisely why the offline template path exists for CI.
- **Security & Compliance:** Nodes call only the predefined MCP tool schemas; no raw SQL/shell/REST construction. `raw_issue_text` may carry PII and is **never** written verbatim into `execution_logs` (logs hold node-status breadcrumbs only, per `04-langgraph-rag-spec.md` § 2.2). LLM prompts may include the issue text and retrieved context, but the audit trail surfaced to the SSE stream and UI (Phase 4.6/5) stays PII-free.
- **Resource Limits:** No new long-lived connections introduced beyond those owned by the MCP servers (Postgres pool) and the retriever (Chroma client singleton + bge embeddings). The LLM client is constructed lazily and cached per-process.
- **Typing strictness:** These modules live under `estc/services/orchestrator/...`, **outside** the `shared.schemas.*` strict-mypy override, so full `--strict` is not mandated here; however the project's baseline `mypy` config applies, and all node signatures are fully annotated (`AgentState -> dict[str, object]`).
- **Determinism for CI:** With no API keys and GitHub in mock mode, every node must produce stable, assertable output. This is a first-class constraint, not a nicety (§ 1.2).

---

## 3. Functional Requirements

- **FR-1 (`classify` node — task 4.3.1):** `async def classify(state: AgentState) -> dict[str, object]` issues `POST {CLASSIFIER_API_URL}/classify` with body `{"text": state.raw_issue_text}` using an `httpx.AsyncClient`, parses the `ClassifyResponse`, and returns `{"intent": <intent>, "confidence_score": <confidence>, "execution_logs": state.execution_logs + ["classified:<intent>"]}`. The classifier's four intent labels are `"billing"`, `"bug"`, `"feature"`, `"lockout"` (per `classifier_api/app/main.py`). The node must accept an injectable `httpx` transport/client so tests can supply `httpx.MockTransport`.
- **FR-2 (`route_by_intent` conditional edge — task 4.3.2):** `def route_by_intent(state: AgentState) -> str` returns exactly one of `"billing_agent" | "bug_agent" | "feature_agent" | "lockout_agent"` by mapping `state.intent`. Mapping is a literal table `{"billing": "billing_agent", "bug": "bug_agent", "feature": "feature_agent", "lockout": "lockout_agent"}`. An unrecognized or `None` intent falls back to `"billing_agent"` (mirroring the classifier's own catch-all default) — see § 5.1 edge case 1. This is a pure function with no side effects.
- **FR-3 (`billing_agent` node — task 4.3.3):** `async def billing_agent(state: AgentState) -> dict[str, object]` (a) calls `get_subscription_status(state.company_id)` on the Postgres MCP, (b) runs `aretrieve(state.raw_issue_text, index=KBIndex.BILLING)`, (c) drafts a reply via `graph/llm.py`, and returns updates to `retrieved_context`, `agent_draft_response`, `confidence_score`, and `execution_logs` (append `"billing_drafted"`). **The draft must mention the customer's `subscription_tier`** (e.g. `"Enterprise"`) so AC-T3 can assert on it; the deterministic template guarantees this even with no LLM.
- **FR-4 (`bug_agent` node — task 4.3.4):** `async def bug_agent(state: AgentState) -> dict[str, object]` (a) calls `search_issues(repo, query, state="open")` on the GitHub MCP using a configured default repo and a query derived from `state.raw_issue_text`, (b) runs `aretrieve(state.raw_issue_text, index=KBIndex.TECHNICAL)`, (c) drafts a reply that **cites at least one issue number in `#<digits>` form**, and appends `"bug_drafted"` to `execution_logs`. The mock GitHub fixture supplies deterministic issue numbers offline.
- **FR-5 (`feature_agent` node — task 4.3.5):** `async def feature_agent(state: AgentState) -> dict[str, object]` runs RAG over **both** indices (`KBIndex.BILLING` and `KBIndex.TECHNICAL`), drafts an acknowledgement, and **creates an internal-only synthetic ticket with no MCP write** — recorded purely by appending `"feature_logged"` (and an internal synthetic id, e.g. `"feature_ticket:<uuid>"`) to `execution_logs`. No GitHub/Postgres write tool is called (none exists; the servers are read-only).
- **FR-6 (`lockout_agent` node — task 4.3.6):** `async def lockout_agent(state: AgentState) -> dict[str, object]` (a) calls `get_customer_by_id(state.company_id)` on the Postgres MCP, (b) drafts an identity-verification explainer, and (c) **sets `requires_escalation = True` unconditionally** (regardless of confidence), appending `"lockout_escalated"` to `execution_logs`. `confidence_score` is left `>= 0` (never negative).
- **FR-7 (`supervisor_review` node — task 4.3.7):** `def supervisor_review(state: AgentState) -> dict[str, object]` (pure, synchronous): if `state.confidence_score < 0.70` **OR** `state.requires_escalation`, it appends `"ESCALATE"` to `execution_logs` and sets `requires_escalation = True`; otherwise it appends `"AUTO_APPROVED"`. The `0.70` threshold is the tunable from `docs/plan.md` § "Decisions Embedded in This Plan".
- **FR-8 (LLM drafting helper — `graph/llm.py`):** `async def draft_reply(*, intent, issue_text, context, facts) -> tuple[str, float]` returns `(draft_text, confidence)`. Provider selection: Claude Sonnet 4.6 if `ANTHROPIC_API_KEY` set, else `gpt-4o-mini` if `OPENAI_API_KEY` set, else a deterministic template. The template composes `facts` (e.g. tier, issue numbers) and a context snippet into a fixed-form reply, guaranteeing the test-asserted substrings without any network call. Confidence returned: classifier-seeded value, reduced toward a floor when `context` is empty (a grounding signal).
- **FR-9 (Partial-update return convention):** Every node returns a `dict[str, object]` of *changed fields only*. For the append-only `execution_logs` list, a node returns the **full extended list** (`state.execution_logs + [...]`), not a delta — this is correct under LangGraph's default last-writer-wins channel merge and remains correct if Phase 4.4 later adds an `Annotated[..., add]` reducer. Nodes never mutate `state` in place.
- **FR-10 (Namespace-package convention):** No `__init__.py` is added under `graph/` or `graph/nodes/`, consistent with the existing `mcp_postgres`, `mcp_github`, `classifier_api`, and `orchestrator/rag` packages (PEP 420; `04-langgraph-rag-spec.md` § 5.1 edge case 4).

---

## 4. Detailed Component Specifications & API Contracts

### 4.1 Interface Code & Data Shapes

**`estc/services/orchestrator/graph/llm.py` (shared drafting helper):**

```python
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

CONFIDENCE_FLOOR_NO_CONTEXT = 0.55  # grounding penalty when retrieval came back empty


@lru_cache(maxsize=1)
def _chat_model() -> Optional[object]:
    """Lazily build a LangChain ChatModel, or None for the offline template path."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model="claude-sonnet-4-6", temperature=0.2)
    if os.environ.get("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model="gpt-4o-mini", temperature=0.2)
    return None  # deterministic template fallback (CI / offline)


def _template_reply(intent: str, facts: dict[str, str], context: list[str]) -> str:
    """Deterministic, network-free draft. MUST emit the facts the node tests assert on
    (e.g. the subscription tier for billing, the #<n> issue refs for bug)."""
    lead = ", ".join(f"{k}: {v}" for k, v in facts.items()) or "no account facts available"
    snippet = (context[0][:400] + "...") if context else "no knowledge-base context found"
    return (
        f"[draft:{intent}] Thanks for reaching out. Based on your account ({lead}), "
        f"here is what we found: {snippet}"
    )


async def draft_reply(
    *,
    intent: str,
    issue_text: str,
    context: list[str],
    facts: dict[str, str],
) -> tuple[str, float]:
    """Return (draft_text, confidence). Uses an LLM when a key is present, else a
    deterministic template. Confidence is reduced when no context was retrieved."""
    confidence = 0.85 if context else CONFIDENCE_FLOOR_NO_CONTEXT
    model = _chat_model()
    if model is None:
        return _template_reply(intent, facts, context), confidence
    # LLM path: build a grounded prompt from facts + context, await model.ainvoke(...),
    # and return (response_text, confidence). The prompt MUST instruct the model to
    # incorporate `facts` verbatim so the same assertions hold (tier name, issue refs).
    ...
```

**`estc/services/orchestrator/graph/nodes/classify.py`:**

```python
from __future__ import annotations

from typing import Optional

import httpx

from estc.shared.config import Settings
from estc.shared.schemas.agent_state import AgentState

_VALID_INTENTS = {"billing", "bug", "feature", "lockout"}


async def classify(
    state: AgentState,
    *,
    client: Optional[httpx.AsyncClient] = None,  # injectable for httpx.MockTransport tests
) -> dict[str, object]:
    base = Settings().CLASSIFIER_API_URL
    owns_client = client is None
    client = client or httpx.AsyncClient(base_url=base, timeout=5.0)
    try:
        resp = await client.post("/classify", json={"text": state.raw_issue_text})
        resp.raise_for_status()
        body = resp.json()
    finally:
        if owns_client:
            await client.aclose()

    intent = body["intent"]
    confidence = float(body["confidence"])
    return {
        "intent": intent,
        "confidence_score": confidence,
        "execution_logs": state.execution_logs + [f"classified:{intent}"],
    }
```

**`estc/services/orchestrator/graph/nodes/router.py`:**

```python
from __future__ import annotations

from estc.shared.schemas.agent_state import AgentState

_ROUTE = {
    "billing": "billing_agent",
    "bug": "bug_agent",
    "feature": "feature_agent",
    "lockout": "lockout_agent",
}


def route_by_intent(state: AgentState) -> str:
    """Pure conditional-edge function. Unknown/None intent -> billing_agent (catch-all)."""
    return _ROUTE.get(state.intent or "", "billing_agent")
```

**`estc/services/orchestrator/graph/nodes/supervisor.py`:**

```python
from __future__ import annotations

from estc.shared.schemas.agent_state import AgentState

CONFIDENCE_THRESHOLD = 0.70  # tunable; docs/plan.md "Decisions Embedded in This Plan"


def supervisor_review(state: AgentState) -> dict[str, object]:
    escalate = state.confidence_score < CONFIDENCE_THRESHOLD or state.requires_escalation
    verdict = "ESCALATE" if escalate else "AUTO_APPROVED"
    return {
        "requires_escalation": bool(escalate),
        "execution_logs": state.execution_logs + [verdict],
    }
```

**`estc/services/orchestrator/graph/nodes/billing_agent.py` (representative worker shape):**

```python
from __future__ import annotations

from estc.services.mcp_postgres.server import get_subscription_status
from estc.services.orchestrator.graph.llm import draft_reply
from estc.services.orchestrator.rag.retriever import KBIndex, aretrieve
from estc.shared.schemas.agent_state import AgentState


async def billing_agent(state: AgentState) -> dict[str, object]:
    sub = await get_subscription_status(state.company_id)           # MCP (read-only)
    hits = await aretrieve(state.raw_issue_text, index=KBIndex.BILLING)
    context = [h.content for h in hits]
    facts = {"tier": sub.subscription_tier} if sub else {}          # tier asserted by AC-T3
    draft, confidence = await draft_reply(
        intent="billing", issue_text=state.raw_issue_text, context=context, facts=facts,
    )
    return {
        "retrieved_context": context,
        "agent_draft_response": draft,
        "confidence_score": confidence,
        "execution_logs": state.execution_logs + ["billing_drafted"],
    }
```

(`bug_agent`, `feature_agent`, `lockout_agent` follow the same shape, differing in their MCP/RAG calls and the breadcrumb/flag they write — see FR-4/5/6. `bug_agent` derives a `repo` from a new `ESTC_GITHUB_REPO` setting and ensures the draft includes a `#<digits>` issue reference; `lockout_agent` sets `requires_escalation=True`; `feature_agent` appends `"feature_logged"` and performs no MCP write.)

### 4.2 Endpoint / Method Contracts

| Node / Function | Signature | Reads | Writes (returned keys) | I/O |
|---|---|---|---|---|
| `classify` (4.3.1) | `async (state, *, client=None) -> dict` | `raw_issue_text` | `intent`, `confidence_score`, `execution_logs` | HTTP → classifier-api |
| `route_by_intent` (4.3.2) | `(state) -> str` | `intent` | *(returns next-node name)* | none (pure) |
| `billing_agent` (4.3.3) | `async (state) -> dict` | `company_id`, `raw_issue_text` | `retrieved_context`, `agent_draft_response`, `confidence_score`, `execution_logs` | Postgres MCP + RAG + LLM |
| `bug_agent` (4.3.4) | `async (state) -> dict` | `raw_issue_text` | `retrieved_context`, `agent_draft_response`, `confidence_score`, `execution_logs` | GitHub MCP + RAG + LLM |
| `feature_agent` (4.3.5) | `async (state) -> dict` | `raw_issue_text` | `retrieved_context`, `agent_draft_response`, `confidence_score`, `execution_logs` (+`feature_logged`) | RAG (both indices) + LLM |
| `lockout_agent` (4.3.6) | `async (state) -> dict` | `company_id`, `raw_issue_text` | `agent_draft_response`, `requires_escalation=True`, `confidence_score`, `execution_logs` | Postgres MCP + LLM |
| `supervisor_review` (4.3.7) | `(state) -> dict` | `confidence_score`, `requires_escalation` | `requires_escalation`, `execution_logs` (+`ESCALATE`/`AUTO_APPROVED`) | none (pure) |
| `draft_reply` (FR-8) | `async (*, intent, issue_text, context, facts) -> tuple[str, float]` | — | — | LLM or template |

- **Input shape:** all nodes take a single `AgentState` (worker/classify nodes additionally accept injectable clients via keyword-only args for testing).
- **Output shape:** node functions return `dict[str, object]` partial updates (FR-9); `route_by_intent` returns `str`; `draft_reply` returns `tuple[str, float]`.

---

## 5. Edge Cases & Error Handling

### 5.1 Anticipated Edge Cases

1. **Unknown / `None` intent at the router.** If `classify` failed to set `intent`, or the classifier returns a label outside the four known ones, `route_by_intent` falls back to `"billing_agent"` (the classifier's own catch-all default). The graph never dead-ends on an unroutable state.
2. **Company not found in Postgres.** `get_subscription_status` / `get_customer_by_id` return `None` for an unknown `company_id` (proven by `test_mcp_postgres.py`). `billing_agent` and `lockout_agent` must tolerate `None`: draft a reply with `facts={}` ("account record unavailable") rather than dereferencing `None`. `lockout_agent` still sets `requires_escalation=True`.
3. **Empty RAG retrieval.** `aretrieve` may return `[]` (e.g. an off-domain query or an unpopulated store). Nodes set `retrieved_context=[]` and `draft_reply` applies the `CONFIDENCE_FLOOR_NO_CONTEXT` penalty so a context-free draft naturally trends toward escalation at the supervisor.
4. **No LLM API key (the CI default).** `draft_reply` returns the deterministic template. This is the *normal* test path: AC-T3 (tier mention), AC-T4 (`#<digits>` issue ref), and the feature/lockout assertions must all hold against the template, not just against a live model.
5. **GitHub MCP in mock mode / repo with no matching issues.** `bug_agent` reads from `github_mock.json` (conftest forces this). If `search_issues` returns `[]`, the node must still emit a draft; to guarantee the `#<digits>` assertion, the bug draft cites the *first available* mock issue number, and the test fixture is expected to contain ≥ 1 issue for the configured repo (a fixture precondition the plan will verify).
6. **MCP transport choice.** Nodes call MCP tools as direct in-process coroutines (`await get_subscription_status(...)`), not via a fresh `fastmcp.Client(mcp)` per node. Rationale: the Phase 3 suite proves the coroutines are the stable, typed contract; a per-call client adds connection/teardown overhead inside the `< 10s` budget and an extra failure mode. The protocol's *security* guarantee (no raw SQL/shell) is preserved because nodes can only reach the predefined tool functions. Revisit in Phase 4.4 only if cross-process isolation becomes a requirement.
7. **Classifier HTTP failure / timeout.** `classify` lets a non-2xx raise (`resp.raise_for_status()`); Phase 4.4's `run_ticket` decides retry/propagation. The 5 s client timeout prevents an indefinite hang within the run budget. (A future hardening could fall back to a keyword heuristic, but Phase 4.3 fails fast and visibly.)
8. **Log append vs. overwrite under LangGraph merge.** Because `AgentState` is a plain Pydantic model with no channel reducer, returning a *delta* list would overwrite prior logs. Nodes therefore return the **full extended** `execution_logs` (FR-9). Documented here because it is the single most likely cross-node regression once the graph is wired.

### 5.2 Error Handling & State Recovery Matrix

| Trigger / Exception | Handled State / Action | Fallback Behavior / Mitigation |
|---|---|---|
| Unknown/`None` `state.intent` | `route_by_intent` returns `"billing_agent"` | Catch-all routing; never an unroutable dead-end (edge case 1) |
| `company_id` not in DB (`None` from MCP) | Node drafts with `facts={}` | "Account record unavailable" phrasing; `lockout_agent` still escalates (edge case 2) |
| `aretrieve` returns `[]` | `retrieved_context=[]`, confidence floored | Low confidence → `supervisor_review` escalates (edge cases 3, FR-8) |
| No `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` | `draft_reply` uses deterministic template | Network-free, assertion-stable drafts in CI (edge case 4) |
| GitHub MCP `search_issues` → `[]` | `bug_agent` cites first available mock issue | Fixture precondition ≥ 1 issue; draft still emits `#<digits>` (edge case 5) |
| Classifier API 5xx / timeout | `classify` raises (`raise_for_status` / 5 s timeout) | Propagates to `run_ticket` (4.4.2) → FastAPI surfaces 5xx; ticket not silently mis-routed (edge case 7) |
| LLM provider error (live path) | Exception bubbles from `ainvoke` | Caller (4.4) policy; offline template path is unaffected and used by CI |
| Node returns delta logs by mistake | Prior breadcrumbs overwritten | FR-9 mandates full extended list; covered by a multi-node log-accumulation test (edge case 8) |

---

## 6. Acceptance Criteria

### 6.1 Technical Acceptance Criteria

- **AC-T1 (`classify` via `MockTransport` — task 4.3.1 verify):** A test using `httpx.MockTransport` that returns `{"intent":"bug","confidence":0.85,"latency_ms":3.0}` makes `classify(AgentState(...))` return `intent == "bug"` and `confidence_score == 0.85`, and appends `"classified:bug"` to `execution_logs`. No real network call occurs.
- **AC-T2 (`route_by_intent` table — task 4.3.2 verify):** A table-driven test asserts all four mappings (`billing→billing_agent`, `bug→bug_agent`, `feature→feature_agent`, `lockout→lockout_agent`) **and** the `None`/unknown→`billing_agent` fallback.
- **AC-T3 (`billing_agent` mentions tier — task 4.3.3 verify):** Run against a seeded company whose `subscription_tier` is known; `state.agent_draft_response` contains that tier string (e.g. `"Enterprise"`). Passes offline via the template path.
- **AC-T4 (`bug_agent` cites an issue — task 4.3.4 verify):** `state.agent_draft_response` matches the regex `#\d+`. Passes with GitHub in mock mode.
- **AC-T5 (`feature_agent` logs — task 4.3.5 verify):** `"feature_logged"` is present in `state.execution_logs`, and no MCP write tool was invoked (feature node imports no write path; none exists).
- **AC-T6 (`lockout_agent` escalates — task 4.3.6 verify):** After the node runs, `requires_escalation is True` and `confidence_score >= 0`.
- **AC-T7 (`supervisor_review` both branches — task 4.3.7 verify):** Two scenarios — one with `confidence_score < 0.70`, one with `>= 0.70` and `requires_escalation=False` — produce `"ESCALATE"` and `"AUTO_APPROVED"` respectively; the low-confidence branch also flips `requires_escalation` to `True`.
- **AC-T8 (async + offline determinism):** The four worker nodes are `async def`; `route_by_intent` and `supervisor_review` are plain `def`. The whole node suite passes with no `GITHUB_PAT`, no LLM keys, and GitHub forced to mock (per `conftest.py`).
- **AC-T9 (log accumulation / FR-9):** A test threading state through `classify → (one worker) → supervisor_review` asserts `execution_logs` *accumulates* (length grows, earlier entries retained) rather than being overwritten.
- **AC-T10 (test suite green):** `.venv\Scripts\pytest estc/tests/test_graph_nodes.py -v` is all green with ≥ 9 cases covering AC-T1–AC-T9.

### 6.2 Business & Functional Alignment

- **AC-B1 (Topology fidelity, `design.md` § Component D):** The seven callables realize exactly `classify → router → {billing|bug|feature|lockout} → supervisor_review`, with intent from the local PyTorch service (never an LLM) and context only via MCP tool schemas.
- **AC-B2 (Grounding mandate, `design.md` § Component C):** Every worker draft is built from `retrieved_context`; an empty retrieval lowers confidence and biases toward escalation — the structural precondition for the Phase 4.5 Ragas Faithfulness/Context-Recall gates.
- **AC-B3 (Read-only security, `design.md` § 2):** No node composes SQL, shell, or GitHub REST; the feature node's "ticket creation" is internal-only with no MCP write — honoring the read-only constraint enforced at the DB-grant and GitHub-guard layers (plan 3.1.5 / 3.2.6).
- **AC-B4 (Escalation policy, plan 4.3.7):** The `0.70` threshold and the unconditional `lockout` escalation match the documented decisions; `supervisor_review` is the single chokepoint where the auto-approve-vs-human-review verdict is recorded.
- **AC-B5 (Phase 4.4 readiness):** All seven units are importable from `estc.services.orchestrator.graph.nodes.*`, take/return the agreed shapes, and need no signature change to be wired into `StateGraph(AgentState)` with `route_by_intent` as the conditional edge (task 4.4.1) — the consumer contract for the next phase.
- **AC-B6 (Offline-first parity, codebase convention):** Like the mock classifier (Phase 2) and the mock GitHub client (Phase 3.2.5), the node layer degrades deterministically with no external keys — preserving the project's "tests pass on a clean checkout" property.

---

**Open items for the execution plan (Phase 4.3 plan):**
1. `estc.shared.config.Settings` currently exposes no `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `ESTC_GITHUB_REPO`. The plan must add these (env-driven, all optional with safe defaults) before `bug_agent` and `graph/llm.py` can resolve their dependencies.
2. Confirm `github_mock.json` contains ≥ 1 issue for the chosen default repo (AC-T4 / edge case 5 precondition); if absent, the plan seeds the fixture.
3. Decide whether AC-T3 reuses an existing seeded `company_id` (e.g. `c-01` = `Enterprise`) or whether the billing test mocks the MCP tool directly to avoid a live-Postgres dependency in unit tests — the plan picks one and states it.
4. `requirements-orchestrator.txt` pins `langchain-openai` and `langchain-anthropic`; confirm both import cleanly so the lazy `_chat_model()` provider branches are valid even when unused.
