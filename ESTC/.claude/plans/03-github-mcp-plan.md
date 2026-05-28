# Execution Plan: Phase 3.2 — GitHub MCP Server
**Source spec:** `.CLAUDE/Specs/03-github-mcp-spec.md`
**Source plan section:** `docs/plan.md` § Phase 3.2 (tasks 3.2.1 – 3.2.7)
**Status:** AWAITING APPROVAL — no code to be executed until user replies `Proceed`.

---

## Context

This plan operationalizes Phase 3.2 of the ESTC roadmap: a read-only **GitHub MCP Server** that fronts GitHub repository state (issues, recent commits, latest deployment workflow) for the LangGraph orchestrator's `bug_agent` and `feature_agent` nodes (Phase 4). The work has three threads that must be done in order:

1. **Server runtime** (Python, single file): a `fastmcp` server named `estc-github` exposing exactly three tools — `search_issues`, `list_recent_commits`, `get_deployment_log` — every PyGithub call routed through `asyncio.to_thread`, every registration routed through a regex write-guard that fails at import if any mutating name slips in.
2. **Offline fallback** (JSON fixture): a `MockGitHubClient` in the same `server.py` reads `estc/tests/fixtures/github_mock.json` when `GITHUB_PAT` is unset — so tests, CI, and air-gapped boots succeed without a real token.
3. **Integration**: in-process pytest harness (using `fastmcp.Client`), Dockerfile, and a new compose service `mcp-github-server` matching the existing `mcp-postgres-server` shape (no healthcheck, `stdin_open: true`, `tty: true`).

The file layout deliberately mirrors the Phase 3.1 server: a **single** `estc/services/mcp_github/server.py` holds DTOs, write-guard decorator, repo-arg validator, both client classes, lazy-init singleton, and `main()`. No `guards.py` / `clients.py` split.

Every step below ends with a **Verify** command. The shell is **PowerShell 5.1**. A step is "done" only when its verification passes.

---

## Pre-Flight (read-only sanity checks before any change)

- [ ] **PF-1** Confirm spec exists and is the version this plan targets.
  **Verify:** `Get-Content .CLAUDE/Specs/03-github-mcp-spec.md | Select-String "estc-github"` returns ≥ 1 match.
- [ ] **PF-2** Confirm Phase 3.1 artifacts the plan mirrors are in place.
  **Verify:** `Test-Path estc/services/mcp_postgres/server.py, estc/tests/test_mcp_postgres.py, estc/tests/conftest.py` returns `True, True, True`.
- [ ] **PF-3** Confirm `docker-compose.yml` currently has `mcp-postgres-server` (the structural sibling we copy).
  **Verify:** `docker compose config --services` lists `mcp-postgres-server`.
- [ ] **PF-4** Confirm the venv is the active Python 3.11 toolchain.
  **Verify:** `.venv\Scripts\python --version` reports `Python 3.11.*`.
- [ ] **PF-5** Confirm `fastmcp` is installed and exposes the in-process `Client` used by tests.
  **Verify:** `.venv\Scripts\python -c "from fastmcp import FastMCP, Client; print(FastMCP, Client)"` exits 0.
- [ ] **PF-6** Confirm `PyGithub` is installable / installed.
  **Verify:** `.venv\Scripts\pip show PyGithub` exits 0. If absent, record the gap for step 3.2.1-a.
- [ ] **PF-7** Confirm the empty service directory exists from earlier scaffolding (Phase 1.1.1).
  **Verify:** `Test-Path estc/services/mcp_github` returns `True`.
- [ ] **PF-8** Confirm `GITHUB_PAT` is exposed by the shared Settings class (Phase 1.4.3) and listed in `.env.example`.
  **Verify:** `.venv\Scripts\python -c "from estc.shared.config import Settings; print(hasattr(Settings(), 'GITHUB_PAT'))"` prints `True` AND `Get-Content .env.example | Select-String '^GITHUB_PAT='` matches.
- [ ] **PF-9** Confirm the orchestrator-wide `estc-net` network is declared (we attach the new service to it).
  **Verify:** `docker compose config | Select-String 'estc-net'` returns ≥ 1 match.

---

## Task 3.2.1 — MCP Server Bootstrap

