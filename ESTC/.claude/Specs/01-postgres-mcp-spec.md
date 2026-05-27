# Architectural Specification: Phase 3.1 — PostgreSQL MCP Server
**Status:** DRAFT / PROPOSED
**Associated Tasks:** Tasks 3.1.1 – 3.1.7 (`docs/plan.md` § Phase 3.1)
**Target Files:**
- `estc/services/mcp-postgres/server.py`
- `estc/services/mcp-postgres/Dockerfile`
- `estc/infra/sql/grants.sql`
- `estc/infra/sql/init.sql` (amended)
- `estc/tests/test_mcp_postgres.py`
- `docker-compose.yml` (amended — split into `postgres-db` and `mcp-postgres-server`)

---


## 1. Executive Summary & Problem Statement

### 1.1 Objective & Context
This sub-phase introduces the **first of two Secure Context Layer servers** described in `docs/design.md` § 2 (Component B). It wraps the `enterprise_customers` PostgreSQL table — the system of record for company health, subscription state, and account status — behind the **Model Context Protocol (MCP)**, exposing it to the LangGraph orchestration engine as a small, typed, **read-only** tool surface.

Within the ESTC topology, the orchestrator's `billing_agent` and `lockout_agent` nodes (Phase 4.3.3 and 4.3.6) must hydrate `AgentState` with transactional customer facts (e.g. *"is company 9422 Enterprise tier and Delinquent?"*) before drafting a reply. They cannot — by design — issue raw SQL. The PostgreSQL MCP server is the **only** path through which any LLM-driven node in the system can observe customer records. It translates a fixed schema of tool calls into parameterized `SELECT` statements against a SELECT-only role, eliminating SQL-injection surface and write surface in one move.

This phase delivers the server process, its three tool schemas, the database-grants layer that enforces read-only at the engine level, the in-process test harness, and the Docker Compose wiring that splits today's single `mcp-postgres` service into a datastore (`postgres-db`) and an MCP wrapper (`mcp-postgres-server`) — a decision called out explicitly in the Plan's "Decisions Embedded" section.

### 1.2 Core Problem Statement
LangGraph nodes powered by `gpt-4o-mini` / `claude-3-5-sonnet` cannot be trusted with direct database credentials or arbitrary SQL execution: a prompt-injection or hallucinated query could exfiltrate, corrupt, or mass-delete customer records. We need a **narrow, typed, append-impossible** abstraction between the model and Postgres that (a) exposes only the read patterns the agents actually need, (b) enforces read-only at *two* layers (protocol surface + DB role grants), and (c) is observable, containerized, and independently restartable from the database it fronts.

---

## 2. System Boundaries & Constraints

### 2.1 Architectural Boundaries
- **Upstream Trigger / Consumer:** The LangGraph orchestrator (`estc/services/orchestrator/`, Phase 4), specifically:
  - `billing_agent` node → calls `get_subscription_status` and `get_customer_by_id`.
  - `lockout_agent` node → calls `get_customer_by_id` to fetch tier + POC email for the escalation envelope.
  - Operator tooling / future analytics nodes → may call `list_delinquent_accounts`.
