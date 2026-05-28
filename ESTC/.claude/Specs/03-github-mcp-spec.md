# Architectural Specification: Phase 3.2 — GitHub MCP Server
**Status:** DRAFT / PROPOSED
**Associated Tasks:** Tasks 3.2.1 – 3.2.7 (`docs/plan.md` § Phase 3.2)
**Target Files:**
- `estc/services/mcp-github/server.py`
- `estc/services/mcp-github/guards.py`
- `estc/services/mcp-github/clients.py`
- `estc/services/mcp-github/Dockerfile`
- `estc/tests/fixtures/github_mock.json`
- `estc/tests/test_mcp_github.py`
- `docker-compose.yml` (amended — add `mcp-github-server` service)

---


## 1. Executive Summary & Problem Statement

### 1.1 Objective & Context
This sub-phase delivers the **second of two Secure Context Layer servers** described in `docs/design.md` § 2 Component B. Where the PostgreSQL MCP server (Phase 3.1, [[01-postgres-mcp-spec]]) wraps the *transactional* system of record (customer health, subscription tier, account status), the GitHub MCP server wraps the *engineering* system of record: open bug tickets, recent code commits, and the most-recent deployment workflow run. Together the two servers form the read-only fact surface that LangGraph agent nodes hydrate `AgentState` from before drafting a reply.

Within the ESTC topology, the `bug_agent` node (Phase 4.3.4) is the primary consumer: when the classifier routes a ticket to `Technical Bug`, the agent must answer questions like *"is there already an open issue about a 500 on `/api/orders`?"* and *"what was the last deployment that touched this code path?"*. Those questions translate to `search_issues(repo, query, state="open")`, `list_recent_commits(repo, limit=5)`, and `get_deployment_log(repo)` tool calls — the three tools this phase ships. The `feature_agent` node (Phase 4.3.5) is a secondary consumer of `search_issues` (to detect duplicate feature asks).