### 3.2.1-a Dependency pins (only if PF-5 or PF-6 flagged a gap)
- [ ] Confirm `fastmcp>=3.3.0` and `PyGithub>=2.4.0` in `requirements-orchestrator.txt`. Install in venv if drifted.
  **Verify:** `.venv\Scripts\pip install -r requirements-orchestrator.txt; .venv\Scripts\python -c "import fastmcp, github; print(fastmcp.__version__, github.__version__)"` prints a ≥ 3.3 and a ≥ 2.4 version respectively.

### 3.2.1-b Package skeleton
- [ ] Add `estc/services/mcp_github/__init__.py` (empty) so it's an importable package: `estc.services.mcp_github`.
  **Verify:** `.venv\Scripts\python -c "import estc.services.mcp_github"` exits 0 (will error until 3.2.1-c actually creates a `server.py`; this verify runs together with 3.2.1-c).

### 3.2.1-c Server identity
- [ ] Create `estc/services/mcp_github/server.py` containing the bootstrap from spec §4.1: `from fastmcp import FastMCP; mcp = FastMCP("estc-github")`. Include the Windows event-loop policy guard from `mcp_postgres/server.py:9-10`. **Do not** read `GITHUB_PAT` at module top — defer to lazy `_get_client()` (next task).
  **Verify:** `.venv\Scripts\python -c "from estc.services.mcp_github.server import mcp; print(mcp.name)"` prints exactly `estc-github`. Matches AC-T1.

### 3.2.1-d Settings extension
- [ ] Append `GITHUB_MOCK_PATH=estc/tests/fixtures/github_mock.json` to `.env.example`. The existing `Settings.GITHUB_PAT: str | None` (Phase 1.4.3) is reused as-is. The server reads `os.environ["GITHUB_PAT"]` / `os.environ.get("GITHUB_MOCK_PATH", ...)` directly inside `_get_client()` — no Settings class change needed.
  **Verify:** `Get-Content .env.example | Select-String '^GITHUB_MOCK_PATH='` matches.

### 3.2.1-e Write-method guard (FR-5 — declared up-front, used by every tool below)
- [ ] In `server.py`, define `_FORBIDDEN = re.compile(r"(?i)(create|update|delete|merge|close|patch|put|post|add|remove|fork|rename|transfer|archive)")` and `register_readonly_tool(mcp)` returning a decorator that raises `RuntimeError(f"Write-method tool name forbidden: {name}")` when the wrapped function's `__name__` matches `_FORBIDDEN`, otherwise forwards to `mcp.tool`. This is the **only** registration path used in this file — `@mcp.tool` directly is forbidden by convention.
  **Verify:** `.venv\Scripts\python -c "from estc.services.mcp_github.server import register_readonly_tool, mcp; @register_readonly_tool(mcp)
async def close_issue(): pass" 2>&1 | Select-String "Write-method tool name forbidden: close_issue"` matches.

### 3.2.1-f Lazy client init (mirrors `_get_pool()` in `mcp_postgres/server.py:49-57`)
- [ ] Define `_client` module-level singleton + `_get_client()` that on first call reads `GITHUB_PAT`. Non-empty → `LiveGitHubClient(token)`; empty → `MockGitHubClient(Path(os.environ.get("GITHUB_MOCK_PATH", "estc/tests/fixtures/github_mock.json")))`. Both classes defined in this same `server.py` (no module split).
- [ ] Add `_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")` and `_validate_repo(repo: str) -> None` that raises `ValueError` on mismatch.
  **Verify:** `.venv\Scripts\python -c "import os; os.environ.pop('GITHUB_PAT', None); from estc.services.mcp_github.server import _get_client; c=_get_client(); print(type(c).__name__)"` prints `MockGitHubClient`.

---

## Task 3.2.2 — Tool: `search_issues`