- **Downstream Dependencies:**
  - `postgres-db` service (renamed from today's `mcp-postgres` per Plan Decision §1) — `postgres:16-alpine`, initialized by `infra/sql/init.sql` (schema from design.md §3) + `infra/sql/seed.sql` (≥ 20 rows from task 1.5.4) + the new `infra/sql/grants.sql`.
  - The `fastmcp` Python SDK (`fastmcp==2.*` from `requirements-orchestrator.txt`) for the server runtime and transport.
  - `psycopg[binary]==3.2.*` for the database driver.
  - Environment variables consumed from `.env` (loaded via `estc/shared/config.py`, Phase 1.4.3): `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, and a **new** pair `POSTGRES_READER_USER` / `POSTGRES_READER_PASSWORD` introduced by this phase.

### 2.2 Technical & Operational Constraints
- **Performance / Latency:** Per-tool round-trip ≤ 150 ms p95 measured from MCP client `call_tool` entry to result delivery, on the seeded fixture DB (≥ 20 rows). The server holds a single `psycopg` connection pool (min=1, max=4) — Postgres connection setup is the dominant cost; pooling keeps p95 stable under back-to-back agent invocations.
- **Security & Compliance:**
  - The DB role used by this server **must** be granted `SELECT` only on `enterprise_customers`. Any `INSERT / UPDATE / DELETE / TRUNCATE / ALTER` must fail with `permission denied` at the Postgres engine, not at application code. This is the test bar for task 3.1.5.
  - All SQL must be parameterized via `psycopg` placeholders (`%s`). String interpolation of tool arguments into SQL is **prohibited** and is a review-blocking defect.
  - PII fields (`technical_poc_email`) are returned as-is to the orchestrator; no masking is performed at this layer. Down-stream masking, if required, is the supervisor node's responsibility.
  - The server **must not** register any tool whose name implies mutation. The GitHub MCP server (Phase 3.2.6) installs a regex guard for this; the PostgreSQL server enforces the same property by construction (only three tools, all explicitly SELECT-only).
- **Resource Limits:**
  - Memory ceiling: 256 MiB in the container (Docker Compose `mem_limit`).
  - Connection pool max: 4 — well below `postgres-db`'s default `max_connections=100`, leaving headroom for `psql` admin sessions and Phase 4 orchestrator connections that may also hit the DB directly during eval.
  - `list_delinquent_accounts.limit` parameter is hard-clamped server-side to `[1, 100]` to prevent unbounded result sets being shipped over the MCP transport.

---

## 3. Functional Requirements

- **FR-1 (Server Identity, task 3.1.1):** Initialize an MCP `Server` instance named exactly `"estc-postgres"`. The name is the discovery handle used by the orchestrator's MCP client registry and by `mcp-inspector` in the Phase 3.3 exit-gate verification.
- **FR-2 (Tool: `get_customer_by_id`, task 3.1.2):** Expose a tool `get_customer_by_id(company_id: str) -> CustomerRecord` executing `SELECT company_id, company_name, subscription_tier, account_status, technical_poc_email FROM enterprise_customers WHERE company_id = %s` with a single parameter binding. Returns `None` (MCP-empty result) when no row matches; never raises on miss.
- **FR-3 (Tool: `get_subscription_status`, task 3.1.3):** Expose `get_subscription_status(company_id: str) -> SubscriptionStatus` returning `{company_id, subscription_tier, account_status}`. Implementation may reuse FR-2's row fetch but the projected shape **must** be the narrower DTO — agents that only need status should not receive the POC email.
- **FR-4 (Tool: `list_delinquent_accounts`, task 3.1.4):** Expose `list_delinquent_accounts(limit: int = 10) -> list[CustomerRecord]` running `SELECT ... WHERE account_status = 'Delinquent' ORDER BY company_id LIMIT %s`. `limit` is clamped to `[1, 100]` server-side. Result count must equal `SELECT COUNT(*) FROM enterprise_customers WHERE account_status='Delinquent'` (capped by clamp).
- **FR-5 (DB-Layer Read-Only Enforcement, task 3.1.5):** A new DB role `estc_reader` is provisioned by `infra/sql/grants.sql`. The role is granted `SELECT` on `enterprise_customers` and **no other privileges**. The MCP server connects exclusively as `estc_reader`. `init.sql` is amended to `\i grants.sql` so the role exists on first boot of `postgres-db`.
- **FR-6 (In-Process Test Harness, task 3.1.6):** A `pytest` suite at `estc/tests/test_mcp_postgres.py` instantiates the MCP server in-process and invokes each of the three tools through the SDK's in-memory client transport — not via the inspector, not via a network port — verifying both shape and content of returned data against the Phase 1.5.4 seed rows.
- **FR-7 (Containerization & Compose Wiring, task 3.1.7):** A `services/mcp-postgres/Dockerfile` builds the server on `python:3.11-slim`. `docker-compose.yml` is amended to (a) rename the existing `mcp-postgres` data service to `postgres-db`, (b) add `mcp-postgres-server` as a new service that `depends_on: postgres-db` (with `condition: service_healthy`), and (c) preserve the existing Postgres healthcheck on `postgres-db`.

---

## 4. Detailed Component Specifications & API Contracts

### 4.1 Interface Code & Data Shapes

**Pydantic DTOs (`services/mcp-postgres/server.py`, top of module):**

```python
from pydantic import BaseModel, Field
from typing import Literal, Optional

SubscriptionTier = Literal["Enterprise", "Growth", "Free"]
AccountStatus    = Literal["Active", "Delinquent", "Locked"]

class CustomerRecord(BaseModel):
    company_id: str
    company_name: str
    subscription_tier: SubscriptionTier
    account_status: AccountStatus
    technical_poc_email: str

class SubscriptionStatus(BaseModel):
    company_id: str
    subscription_tier: SubscriptionTier
    account_status: AccountStatus
```

**Server bootstrap & tool registration (target shape):**

```python
import os
from fastmcp import FastMCP
from psycopg_pool import ConnectionPool

mcp = FastMCP("estc-postgres")  # FR-1

_pool = ConnectionPool(
    conninfo=(
        f"host={os.environ['POSTGRES_HOST']} "
        f"port={os.environ['POSTGRES_PORT']} "
        f"dbname={os.environ['POSTGRES_DB']} "
        f"user={os.environ['POSTGRES_READER_USER']} "
        f"password={os.environ['POSTGRES_READER_PASSWORD']}"
    ),
    min_size=1, max_size=4, open=False,
)

@mcp.tool
async def get_customer_by_id(company_id: str) -> Optional[CustomerRecord]:
    """Fetch the full enterprise_customers row for a given company_id."""
    with _pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT company_id, company_name, subscription_tier, "
            "account_status, technical_poc_email "
            "FROM enterprise_customers WHERE company_id = %s",
            (company_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return CustomerRecord(
            company_id=row[0], company_name=row[1],
            subscription_tier=row[2], account_status=row[3],
            technical_poc_email=row[4],
        )

@mcp.tool
async def get_subscription_status(company_id: str) -> Optional[SubscriptionStatus]:
    """Return only subscription_tier + account_status for a company_id."""
    rec = await get_customer_by_id(company_id)
    if rec is None:
        return None
    return SubscriptionStatus(
        company_id=rec.company_id,
        subscription_tier=rec.subscription_tier,
        account_status=rec.account_status,
    )

@mcp.tool
async def list_delinquent_accounts(limit: int = 10) -> list[CustomerRecord]:
    """List up to `limit` (1..100) customers whose account_status='Delinquent'."""
    limit = max(1, min(100, limit))
    with _pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT company_id, company_name, subscription_tier, "
            "account_status, technical_poc_email "
            "FROM enterprise_customers "
            "WHERE account_status = 'Delinquent' "
            "ORDER BY company_id LIMIT %s",
            (limit,),
        )
        return [CustomerRecord(
            company_id=r[0], company_name=r[1],
            subscription_tier=r[2], account_status=r[3],
            technical_poc_email=r[4],
        ) for r in cur.fetchall()]

def main() -> None:
    _pool.open()
    mcp.run()
```

**`infra/sql/grants.sql` (FR-5):**

```sql
-- Idempotent role + grants for the MCP read-only connector.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'estc_reader') THEN
        CREATE ROLE estc_reader LOGIN PASSWORD 'estc_reader_dev_pw';
    END IF;