This phase delivers the server process, its three tool schemas, a **regex-driven write-guard** that aborts module load if anyone ever registers a mutating tool (the GitHub equivalent of the database-grant defense in Phase 3.1.5), an **offline mock fallback** so the test suite and air-gapped dev boots succeed without a real `GITHUB_PAT`, an async wrapper around the sync `PyGithub==2.4.*` library (per the project's async-first MCP rule, see [[feedback-mcp-async]]), and the Docker Compose wiring for `mcp-github-server`.

### 1.2 Core Problem Statement
LangGraph nodes powered by `gpt-4o-mini` / `claude-sonnet-4-6` cannot be trusted with a GitHub Personal Access Token: a prompt-injection could induce them to close issues, force-push, delete branches, or merge PRs. We need a **narrow, typed, append-impossible** abstraction between the model and the GitHub API that (a) exposes only the read patterns the agents actually need, (b) makes mutation **structurally impossible** by enforcing a regex guard at module-import time against the tool registry, (c) degrades gracefully to a deterministic fixture when no token is available (so CI and offline dev still pass), and (d) is observable, containerized, independently restartable, and async-native to match the rest of the MCP and orchestrator runtime.

---

## 2. System Boundaries & Constraints

### 2.1 Architectural Boundaries
- **Upstream Trigger / Consumer:** The LangGraph orchestrator (`estc/services/orchestrator/`, Phase 4), specifically:
  - `bug_agent` node (Phase 4.3.4) → calls `search_issues` and `list_recent_commits`; cites issue numbers (`#<digit>`) in the draft response (this is the Plan 4.3.4 verification).
  - `bug_agent` node may also call `get_deployment_log` to mention "deployment X on YYYY-MM-DD may be related" in the draft.
  - `feature_agent` node (Phase 4.3.5) → calls `search_issues(state="open")` to detect duplicate feature requests before logging a synthetic ticket.
  - The Phase 3.3 exit-gate inspector run consumes the tool list.
- **Downstream Dependencies:**
  - **GitHub REST API v3** (via `PyGithub==2.4.*` from `requirements-orchestrator.txt`) when `GITHUB_PAT` is set. The token is read from `.env` (loaded via `estc/shared/config.py`, Phase 1.4.3) — keys: `GITHUB_PAT`.
  - **Offline fixture**: `estc/tests/fixtures/github_mock.json` — shipped in-repo, loaded when `GITHUB_PAT` is unset / empty. Returns deterministic, hand-curated `issues`, `commits`, and `deployments` arrays keyed by `repo`.
  - The `fastmcp==2.*` Python SDK for the server runtime and transport.
  - No database dependency. Unlike Phase 3.1, this server is stateless aside from an in-process LRU cache of GitHub responses (optional, see § 2.2 Resource Limits).

### 2.2 Technical & Operational Constraints
- **Performance / Latency:**
  - Per-tool round-trip ≤ **800 ms p95** when `GITHUB_PAT` is set and the GitHub API is reachable (network-bound; we don't control upstream latency).
  - Per-tool round-trip ≤ **20 ms p95** in mock mode (fixture is a small in-memory dict).
  - The async wrapper around PyGithub (which is sync) uses `asyncio.to_thread()` to keep FastMCP's event loop unblocked — every blocking `Github.get_repo(...).get_issues(...)` call is dispatched off-loop. This is mandatory per [[feedback-mcp-async]].
- **Security & Compliance:**
  - The PAT used by this server **must be granted minimum scopes only**: `public_repo` (or `repo` if private repos are in scope), `read:org`, and nothing else. No `write:*`, no `admin:*`, no `delete_repo`. This is enforced operationally (in the secret-issuance runbook) and verified by a startup log line that prints the token's scopes (via `Github.get_user().get_authorization_url()` is not available for fine-grained PATs — use `requests.get('https://api.github.com', headers=...).headers.get('X-OAuth-Scopes')` once at boot).
  - The server **must not register any tool whose name matches the regex `r"(?i)(create|update|delete|merge|close|patch|put|post|add|remove|fork|rename|transfer|archive)"`**. This is task 3.2.6's hard constraint. The guard is enforced at registration time by a decorator that wraps `mcp.tool`, raising `RuntimeError` synchronously before module init completes.
  - All identifiers passed to PyGithub are treated as **opaque strings**, never concatenated into URLs by hand. PyGithub itself encodes URL components, but we additionally validate that `repo` matches `r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$"` before any call — a structural defense against tool-arg-driven SSRF if PyGithub is ever swapped out.
  - PII: GitHub commit author emails are returned. We do not mask them at this layer (matches the PostgreSQL server's posture on `technical_poc_email`).
- **Resource Limits:**
  - Memory ceiling: 256 MiB in the container (Docker Compose `mem_limit`).
  - GitHub REST API rate limit: 5000 req/h authenticated. The server logs the `X-RateLimit-Remaining` header after every call at DEBUG level. Below 100, the server emits a WARN log; below 10, tools return a `ToolError("github_rate_limited")` so the supervisor node can escalate rather than auto-approve a stale draft.
  - `list_recent_commits.limit` and the page size of `search_issues` are **server-side clamped to `[1, 50]`** to bound response payload and rate-limit cost. This mirrors the Phase 3.1 `list_delinquent_accounts.limit` clamp pattern.
  - Optional in-process TTL cache (60s) keyed by `(tool_name, args)` to absorb the burst pattern when multiple agent nodes run in parallel and all query the same `repo` — sized at 256 entries with LRU eviction. Disabled when in mock mode.

---

## 3. Functional Requirements

- **FR-1 (Server Identity, task 3.2.1):** Initialize a FastMCP `Server` instance named exactly `"estc-github"`. The name is the discovery handle used by the orchestrator's MCP client registry and by `mcp-inspector` in the Phase 3.3 exit-gate verification. On startup the server logs `tools registered: 3` (verbatim — this is the string the Phase 3.2.7 verification greps for).
- **FR-2 (Tool: `search_issues`, task 3.2.2):** Expose an async tool `search_issues(repo: str, query: str, state: str = "open") -> list[IssueSummary]` returning a JSON-serializable list of objects with keys `number`, `title`, `url`, `labels`. Implementation calls `PyGithub.search_issues(query=f"repo:{repo} {query} state:{state}")` wrapped in `asyncio.to_thread()`. Returns `[]` (empty list) when no matches; never raises on miss.
- **FR-3 (Tool: `list_recent_commits`, task 3.2.3):** Expose an async tool `list_recent_commits(repo: str, limit: int = 5) -> list[CommitSummary]` returning objects with keys `sha`, `author`, `message`. `limit` is clamped server-side to `[1, 50]`. Implementation calls `Github.get_repo(repo).get_commits()[:limit]` wrapped in `asyncio.to_thread()`.
- **FR-4 (Tool: `get_deployment_log`, task 3.2.4):** Expose an async tool `get_deployment_log(repo: str) -> DeploymentSummary | None` returning the **most-recent** GitHub Actions deployment workflow run as `{workflow, status, conclusion, run_url}`. "Deployment workflow" means the latest workflow run whose `event` is `deployment` OR whose `name` contains the substring `deploy` (case-insensitive). Returns `None` if no such run exists.
- **FR-5 (Write-Method Guard, task 3.2.6):** Expose a `register_readonly_tool` decorator wrapping `mcp.tool` that inspects the wrapped function's `__name__` against the regex `r"(?i)(create|update|delete|merge|close|patch|put|post|add|remove|fork|rename|transfer|archive)"` and raises `RuntimeError(f"Write-method tool name forbidden: {name}")` if it matches. **All three FR-2/3/4 tools are registered through this decorator**, not the raw `@mcp.tool`. A negative-path unit test attempts to register `close_issue` through the decorator and asserts `RuntimeError` is raised.
- **FR-6 (Offline Mock Fallback, task 3.2.5):** At module load, the server reads `GITHUB_PAT`. If unset or empty, it constructs an in-process `MockGitHubClient` backed by `estc/tests/fixtures/github_mock.json` (path overridable via `GITHUB_MOCK_PATH` env var). The fixture's top-level shape is `{ "<owner>/<repo>": { "issues": [...], "commits": [...], "deployments": [...] } }`. All three tools must return shape-identical results from mock or live mode — callers cannot tell which is active. The fixture ships with **at least one entry** for `kalai555777/Enterprise-Support-Triage-Copilot` (the project's own repo), pre-populated with one open issue, five commits, and one deployment run — enough for `pytest tests/test_mcp_github.py -v` to pass green with `GITHUB_PAT` unset (Plan verification for 3.2.5).
- **FR-7 (Containerization & Compose Wiring, task 3.2.7):** A `services/mcp-github/Dockerfile` builds the server on `python:3.11-slim`. `docker-compose.yml` is amended to add `mcp-github-server` as a new service that mounts the fixture path and reads `GITHUB_PAT` from `.env`. Service has a healthcheck that calls a tiny `/healthz`-equivalent — since MCP is not HTTP, the healthcheck shells `python -c "from estc.services.mcp_github.server import mcp; import sys; sys.exit(0 if len(mcp._tool_manager._tools)==3 else 1)"`. On boot the logs must contain the literal string `tools registered: 3` (Plan verification for 3.2.7).

---

## 4. Detailed Component Specifications & API Contracts

### 4.1 Interface Code & Data Shapes

**Pydantic DTOs (`services/mcp-github/server.py`, top of module):**

```python
from pydantic import BaseModel
from typing import Literal, Optional

IssueState = Literal["open", "closed", "all"]

class IssueSummary(BaseModel):
    number: int
    title: str
    url: str
    labels: list[str]

class CommitSummary(BaseModel):
    sha: str
    author: str        # author.login if available, else commit.author.email
    message: str       # first line of commit.message

class DeploymentSummary(BaseModel):
    workflow: str      # workflow run.name
    status: str        # 'completed' | 'in_progress' | 'queued'
    conclusion: Optional[str]   # 'success' | 'failure' | 'cancelled' | None
    run_url: str       # html_url
```

**Write-guard decorator (`services/mcp-github/guards.py`):**

```python
import re
from typing import Callable, TypeVar

_FORBIDDEN = re.compile(
    r"(?i)(create|update|delete|merge|close|patch|put|post|"
    r"add|remove|fork|rename|transfer|archive)"
)

F = TypeVar("F", bound=Callable)

def register_readonly_tool(mcp):
    """Returns a decorator that asserts the tool name is read-only,
    then forwards registration to `mcp.tool`. Fails at import time."""
    def _decorator(fn: F) -> F:
        name = fn.__name__
        if _FORBIDDEN.search(name):
            raise RuntimeError(f"Write-method tool name forbidden: {name}")
        return mcp.tool(fn)
    return _decorator
```

**GitHub client abstraction (`services/mcp-github/clients.py`):**

```python
import asyncio, json, os
from pathlib import Path
from github import Github   # PyGithub

class MockGitHubClient:
    """File-backed deterministic stand-in. Used when GITHUB_PAT is unset."""
    def __init__(self, fixture_path: Path):
        self._data = json.loads(fixture_path.read_text())

    async def search_issues(self, repo, query, state):
        bucket = self._data.get(repo, {}).get("issues", [])
        # state filter; ignore `query` for fixture simplicity (matches all)
        return [i for i in bucket if state == "all" or i["state"] == state]

    async def list_recent_commits(self, repo, limit):
        return self._data.get(repo, {}).get("commits", [])[:limit]

    async def get_deployment_log(self, repo):
        deps = self._data.get(repo, {}).get("deployments", [])
        return deps[0] if deps else None

class LiveGitHubClient:
    """Thin async wrapper around sync PyGithub, via asyncio.to_thread."""
    def __init__(self, token: str):
        self._gh = Github(token)

    async def search_issues(self, repo, query, state):
        q = f"repo:{repo} {query} state:{state}"
        def _call():
            return [
                {
                    "number": i.number,
                    "title": i.title,
                    "url": i.html_url,
                    "labels": [l.name for l in i.labels],
                }
                for i in self._gh.search_issues(query=q)[:50]
            ]
        return await asyncio.to_thread(_call)

    async def list_recent_commits(self, repo, limit):
        def _call():
            return [
                {
                    "sha": c.sha,
                    "author": (c.author.login if c.author
                               else c.commit.author.email),
                    "message": c.commit.message.split("\n", 1)[0],
                }
                for c in list(self._gh.get_repo(repo).get_commits()[:limit])
            ]
        return await asyncio.to_thread(_call)

    async def get_deployment_log(self, repo):
        def _call():
            runs = self._gh.get_repo(repo).get_workflow_runs()
            for run in runs[:25]:   # scan first page
                if run.event == "deployment" or "deploy" in run.name.lower():
                    return {
                        "workflow": run.name,
                        "status": run.status,
                        "conclusion": run.conclusion,
                        "run_url": run.html_url,
                    }
            return None
        return await asyncio.to_thread(_call)
```

**Server bootstrap & tool registration (`services/mcp-github/server.py`):**

```python
import os, logging, asyncio
from pathlib import Path
from fastmcp import FastMCP
from .guards import register_readonly_tool
from .clients import LiveGitHubClient, MockGitHubClient
# ... DTO imports ...

log = logging.getLogger("estc.mcp-github")
mcp = FastMCP("estc-github")  # FR-1

_token = os.environ.get("GITHUB_PAT", "").strip()
if _token:
    _client = LiveGitHubClient(_token)
    _mode = "live"
else:
    _fixture = Path(os.environ.get(
        "GITHUB_MOCK_PATH",
        "estc/tests/fixtures/github_mock.json",
    ))
    _client = MockGitHubClient(_fixture)
    _mode = "mock"

tool = register_readonly_tool(mcp)  # FR-5 — every tool below routes through this

@tool
async def search_issues(repo: str, query: str,
                        state: str = "open") -> list[IssueSummary]:
    _validate_repo(repo)
    rows = await _client.search_issues(repo, query, state)
    return [IssueSummary(**r) for r in rows]

@tool
async def list_recent_commits(repo: str,
                              limit: int = 5) -> list[CommitSummary]:
    _validate_repo(repo)
    limit = max(1, min(50, limit))
    rows = await _client.list_recent_commits(repo, limit)
    return [CommitSummary(**r) for r in rows]

@tool
async def get_deployment_log(repo: str) -> DeploymentSummary | None:
    _validate_repo(repo)
    row = await _client.get_deployment_log(repo)
    return DeploymentSummary(**row) if row else None

import re
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
def _validate_repo(repo: str) -> None:
    if not _REPO_RE.match(repo):
        raise ValueError(f"Invalid repo identifier: {repo!r}")

async def main() -> None:
    log.info("estc-github starting in %s mode; tools registered: 3", _mode)
    await mcp.run_async()

if __name__ == "__main__":
    asyncio.run(main())
```

**Fixture skeleton (`estc/tests/fixtures/github_mock.json`):**

```json
{
  "kalai555777/Enterprise-Support-Triage-Copilot": {
    "issues": [
      {
        "number": 42,
        "title": "500 error on /api/orders for Enterprise tier customers",
        "url": "https://github.com/kalai555777/Enterprise-Support-Triage-Copilot/issues/42",
        "labels": ["bug", "priority:high"],
        "state": "open"
      }
    ],
    "commits": [
      {"sha": "abc123", "author": "kalai555777", "message": "fix: handle null company_id"},
      {"sha": "def456", "author": "kalai555777", "message": "feat: add /api/orders endpoint"},
      {"sha": "ghi789", "author": "kalai555777", "message": "chore: bump deps"},
      {"sha": "jkl012", "author": "kalai555777", "message": "docs: README update"},
      {"sha": "mno345", "author": "kalai555777", "message": "ci: add e2e workflow"}
    ],
    "deployments": [
      {
        "workflow": "Deploy to staging",
        "status": "completed",
        "conclusion": "success",
        "run_url": "https://github.com/kalai555777/Enterprise-Support-Triage-Copilot/actions/runs/9999"
      }
    ]
  }
}
```

**Docker Compose delta (`docker-compose.yml`, FR-7):**

```yaml
services:
  mcp-github-server:        # new (task 3.2.7)
    build: ./services/mcp-github
    env_file: .env
    environment:
      GITHUB_MOCK_PATH: /app/tests/fixtures/github_mock.json
    volumes:
      - ./estc/tests/fixtures:/app/tests/fixtures:ro
    healthcheck:
      test: ["CMD-SHELL",
             "python -c \"from estc.services.mcp_github.server import mcp; \
              import sys; \
              sys.exit(0 if len(mcp._tool_manager._tools)==3 else 1)\""]
      interval: 10s
      timeout: 5s
      retries: 3
    mem_limit: 256m
    networks: [estc-net]
```

### 4.2 Endpoint / Method Contracts

The MCP surface is not HTTP — tools are invoked over the SDK transport. Three contracts are exposed:

- **Tool `search_issues`**
  - Input: `{"repo": str, "query": str, "state": "open"|"closed"|"all" = "open"}`. `repo` must match `^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$`.
  - Output: `list[IssueSummary]`. Empty list is a valid response. Capped server-side to 50 items.

- **Tool `list_recent_commits`**
  - Input: `{"repo": str, "limit": int = 5}`. `repo` validated; `limit` clamped to `[1, 50]`.
  - Output: `list[CommitSummary]`, newest-first (GitHub default order).

- **Tool `get_deployment_log`**
  - Input: `{"repo": str}`. `repo` validated.
  - Output: `DeploymentSummary | null`. Null when the repo has no workflow run matching the deployment heuristic.

---

## 5. Edge Cases & Error Handling

### 5.1 Anticipated Edge Cases
1. **`GITHUB_PAT` unset at boot**: The server transparently switches to mock mode and logs `estc-github starting in mock mode; tools registered: 3`. Tests at `tests/test_mcp_github.py` rely on this — the Plan 3.2.5 verification explicitly runs `pytest` with `GITHUB_PAT` unset.
2. **Repo not found / private + no access** (`search_issues`, `list_recent_commits`, `get_deployment_log`): PyGithub raises `UnknownObjectException`. The tool catches it at the boundary and re-raises as MCP `ToolError("repo_not_found")`. The orchestrator's `bug_agent` interprets this as "cite no issue" and the supervisor node decides if escalation is warranted.
3. **Empty search result**: `search_issues` returns `[]`. Not an error. `bug_agent` then drafts a reply that says "I couldn't find an existing issue, opening this as new …" — that's the agent's job, not ours.
4. **Repo has no workflow runs / no deployment-shaped runs**: `get_deployment_log` returns `None`. Same null-as-fact pattern as the PostgreSQL `get_customer_by_id` miss in [[01-postgres-mcp-spec]] § 5.1.
5. **`limit=0` or negative** (`list_recent_commits`): Server clamps to `1` and proceeds — matches the PostgreSQL server's clamp policy for hallucinated LLM args.
6. **Rate-limit exhaustion (`X-RateLimit-Remaining < 10`)**: Next tool call raises `ToolError("github_rate_limited")`. Supervisor escalates the ticket. (Cache TTL of 60s is designed to push burst patterns away from this floor.)
7. **Malformed `repo` argument** (e.g. `"; rm -rf /"` or `"owner/repo/extra"`): `_validate_repo` raises `ValueError` before any network call. MCP transport surfaces this as a validation error to the caller.
8. **Mock fixture missing for a queried repo**: Mock client returns `[]` / `None` (the `.get(repo, {}).get(...)` chain). Test suite includes a deliberate "unknown repo" assertion to lock this behavior in.
9. **Attempted registration of a write-shaped tool** (defect signal, caught in CI): Module load itself fails with `RuntimeError("Write-method tool name forbidden: <name>")` before the server ever binds. The Phase 3.2.6 negative test verifies this.

### 5.2 Error Handling & State Recovery Matrix

| Trigger / Exception | Handled State / Action | Fallback Behavior / Mitigation |
|---|---|---|
| `GITHUB_PAT` unset / empty | Bootstrap selects `MockGitHubClient`; log `mock mode` | Tests pass offline; container boots clean without secrets |
| `UnknownObjectException` (repo 404) | Caught at tool boundary; re-raised as MCP `ToolError("repo_not_found")` | `bug_agent` produces a "no related issue cited" draft; supervisor decides on escalation |
| `RateLimitExceededException` or remaining < 10 | Re-raised as MCP `ToolError("github_rate_limited")` | Supervisor flips `requires_escalation=True`; ticket lands in operator queue |
| `BadCredentialsException` (token revoked / wrong scope) | Logged at ERROR; re-raised as MCP `ToolError("github_auth_failed")` | Operator alerted via dashboard; server stays up but every live call fails until token rotated |
| `ValueError` from `_validate_repo` | Raised to MCP SDK before network call | Caller sees protocol-level validation error; no GitHub hit, no rate-limit cost |
| `RuntimeError("Write-method tool name forbidden: …")` | **Server fails to import** (defect signal, not a runtime case) | CI red; PR cannot merge. Same gate semantics as the PostgreSQL `InsufficientPrivilege` row in [[01-postgres-mcp-spec]] § 5.2 |
| `limit` outside `[1, 50]` | Clamped silently to bounds | No error returned; matches FR-3 contract |
| `asyncio.to_thread` worker raises any other `GithubException` | Logged at WARN; re-raised as MCP `ToolError("github_upstream_error")` | Supervisor escalates; LangSmith trace captures the underlying exception class |
| Network timeout on GitHub API call | PyGithub raises after 15s default; mapped to `ToolError("github_unreachable")` | Same as the PostgreSQL `OperationalError` row in [[01-postgres-mcp-spec]] § 5.2 — escalation |
| Tool args invalid type (e.g. `limit="five"`) | MCP SDK rejects at schema validation before tool body | No remote hit; caller sees validation error |

---

## 6. Acceptance Criteria

### 6.1 Technical Acceptance Criteria
- **AC-T1 (Server identity, task 3.2.1):** `.venv\Scripts\python -c "from estc.services.mcp_github.server import mcp; print(mcp.name)"` prints exactly `estc-github`. Server logs include the literal string `tools registered: 3` on startup.
- **AC-T2 (Tool surface, tasks 3.2.2–3.2.4):** `mcp-inspector ./services/mcp-github/server.py` lists **exactly three** tools — `search_issues`, `list_recent_commits`, `get_deployment_log` — with the parameter shapes given in §4.1, and **no other** tools.
- **AC-T3 (Mock-mode functional correctness, task 3.2.5):** With `GITHUB_PAT` unset, `.venv\Scripts\pytest tests/test_mcp_github.py -v` reports **all green**. The suite includes: one happy-path test per tool (using the seeded fixture for `kalai555777/Enterprise-Support-Triage-Copilot`), one unknown-repo test (asserts `[]` / `None`), one `limit` clamp test, one repo-validation test (asserts `ValueError` on `"; rm -rf /"`), and one inspector test verifying exactly three tools registered.
- **AC-T4 (Live-mode smoke, tasks 3.2.2–3.2.4):** With `GITHUB_PAT` set to a valid token, an integration test (excluded from the default `pytest` run, gated by `GITHUB_INTEGRATION=1`) calls `search_issues("kalai555777/Enterprise-Support-Triage-Copilot", "test", "open")` and asserts the result is a `list[IssueSummary]` (may be empty); calls `list_recent_commits` and asserts `len(result) == 5`; calls `get_deployment_log` and asserts the result is `None` or a `DeploymentSummary`.
- **AC-T5 (Write-method guard, task 3.2.6):**
  ```python
  with pytest.raises(RuntimeError, match="Write-method tool name forbidden"):
      @register_readonly_tool(mcp)
      async def close_issue(repo: str, number: int): ...
  ```
  passes. Test also iterates over the forbidden verbs (`create`, `update`, `delete`, `merge`, `close`, `patch`, `put`, `post`, `add`, `remove`, `fork`, `rename`, `transfer`, `archive`) and asserts each one triggers the guard.
- **AC-T6 (Async correctness, [[feedback-mcp-async]]):** Every `@register_readonly_tool` function is `async def`. Static review confirms zero sync `def` tool registrations. `pytest` markers use `@pytest.mark.asyncio`. All PyGithub calls inside `LiveGitHubClient` are wrapped in `asyncio.to_thread` — grep `server.py + clients.py` shows zero direct PyGithub calls outside `to_thread`.
- **AC-T7 (Repo-arg validation):** `search_issues(repo="not-a-repo")` raises `ValueError` before any network call. SSRF-shaped args (`"http://evil.com/x"`, `"owner/repo/../../etc/passwd"`) likewise rejected at `_validate_repo`.
- **AC-T8 (Latency in mock mode):** A pytest-bench loop measures p95 ≤ 20 ms across 50 sequential mock-mode `search_issues` calls.
- **AC-T9 (Container health, task 3.2.7):** `docker compose up -d mcp-github-server` followed by `docker compose ps mcp-github-server` shows the service `healthy`. `docker compose logs mcp-github-server` contains the literal string `tools registered: 3`.

### 6.2 Business & Functional Alignment
- **AC-B1 (Design fidelity):** The three tools map 1:1 to the "GitHub Server: repository issue states, bug trackers, and recent deployment commit logs" responsibility named in `design.md` § 2 Component B. `search_issues` → issue states + bug trackers; `list_recent_commits` → recent commits; `get_deployment_log` → deployment commit logs.
- **AC-B2 (Security posture, two-layer enforcement):** The protocol-surface guard (`register_readonly_tool` regex) plus the operational PAT-scope policy (`public_repo` / `read:org` only) honors design.md § 2's constraint "The orchestration model cannot execute raw SQL queries or touch bash command-lines." The regex guard is the GitHub analogue of the Phase 3.1 DB `GRANT SELECT` — defense-in-depth, not single-layer.
- **AC-B3 (Downstream consumability):** A subsequent Phase 4.3.4 (`bug_agent`) integration test, when wired against this server, can produce a draft response that contains `#<digit>` (the Plan 4.3.4 verification). I.e. the `IssueSummary.number` field carries the integer the agent prompt template needs.
- **AC-B4 (Offline-capable CI):** The mock fallback means `pytest` and `docker compose up` succeed on a fresh checkout with no `GITHUB_PAT`. New contributors can run the full test suite without provisioning a token. This is the Plan 3.2.5 verification.
- **AC-B5 (Phase 3.3 exit-gate readiness):** Together with [[01-postgres-mcp-spec]], this phase satisfies the Phase 3.3.1 exit gate ("MCP inspector against both servers, only read-style tools appear; tool list contains the 3 + 3 tool names above and no others"). The 6 tools are: `get_customer_by_id`, `get_subscription_status`, `list_delinquent_accounts`, `search_issues`, `list_recent_commits`, `get_deployment_log`.
- **AC-B6 (Async-first runtime, [[feedback-mcp-async]]):** All tools are async, all GitHub calls are dispatched via `asyncio.to_thread`. This preserves the FastMCP event loop's concurrency for the orchestrator's parallel-fan-out pattern in Phase 4 (multiple agents may hit MCP tools simultaneously while a long-running RAG retrieval is in flight).