- [ ] Define Pydantic DTO `IssueSummary` (fields `number: int`, `title: str`, `url: str`, `labels: list[str]`) at the top of `server.py`.
- [ ] On `MockGitHubClient`, define `async def search_issues(repo, query, state)` that returns `self._data.get(repo, {}).get("issues", [])` filtered by `state` (or all if `state == "all"`). The `query` arg is accepted but not applied to the fixture (deterministic).
- [ ] On `LiveGitHubClient`, define `async def search_issues(repo, query, state)` that wraps `self._gh.search_issues(query=f"repo:{repo} {query} state:{state}")[:50]` inside `asyncio.to_thread(_call)`, returning a list of dicts shaped exactly like `IssueSummary`.
- [ ] Register the public tool `search_issues(repo: str, query: str, state: str = "open") -> list[IssueSummary]` through `@register_readonly_tool(mcp)`. Body: `_validate_repo(repo)`, then `rows = await _get_client().search_issues(repo, query, state)`, then `return [IssueSummary(**r) for r in rows]`.
- [ ] Wrap `github.UnknownObjectException` → MCP `ToolError("repo_not_found")`; wrap `github.RateLimitExceededException` → `ToolError("github_rate_limited")` per spec §5.2.
  **Verify:** `.venv\Scripts\python -c "import asyncio, os; os.environ.pop('GITHUB_PAT', None); from estc.services.mcp_github.server import search_issues; r=asyncio.run(search_issues('kalai555777/Enterprise-Support-Triage-Copilot','x','open')); print(len(r), r[0].number)"` prints a count ≥ 1 and an integer issue number (assuming the fixture from 3.2.5-a is in place).

---

## Task 3.2.3 — Tool: `list_recent_commits`

- [ ] Define Pydantic DTO `CommitSummary` (`sha: str`, `author: str`, `message: str`).
- [ ] On `MockGitHubClient.list_recent_commits(repo, limit)`: return `self._data.get(repo, {}).get("commits", [])[:limit]`.
- [ ] On `LiveGitHubClient.list_recent_commits(repo, limit)`: wrap `list(self._gh.get_repo(repo).get_commits()[:limit])` in `asyncio.to_thread`; project each commit to the DTO shape — `author = c.author.login if c.author else c.commit.author.email`, `message = c.commit.message.split("\n", 1)[0]`.
- [ ] Register the public tool `list_recent_commits(repo: str, limit: int = 5) -> list[CommitSummary]` through `@register_readonly_tool(mcp)`. Body: `_validate_repo(repo)`, `limit = max(1, min(50, limit))`, dispatch.
  **Verify:** `.venv\Scripts\python -c "import asyncio, os; os.environ.pop('GITHUB_PAT', None); from estc.services.mcp_github.server import list_recent_commits; rows=asyncio.run(list_recent_commits('kalai555777/Enterprise-Support-Triage-Copilot', 5)); print(len(rows)); assert len(rows)==5"` exits 0.

---

## Task 3.2.4 — Tool: `get_deployment_log`

- [ ] Define Pydantic DTO `DeploymentSummary` (`workflow: str`, `status: str`, `conclusion: Optional[str]`, `run_url: str`).
- [ ] On `MockGitHubClient.get_deployment_log(repo)`: return `self._data.get(repo, {}).get("deployments", [])[0]` if present, else `None`.
- [ ] On `LiveGitHubClient.get_deployment_log(repo)`: wrap in `asyncio.to_thread` a scan of the first page of `self._gh.get_repo(repo).get_workflow_runs()` (cap at 25) — return the first run whose `event == "deployment"` OR whose `name.lower()` contains `"deploy"`. Project to DTO shape. Return `None` if no match.
- [ ] Register the public tool `get_deployment_log(repo: str) -> Optional[DeploymentSummary]` through `@register_readonly_tool(mcp)`. Body: `_validate_repo(repo)`, dispatch, wrap result in DTO or `None`.
  **Verify:** `.venv\Scripts\python -c "import asyncio, os; os.environ.pop('GITHUB_PAT', None); from estc.services.mcp_github.server import get_deployment_log; r=asyncio.run(get_deployment_log('kalai555777/Enterprise-Support-Triage-Copilot')); print(r.workflow, r.conclusion); assert r.conclusion=='success'"` exits 0.

---

## Task 3.2.5 — Offline Mock Fallback

