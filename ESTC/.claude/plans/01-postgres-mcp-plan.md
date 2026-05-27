# Execution Plan: Phase 3.1 — PostgreSQL MCP Server
**Source spec:** `.CLAUDE/Specs/01-postgres-mcp-spec.md`
**Source plan section:** `docs/plan.md` § Phase 3.1 (tasks 3.1.1 – 3.1.7)
**Status:** AWAITING APPROVAL — no code to be executed until user replies `Proceed`.

---

## Context

This plan operationalizes Phase 3.1 of the ESTC roadmap: a read-only **PostgreSQL MCP Server** that fronts the `enterprise_customers` table for the LangGraph orchestrator (Phase 4). The work has three threads that must be done in order:

1. **Schema / role layer** (SQL): add a SELECT-only role `estc_reader` and ensure it survives a clean `docker compose down -v`.
2. **Server runtime** (Python): a `fastmcp` server named `estc-postgres` exposing exactly three tools — `get_customer_by_id`, `get_subscription_status`, `list_delinquent_accounts` — all parameterized, all SELECT-only.
3. **Integration**: in-process pytest harness, Dockerfile, and a compose split (`mcp-postgres` → `postgres-db` + `mcp-postgres-server`).

Every step below ends with a **Verify** command. The shell is **PowerShell 5.1**. A step is "done" only when its verification passes.

---

## Pre-Flight (read-only sanity checks before any change)

- [ ] **PF-1** Confirm spec exists and is the version this plan targets.
  **Verify:** `Get-Content .CLAUDE/Specs/01-postgres-mcp-spec.md | Select-String "estc-postgres"` returns ≥ 1 match.
- [ ] **PF-2** Confirm Phase 1 artifacts the spec depends on are in place: schema DDL and seed.
  **Verify:** `Test-Path infra/sql/init.sql, infra/sql/seed.sql` returns `True, True`.
- [ ] **PF-3** Confirm `docker-compose.yml` currently has a Postgres service (any name) and identify it for the rename in 3.1.7.
  **Verify:** `docker compose config --services` lists at least one service whose definition uses `postgres:16-alpine` (inspect via `docker compose config`).
- [ ] **PF-4** Confirm the venv is the active Python 3.11 toolchain.
  **Verify:** `.venv\Scripts\python --version` reports `Python 3.11.*`.
- [ ] **PF-5** Confirm `fastmcp` is installed (standalone framework, not the legacy bundled `mcp` SDK).
  **Verify:** `.venv\Scripts\python -c "from fastmcp import FastMCP; print(FastMCP)"` exits 0. If it fails, install via step 3.1.1-a.