END $$;

REVOKE ALL ON enterprise_customers FROM estc_reader;
GRANT SELECT ON enterprise_customers TO estc_reader;
GRANT USAGE ON SCHEMA public TO estc_reader;
```

`init.sql` is appended with a single line: `\i grants.sql` (or the file is concatenated by Compose's `/docker-entrypoint-initdb.d` ordering).

**Docker Compose delta (`docker-compose.yml`, FR-7):**

```yaml
services:
  postgres-db:        # renamed from mcp-postgres (Phase 1.5.2)
    image: postgres:16-alpine
    env_file: .env
    volumes:
      - ./infra/sql:/docker-entrypoint-initdb.d:ro
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 5s
      timeout: 3s
      retries: 6
    networks: [estc-net]

  mcp-postgres-server:    # new (task 3.1.7)
    build: ./services/mcp-postgres
    env_file: .env
    environment:
      POSTGRES_HOST: postgres-db
    depends_on:
      postgres-db:
        condition: service_healthy
    mem_limit: 256m
    networks: [estc-net]

volumes:
  pgdata:

networks:
  estc-net:
```

### 4.2 Endpoint / Method Contracts

The MCP surface is not HTTP — tools are invoked over the SDK transport. Three contracts are exposed:

- **Tool `get_customer_by_id`**
  - Input: `{"company_id": str}` — non-empty.
  - Output: `CustomerRecord | null`. Null indicates no row matched (not an error).

- **Tool `get_subscription_status`**
  - Input: `{"company_id": str}` — non-empty.
  - Output: `SubscriptionStatus | null`. Projection of `CustomerRecord`; `technical_poc_email` is intentionally excluded.

- **Tool `list_delinquent_accounts`**
  - Input: `{"limit": int = 10}` — server clamps to `[1, 100]`.
  - Output: `list[CustomerRecord]`, ordered by `company_id` ascending. Empty list is a valid response.

---

## 5. Edge Cases & Error Handling

### 5.1 Anticipated Edge Cases
1. **Unknown `company_id`** (`get_customer_by_id`, `get_subscription_status`): The DB returns no rows. The tool returns `None`, **never raises**. The orchestrator's agent node is responsible for translating null into a user-facing "We could not locate company X" reply — this is the layering decision: the MCP server reports facts, the LangGraph node performs UX.
2. **`limit=0` or negative** (`list_delinquent_accounts`): Server clamps to `1` and proceeds. This is preferable to raising, because the orchestrator may receive a hallucinated `limit=-1` from the LLM and we'd rather return one delinquent account than blow up the node graph.
3. **`postgres-db` unhealthy at startup**: `mcp-postgres-server` is gated by `depends_on: condition: service_healthy`, so it won't start. If `postgres-db` dies *after* the MCP server is up, the next `pool.connection()` call raises `psycopg.OperationalError`; the tool catches and re-raises as an MCP-protocol error so the orchestrator's supervisor node can mark the ticket for escalation rather than auto-approve a hollow draft.
4. **Whitespace / case-mismatched `company_id`**: The DB column is `VARCHAR(50)` case-sensitive. We do **not** normalize. If the orchestrator passes `"9422 "` with a trailing space, no row matches. This is deliberate: a normalization layer here would mask data-quality bugs upstream in ticket parsing.
5. **Concurrent calls from multiple orchestrator runs**: Connection pool (max=4) serializes overflow. p95 latency under 4× concurrent burst is still well inside the 150 ms target on the seed dataset.

### 5.2 Error Handling & State Recovery Matrix

| Trigger / Exception | Handled State / Action | Fallback Behavior / Mitigation |
|---|---|---|
| Row not found (any `SELECT`) | Return `None` (single-row tools) or `[]` (`list_delinquent_accounts`) | Caller (agent node) decides UX wording; no exception propagated |
| `psycopg.OperationalError` (DB down / network partition) | Caught at tool boundary; re-raised as MCP `ToolError("postgres_unreachable")` | Orchestrator's `supervisor_review` node flips `requires_escalation=True`; ticket lands in operator queue |
| `psycopg.errors.InsufficientPrivilege` on any statement | Logged at ERROR level with the offending SQL; re-raised as MCP `ToolError("read_only_violation")` | **This is a defect signal, not a runtime case** — surfaces immediately in CI if anyone adds a non-SELECT path. Test 3.1.5 verifies the same failure from the DB side |
| `limit` outside `[1, 100]` | Clamped silently to bounds | No error returned; behavior matches FR-4 contract |
| `company_id` is empty string | DB returns no row; tool returns `None` | Same as edge case 1 — orchestrator handles null |
| Pool exhaustion (> 4 concurrent in-flight tool calls) | `psycopg_pool` blocks up to `timeout=30s`, then raises `PoolTimeout` | Re-raised as MCP `ToolError("backend_overloaded")`; supervisor escalates |
| Malformed argument type (e.g. `limit="ten"`) | MCP SDK rejects at schema validation before reaching the tool body | Caller sees a protocol-level validation error; no DB hit |

---

## 6. Acceptance Criteria

### 6.1 Technical Acceptance Criteria
- **AC-T1 (Server identity, task 3.1.1):** `.venv\Scripts\python -c "from estc.services.mcp_postgres.server import mcp; print(mcp.name)"` prints exactly `estc-postgres`.
- **AC-T2 (Tool surface, tasks 3.1.2–3.1.4):** `mcp-inspector ./services/mcp-postgres/server.py` lists **exactly three** tools — `get_customer_by_id`, `get_subscription_status`, `list_delinquent_accounts` — with the parameter shapes given in §4.1, and **no other** tools.
- **AC-T3 (Functional correctness):** Against the Phase 1.5.4 seed dataset, `get_customer_by_id("9422")` returns a populated `CustomerRecord`; `get_subscription_status("9422")` returns the same tier/status pair (and omits the email); `list_delinquent_accounts(limit=10)` row count equals `SELECT COUNT(*) FROM enterprise_customers WHERE account_status='Delinquent' LIMIT 10` executed against the seeded DB.
- **AC-T4 (Read-only DB enforcement, task 3.1.5):** `docker compose exec postgres-db psql -U estc_reader -d estc -c "INSERT INTO enterprise_customers VALUES ('x','x','Free','Active','x@y.z');"` exits non-zero with `ERROR: permission denied for table enterprise_customers`. The same error appears on `UPDATE`, `DELETE`, and `TRUNCATE` attempts.
- **AC-T5 (Parameterization):** Static review (manual or `ruff` rule) confirms **zero** occurrences of f-string / `.format` / `%` interpolation of arguments into SQL strings anywhere in `server.py`. All bindings use `%s` placeholders.
- **AC-T6 (Test suite, task 3.1.6):** `.venv\Scripts\pytest tests/test_mcp_postgres.py -v` reports **all green**, with at minimum: one test per tool happy path, one test for the unknown-`company_id` null case, one test for the `limit` clamp, one test verifying the in-process MCP client lists exactly three tools.
- **AC-T7 (Latency):** A pytest-bench or simple `time.perf_counter` loop in the test suite measures p95 ≤ 150 ms across 50 sequential `get_customer_by_id` calls warm-pool.
- **AC-T8 (Container health, task 3.1.7):** `docker compose up -d mcp-postgres-server` followed by `docker compose ps mcp-postgres-server` shows the service running and `postgres-db` healthy. Logs include a successful pool open at startup.

### 6.2 Business & Functional Alignment
- **AC-B1 (Design fidelity):** The three tools and the schema they read map 1:1 to the "PostgreSQL Server: company health records, subscription states, payment profiles" responsibility named in `design.md` § 2 Component B.
- **AC-B2 (Security posture):** The two-layer read-only enforcement — protocol surface (only SELECT-shaped tools) **and** DB grants (`estc_reader` has SELECT only) — directly honors design.md § 2's constraint "The orchestration model cannot execute raw SQL queries or touch bash command-lines."
- **AC-B3 (Downstream consumability):** A subsequent Phase 4.3.3 (`billing_agent`) integration test, when wired against this server, can produce a draft response that mentions the customer's `subscription_tier` (the explicit Plan §4.3.3 verification). I.e. the DTOs from §4.1 carry the fields the agent prompt template needs.
- **AC-B4 (Compose split):** The Plan's explicit Decision ("Split into `postgres-db` and `mcp-postgres-server`") is realized. After this phase, `docker compose ps` lists both services; Phase 1.5.3 / 1.5.4 verifications still pass against the renamed `postgres-db`.
- **AC-B5 (Phase 3.3 exit-gate readiness):** The PostgreSQL half of the Phase 3 exit gate (3.3.1) — "MCP inspector against both servers, only read-style tools appear" — is fully satisfiable on completion of 3.1.1–3.1.7, awaiting only Phase 3.2 (GitHub MCP) for the joint check.