### 3.2.5-a Fixture file
- [ ] Create `estc/tests/fixtures/` directory (first fixture in the repo).
- [ ] Create `estc/tests/fixtures/github_mock.json` with one top-level entry for `kalai555777/Enterprise-Support-Triage-Copilot` containing: ≥ 1 open issue with all `IssueSummary` fields plus `state`, exactly 5 commits with all `CommitSummary` fields, exactly 1 deployment record with all `DeploymentSummary` fields and `conclusion: "success"`. Shape must match spec §4.1 exactly.
  **Verify:** `Get-Content estc/tests/fixtures/github_mock.json | ConvertFrom-Json | ForEach-Object { $_.'kalai555777/Enterprise-Support-Triage-Copilot' } | ForEach-Object { $_.commits.Count }` prints `5`.

### 3.2.5-b conftest forces mock mode
- [ ] Extend `estc/tests/conftest.py` with two lines (appended after `load_dotenv()`):
  ```python
  os.environ.pop("GITHUB_PAT", None)
  os.environ.setdefault("GITHUB_MOCK_PATH", "estc/tests/fixtures/github_mock.json")
  ```
  This guarantees the default `pytest` run uses mock mode regardless of the developer's `.env`.
  **Verify:** `.venv\Scripts\pytest --collect-only estc/tests/` prints `collected` without import errors AND `Select-String -Path estc/tests/conftest.py -Pattern 'GITHUB_PAT'` matches.

### 3.2.5-c Mock-mode end-to-end probe
- [ ] Drive all three tools through the in-process `fastmcp.Client` with `GITHUB_PAT` unset.
  **Verify:** `.venv\Scripts\pytest estc/tests/test_mcp_github.py -v -k "search_issues or list_recent_commits or get_deployment_log"` reports all green. Plan §3.2.5 bar.

---

## Task 3.2.6 — Write-Method Guard

### 3.2.6-a Single registration path enforced
- [ ] Confirm by static review that every tool in `server.py` is decorated with `@register_readonly_tool(mcp)` and **no** raw `@mcp.tool` decorator appears anywhere in the file.
  **Verify:** `Select-String -Path estc/services/mcp_github/server.py -Pattern '^@mcp\.tool'` returns **no matches**, AND `Select-String -Path estc/services/mcp_github/server.py -Pattern '^@register_readonly_tool\(mcp\)'` returns exactly 3 matches.

### 3.2.6-b Negative test: every forbidden verb is rejected (AC-T5 bar)
- [ ] In `estc/tests/test_mcp_github.py`, add a parametrized test iterating over `["create_issue", "update_repo", "delete_branch", "merge_pr", "close_issue", "patch_file", "post_comment", "add_label", "remove_label", "fork_repo", "rename_branch", "transfer_ownership", "archive_repo"]`. For each, build a stub `async def <name>(): pass`, pass through `register_readonly_tool(mcp)`, assert `pytest.raises(RuntimeError, match="Write-method tool name forbidden")`.
  **Verify:** `.venv\Scripts\pytest estc/tests/test_mcp_github.py -v -k "test_write_guard"` reports all parametrized cases passing.

### 3.2.6-c Positive guard regression
- [ ] Confirm a benign name (`search_issues`) still registers cleanly through the same decorator (no false positives — `search` is not in `_FORBIDDEN`).
  **Verify:** the existing `test_lists_exactly_three_tools` (next task) implicitly proves this — if any of the three registrations had been rejected at import, the test module would fail to import.

---

## Task 3.2.6-d / 3.2.6-bis — In-Process Test Harness

### 3.2.6-d Test cases — must include at minimum (AC-T3 / AC-T5 / AC-T6 bars)
- [ ] `test_server_name` → asserts `server.mcp.name == "estc-github"`.
- [ ] `test_lists_exactly_three_tools` → in-process `fastmcp.Client(mcp).list_tools()` returns set `{"search_issues","list_recent_commits","get_deployment_log"}`.
- [ ] `test_search_issues_happy` → returns ≥ 1 `IssueSummary` for the seeded repo.
- [ ] `test_list_recent_commits_count` → returns exactly 5 commits.
- [ ] `test_get_deployment_log_populated` → returns a `DeploymentSummary` with `conclusion == "success"`.
- [ ] `test_unknown_repo_returns_empty` → unknown repo: `search_issues` → `[]`, `list_recent_commits` → `[]`, `get_deployment_log` → `None`. No exceptions.
- [ ] `test_limit_clamped_high` → `list_recent_commits(repo, limit=999)` returns at most 50 rows (fixture caps at 5; assertion is on the clamp ceiling, not the row count).
- [ ] `test_limit_clamped_low` → `list_recent_commits(repo, limit=-3)` returns ≥ 1 row (clamp to 1).
- [ ] `test_repo_validation_rejects_ssrf` → calling any tool with `repo="; rm -rf /"`, `"owner/repo/extra"`, or `"http://evil.com/x"` raises `ValueError` **before** any client method is called.
- [ ] `test_write_guard_blocks_forbidden_verbs` → the parametrized FR-5 negative test from 3.2.6-b.
- [ ] `test_latency_p95_mock_mode` → 50 sequential `search_issues` calls in mock mode, p95 ≤ 20 ms (AC-T8 bar; warm cache, skip first 5).

  **Verify:** `.venv\Scripts\pytest estc/tests/test_mcp_github.py -v` reports **all green** with at least 11 passed.