- [ ] **PF-6** Confirm `psycopg-pool` is installable (it's a sibling package to `psycopg[binary]`, not bundled). If absent from `requirements-orchestrator.txt`, the plan adds a pin in step 3.1.1-a.
  **Verify:** `.venv\Scripts\pip show psycopg-pool` exits 0, OR record the gap.
- [ ] **PF-7** Confirm canonical test company `9422` exists in seed data.
  **Verify:** `docker compose up -d postgres-db` (or current name) then `docker compose exec <pg-service> psql -U estc -d estc -c "SELECT company_id FROM enterprise_customers WHERE company_id='9422';"` returns 1 row. If absent, append it to `seed.sql` as part of step 3.1.6-pre.

---

## Task 3.1.1 — MCP Server Bootstrap

### 3.1.1-a Dependency pins (only if PF-5 or PF-6 flagged a gap)
- [ ] Replace `mcp==1.0.*` with `fastmcp==2.*` in `requirements-orchestrator.txt`. Append `psycopg-pool==3.2.*` if missing. Install in venv.
  **Verify:** `.venv\Scripts\pip install -r requirements-orchestrator.txt; .venv\Scripts\python -c "import fastmcp, psycopg_pool; print(fastmcp.__version__, psycopg_pool.__version__)"` prints a 2.x and a 3.2.x version respectively.

### 3.1.1-b Package skeleton
- [ ] Create directory `estc/services/mcp-postgres/` and an empty `__init__.py` (so it's an importable package: `estc.services.mcp_postgres`). Mirror with `estc/services/__init__.py` if missing.
  **Verify:** `.venv\Scripts\python -c "import estc.services.mcp_postgres"` exits 0 (will error until next step actually creates a `server.py`; this verify runs together with 3.1.1-c).

### 3.1.1-c Server identity
- [ ] Create `estc/services/mcp-postgres/server.py` containing exactly the bootstrap from spec §4.1: `from fastmcp import FastMCP; mcp = FastMCP("estc-postgres")`. Import the connection pool but **do not open it** at module load — only open in `main()`.
  **Verify:** `.venv\Scripts\python -c "from estc.services.mcp_postgres.server import mcp; print(mcp.name)"` prints exactly `estc-postgres`. Matches AC-T1.

### 3.1.1-d Settings extension
- [ ] Extend `estc/shared/config.py` `Settings` class with `POSTGRES_READER_USER: str` and `POSTGRES_READER_PASSWORD: str`. Append the same two keys (with placeholder values) to `.env.example`. Update local `.env` (manually, off-band — the plan must not write secrets).
  **Verify:** `.venv\Scripts\python -c "from estc.shared.config import Settings; s=Settings(); print(s.POSTGRES_READER_USER)"` prints the configured value (non-empty).

---

## Task 3.1.2 — Tool: `get_customer_by_id`

- [ ] Define Pydantic DTOs `CustomerRecord` and `SubscriptionStatus` (spec §4.1) at module top of `server.py`.
- [ ] Register `get_customer_by_id(company_id: str) -> Optional[CustomerRecord]` using the **parameterized** SELECT shown in spec §4.1. Return `None` on miss — never raise.
- [ ] Wrap `psycopg.OperationalError` → MCP `ToolError("postgres_unreachable")` per spec §5.2.
  **Verify:** With `postgres-db` up, run an in-process probe script: `.venv\Scripts\python -c "import asyncio; from estc.services.mcp_postgres.server import get_customer_by_id; print(asyncio.run(get_customer_by_id('9422')))"` prints a populated `CustomerRecord` for company 9422.

---

## Task 3.1.3 — Tool: `get_subscription_status`

- [ ] Register `get_subscription_status(company_id: str) -> Optional[SubscriptionStatus]` reusing `get_customer_by_id`'s row fetch but projecting to the narrower DTO (no `technical_poc_email`).
  **Verify:** `.venv\Scripts\python -c "import asyncio; from estc.services.mcp_postgres.server import get_subscription_status; r=asyncio.run(get_subscription_status('9422')); print(r); assert 'technical_poc_email' not in r.model_dump()"` exits 0 and prints a populated `SubscriptionStatus`.

---

## Task 3.1.4 — Tool: `list_delinquent_accounts`

- [ ] Register `list_delinquent_accounts(limit: int = 10) -> list[CustomerRecord]` with server-side clamp `limit = max(1, min(100, limit))` and the `ORDER BY company_id LIMIT %s` query from spec §4.1.
  **Verify:** `.venv\Scripts\python -c "import asyncio; from estc.services.mcp_postgres.server import list_delinquent_accounts; rows=asyncio.run(list_delinquent_accounts(10)); print(len(rows)); assert all(r.account_status=='Delinquent' for r in rows)"` exits 0, and the printed count equals the output of `docker compose exec postgres-db psql -U estc -d estc -At -c "SELECT LEAST(COUNT(*),10) FROM enterprise_customers WHERE account_status='Delinquent';"`.

---

## Task 3.1.5 — DB-Layer Read-Only Enforcement

### 3.1.5-a Authoring the grants file
- [ ] Create `infra/sql/grants.sql` exactly as in spec §4.1 (idempotent `CREATE ROLE estc_reader`, `REVOKE ALL`, `GRANT SELECT ON enterprise_customers`, `GRANT USAGE ON SCHEMA public`). Hard-code the dev password to match `.env`'s `POSTGRES_READER_PASSWORD`.
  **Verify:** `Get-Content infra/sql/grants.sql | Select-String "GRANT SELECT ON enterprise_customers TO estc_reader"` matches.

### 3.1.5-b Wire into init order
- [ ] Confirm that Postgres's init-dir runs `*.sql` files alphabetically. Rename / order so the sequence is `01-init.sql` → `02-grants.sql` → `03-seed.sql` (or use a single concatenated init.sql that `\i`s the others). Choose **renaming + alphabetical order** — simpler and survives Compose semantics on Windows.
  **Verify:** `Get-ChildItem infra/sql/*.sql | Sort-Object Name | Select-Object -ExpandProperty Name` lists the files in the intended order.

### 3.1.5-c Clean boot
- [ ] `docker compose down -v` then `docker compose up -d postgres-db` (service still named `mcp-postgres` at this step — rename happens in 3.1.7).
  **Verify:** `docker compose exec <pg-service> psql -U estc -d estc -c "\du estc_reader"` lists the role with `LOGIN` attribute and `Cannot login as superuser`.

### 3.1.5-d Negative test: writes denied
- [ ] As `estc_reader`, attempt INSERT / UPDATE / DELETE / TRUNCATE — each must fail.
  **Verify (the AC-T4 bar):** `docker compose exec <pg-service> psql -U estc_reader -d estc -c "INSERT INTO enterprise_customers VALUES ('x','x','Free','Active','x@y.z');"` exits non-zero with `ERROR: permission denied for table enterprise_customers`. Repeat with `UPDATE enterprise_customers SET company_name='x' WHERE company_id='9422';`, `DELETE FROM enterprise_customers;`, `TRUNCATE enterprise_customers;` — all must emit `permission denied`.

### 3.1.5-e Positive test: reads succeed
- [ ] Confirm SELECT still works as `estc_reader`.
  **Verify:** `docker compose exec <pg-service> psql -U estc_reader -d estc -At -c "SELECT COUNT(*) FROM enterprise_customers;"` prints a number ≥ 20.

---

## Task 3.1.6 — In-Process Test Harness

### 3.1.6-a conftest
- [ ] Create or extend `estc/tests/conftest.py` with a session-scoped fixture that ensures `postgres-db` is healthy before tests run (skip if `DOCKER_DISABLED=1` for CI bypass) and that `POSTGRES_READER_USER` / `POSTGRES_READER_PASSWORD` are exported from `.env` via `python-dotenv`.
  **Verify:** `.venv\Scripts\pytest --collect-only estc/tests/` prints `collected 0 items` cleanly (no import errors).

### 3.1.6-b Test cases — must include at minimum (AC-T6 bar)
- [ ] `test_server_name` → asserts `server.name == "estc-postgres"`.
- [ ] `test_lists_exactly_three_tools` → in-process MCP client `list_tools()` returns set `{"get_customer_by_id","get_subscription_status","list_delinquent_accounts"}`.
- [ ] `test_get_customer_by_id_happy` → returns populated `CustomerRecord` for `9422`.
- [ ] `test_get_customer_by_id_unknown_returns_none` → unknown id returns `None`, no exception.
- [ ] `test_get_subscription_status_omits_email` → returned dict has no `technical_poc_email` key.
- [ ] `test_list_delinquent_accounts_count` → row count matches direct SQL count, capped at limit.
- [ ] `test_limit_clamped_low` → `limit=0` returns ≥ 1 row (clamp to 1).
- [ ] `test_limit_clamped_high` → `limit=999` returns at most 100 rows.
- [ ] `test_parameterization_smoke` → call with `company_id="' OR 1=1 --"` returns `None` (not all rows — proves parameterization).
- [ ] `test_latency_p95` → 50 sequential `get_customer_by_id('9422')` calls, p95 ≤ 150 ms (AC-T7 bar; warm pool, skip first 5).

  **Verify:** `.venv\Scripts\pytest estc/tests/test_mcp_postgres.py -v` reports **all green** with at least 10 passed.

### 3.1.6-c Static parameterization audit (AC-T5 bar)
- [ ] Grep for f-string / `.format` / `%` interpolation against SQL strings in `server.py`. Expect zero matches.
  **Verify:** `Select-String -Path estc/services/mcp-postgres/server.py -Pattern 'f"[^"]*SELECT|\.format\(.*SELECT|"%s".*%\s*\('` returns no matches.

---

## Task 3.1.7 — Containerization & Compose Split

### 3.1.7-a Dockerfile
- [ ] Create `estc/services/mcp-postgres/Dockerfile`:
  - Base: `python:3.11-slim`.
  - Copy `requirements-orchestrator.txt` (or a slim per-service requirements file pinning only `fastmcp`, `psycopg[binary]`, `psycopg-pool`, `pydantic`, `python-dotenv`).
  - Install deps; copy `estc/services/mcp-postgres/` and `estc/shared/`.
  - `CMD ["python", "-m", "estc.services.mcp_postgres.server"]`.
  **Verify:** `docker build -t estc-mcp-postgres ./estc/services/mcp-postgres` exits 0.

### 3.1.7-b Compose rename: `mcp-postgres` → `postgres-db`
- [ ] In `docker-compose.yml`, rename the existing Postgres service from `mcp-postgres` to `postgres-db`. Preserve the healthcheck, env_file, and `/docker-entrypoint-initdb.d` mount.
- [ ] Update any references elsewhere (e.g. `POSTGRES_HOST=mcp-postgres` in `.env` → `POSTGRES_HOST=postgres-db`).
  **Verify:** `docker compose config --services` lists `postgres-db` and **not** `mcp-postgres`.

### 3.1.7-c New service: `mcp-postgres-server`
- [ ] Add `mcp-postgres-server` block per spec §4.1 Compose delta: builds `./estc/services/mcp-postgres`, `depends_on: postgres-db (service_healthy)`, `mem_limit: 256m`, on `estc-net`. Override `POSTGRES_HOST: postgres-db` in `environment:`.
  **Verify:** `docker compose config` parses without error AND `docker compose config --services` includes `mcp-postgres-server`.

### 3.1.7-d Clean-boot bring-up (AC-T8 bar)
- [ ] `docker compose down -v; docker compose up -d --build mcp-postgres-server`.
  **Verify:** `docker compose ps mcp-postgres-server` shows `Up` (or `running`) within 30 s and `docker compose ps postgres-db` shows `healthy`. `docker compose logs mcp-postgres-server --tail 20` contains a "pool opened" / FastMCP startup line and no traceback.

### 3.1.7-e Pre-existing verifications still pass against renamed service
- [ ] Re-run Phase 1.5.3 and 1.5.4 verifications against `postgres-db`.
  **Verify:** `docker compose exec postgres-db psql -U estc -d estc -c "\d enterprise_customers"` lists 5 columns AND `docker compose exec postgres-db psql -U estc -d estc -At -c "SELECT COUNT(*), COUNT(DISTINCT subscription_tier), COUNT(DISTINCT account_status) FROM enterprise_customers;"` returns counts ≥ 20, 3, 3.

---

## Phase 3.1 Exit Gate

- [ ] **EG-1 (tool surface, AC-T2 bar)** — Use `mcp-inspector` (Node CLI: `npx @modelcontextprotocol/inspector`) against the running `mcp-postgres-server` to list tools.
  **Verify:** `npx -y @modelcontextprotocol/inspector --cli docker exec -i $(docker compose ps -q mcp-postgres-server) python -m estc.services.mcp_postgres.server tools/list` (or the equivalent stdio-attach form) prints exactly 3 tool names — `get_customer_by_id`, `get_subscription_status`, `list_delinquent_accounts` — and no others. **Fallback if `npx` is unavailable:** run the in-process listing assertion from test `test_lists_exactly_three_tools` (it covers the same bar).

- [ ] **EG-2 (full pytest sweep)** — Tests all green with verbose output.
  **Verify:** `.venv\Scripts\pytest estc/tests/test_mcp_postgres.py -v --tb=short` reports **0 failed**.

- [ ] **EG-3 (clean-boot regression)** — Confirm everything still passes from a clean state.
  **Verify:** `docker compose down -v; docker compose up -d --build` then re-run EG-2. Both must succeed.

---

## Risks & Open Questions

1. **FastMCP API shape** — Spec and plan are committed to the standalone `fastmcp` framework (decorator style: `mcp = FastMCP("estc-postgres")`, `@mcp.tool`). PF-5 verifies the package is installed; 3.1.1-a pins it if not. The legacy bundled `mcp` SDK is no longer in play.
2. **`mcp-inspector` availability** — It's a Node CLI from `@modelcontextprotocol/inspector`, not part of the Python SDK. EG-1 includes a fallback (in-process listing test) so the phase is not blocked if Node is unavailable.
3. **Init-script ordering** — Postgres runs `/docker-entrypoint-initdb.d/*.sql` alphabetically; the `01-/02-/03-` rename in 3.1.5-b is the cleanest cross-platform fix. Verify no other code references the old filenames.
4. **Compose rename ripple** — Renaming `mcp-postgres` to `postgres-db` may break other services that hard-code the hostname (e.g. orchestrator stubs from Phase 1.5.1). 3.1.7-b includes a `.env` update; a sweep of `docker-compose.yml` for any other `mcp-postgres` reference is part of that step.
5. **Container `__main__` entrypoint** — `python -m estc.services.mcp_postgres.server` requires a `if __name__ == "__main__": asyncio.run(main())` guard. Implementation must add this.

---

## Out of Scope (explicitly deferred)

- GitHub MCP server — Phase 3.2 (`02-github-mcp-spec.md` to be authored).
- Joint Phase 3 exit gate (3.3.1) — blocked on Phase 3.2.
- LangGraph orchestrator integration — Phase 4.
- LangSmith tracing of MCP tool calls — Phase 4.5.1.

---

**Awaiting `Proceed` to begin execution at PF-1.**