### 3.2.6-e Async-correctness audit (per [feedback-mcp-async])
- [ ] Grep for `^def ` immediately preceded by `@register_readonly_tool` — must be zero (every tool is `async def`). Grep for direct `self._gh.` calls outside an `asyncio.to_thread` block — must be zero.
  **Verify:** `Select-String -Path estc/services/mcp_github/server.py -Pattern '@register_readonly_tool\(mcp\)\s*\r?\ndef '` returns no matches AND a manual review of `LiveGitHubClient` confirms every PyGithub access is inside a `_call` function dispatched via `asyncio.to_thread`.

---

## Task 3.2.7 — Containerization & Compose Wiring

### 3.2.7-a Dockerfile
- [ ] Create `estc/services/mcp_github/Dockerfile`:
  - Base: `python:3.11-slim`.
  - Copy a slim per-service `requirements.txt` pinning only `fastmcp>=3.3.0`, `PyGithub>=2.4.0`, `pydantic>=2.9`, `python-dotenv>=1.0`.
  - Install deps; copy `server.py`.
  - `CMD ["python", "server.py"]` (matches the Postgres MCP Dockerfile's entrypoint style).
  **Verify:** `docker build -t estc-mcp-github ./estc/services/mcp_github` exits 0.

### 3.2.7-b New service: `mcp-github-server` (no healthcheck — matches `mcp-postgres-server`)
- [ ] Add to `docker-compose.yml`:
  ```yaml
  mcp-github-server:
    build: ./estc/services/mcp_github
    env_file: .env
    environment:
      GITHUB_MOCK_PATH: /app/fixtures/github_mock.json
    volumes:
      - ./estc/tests/fixtures:/app/fixtures:ro
    stdin_open: true
    tty: true
    mem_limit: 256m
    networks: [estc-net]
  ```
  The fixture is bind-mounted read-only so the container serves mock mode without a `GITHUB_PAT`. No `healthcheck:` block — readiness is implicit via the startup log line (next step).
  **Verify:** `docker compose config` parses without error AND `docker compose config --services` includes `mcp-github-server`.

### 3.2.7-c Startup log marker (Plan 3.2.7 verification bar)
- [ ] In `server.py`'s `async def main()`, emit `log.info("estc-github starting in %s mode; tools registered: 3", _mode)` BEFORE `await mcp.run_async()`. The literal string `tools registered: 3` is the grep target.
  **Verify:** `.venv\Scripts\python -m estc.services.mcp_github.server 2>&1 | Select-String "tools registered: 3"` matches (run briefly, then Ctrl-C; or capture via a 3-second `Start-Process` + `Stop-Process`).

### 3.2.7-d Clean-boot bring-up (AC-T9 bar)
- [ ] `docker compose down -v; docker compose up -d --build mcp-github-server`.
  **Verify:** `docker compose ps mcp-github-server` shows `Up` (or `running`) within 30 s AND `docker compose logs mcp-github-server --tail 20 | Select-String "tools registered: 3"` matches.

### 3.2.7-e Repo-arg validator survives containerized boot
- [ ] Confirm the regex repo validator doesn't fail on the canonical project repo string under the container's locale.
  **Verify:** `docker compose exec mcp-github-server python -c "from server import _validate_repo; _validate_repo('kalai555777/Enterprise-Support-Triage-Copilot'); print('ok')"` prints `ok`.

---

## Phase 3.2 Exit Gate

- [ ] **EG-1 (tool surface, AC-T2 bar)** — Use `mcp-inspector` (Node CLI: `npx @modelcontextprotocol/inspector`) against the running `mcp-github-server` to list tools.
  **Verify:** `npx -y @modelcontextprotocol/inspector --cli docker exec -i $(docker compose ps -q mcp-github-server) python server.py tools/list` (or the equivalent stdio-attach form) prints exactly 3 tool names — `search_issues`, `list_recent_commits`, `get_deployment_log` — and no others. **Fallback if `npx` is unavailable:** run the in-process `test_lists_exactly_three_tools` assertion from 3.2.6-d (it covers the same bar).

- [ ] **EG-2 (full pytest sweep)** — Tests all green with verbose output, mock mode forced by conftest.
  **Verify:** `.venv\Scripts\pytest estc/tests/test_mcp_github.py -v --tb=short` reports **0 failed** and at least 11 passed.

- [ ] **EG-3 (clean-boot regression)** — Confirm everything still passes from a clean state.
  **Verify:** `docker compose down -v; docker compose up -d --build mcp-github-server` then re-run EG-2. Both must succeed.

- [ ] **EG-4 (joint Phase 3.3 readiness)** — With both `mcp-postgres-server` and `mcp-github-server` up, the joint Phase 3.3.1 exit gate from `docs/plan.md` is unblockable.
  **Verify:** `docker compose ps mcp-postgres-server mcp-github-server` shows both `Up`. The 6 tools across both servers are: `get_customer_by_id`, `get_subscription_status`, `list_delinquent_accounts`, `search_issues`, `list_recent_commits`, `get_deployment_log` — and no others.

---

## Risks & Open Questions

1. **FastMCP internal API drift** — The plan uses only public surface (`FastMCP("name")`, `@mcp.tool` via the wrapper, `mcp.run_async()`, `fastmcp.Client(mcp).list_tools()`). No reliance on `mcp._tool_manager._tools` or any private attribute (spec §4.1 had originally proposed this for a healthcheck — dropped per user-confirmed "no healthcheck").
2. **PyGithub is sync** — Every PyGithub call inside `LiveGitHubClient` is dispatched through `asyncio.to_thread`. The [feedback-mcp-async] rule is honored at the wrapper layer, not by swapping libraries. Future migration to `gidgethub`/`githubkit` is out of scope.
3. **Mock fixture drift** — The fixture's shape is what the three Pydantic DTOs validate against. If a DTO field is added later, the fixture must be updated in lockstep or `test_*_happy` tests will fail at DTO construction. Acceptable churn cost.
4. **`mcp-inspector` availability** — Node CLI from `@modelcontextprotocol/inspector`. EG-1 includes a fallback (in-process listing test) so the phase is not blocked if Node is unavailable.
5. **Write-guard regex over-matches** — `_FORBIDDEN` matches case-insensitively against function names. The benign tool name `search_issues` is safe because the regex looks for the listed verbs as substrings; `search` is not among them. Adding any future tool whose name contains `update`, `merge`, etc. — even a read-only `update_check` — will be blocked. By design.
6. **Container entrypoint** — `CMD ["python", "server.py"]` mirrors `mcp-postgres-server`. Requires the `if __name__ == "__main__": asyncio.run(main())` guard at the bottom of `server.py`. Implementation must add this.
7. **Live-mode integration test** — `AC-T4` (live PyGithub call against the project's own GitHub repo) is gated behind `GITHUB_INTEGRATION=1` and skipped by default. CI stays offline-capable.

---

## Out of Scope (explicitly deferred)

- Joint Phase 3 exit gate (3.3.1) full execution — unblocked by EG-4 but the formal joint inspector run is Phase 3.3's task.
- LangGraph `bug_agent` / `feature_agent` integration — Phase 4.3.4 / 4.3.5.
- LangSmith tracing of GitHub MCP tool calls — Phase 4.5.1.
- Rate-limit-aware caching beyond the inline guard in spec §2.2 — deferred until Phase 4 traffic patterns are known.
- Migration to an async-native GitHub client (`gidgethub` / `githubkit`) — re-evaluate post-Phase 4.

---

**Awaiting `Proceed` to begin execution at PF-1.**
