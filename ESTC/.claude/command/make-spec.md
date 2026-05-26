# Command: Build SDD Specification
**Role:** Context-Grounded Architectural Specification Generator  
**Triggers when user asks:** "Run build-sdd-spec" or "Create phase specification"

---

## Instructions for Claude Engine

When this command is triggered by a user query regarding a phase or task range, execute this strict pipeline sequentially without manual interruption:

### Step 1: Contextual Grounding (The Pre-Check)
Before parsing or structuring anything, open and thoroughly read the following project master documents to capture the complete engineering context:
- Master Architecture & Design Guidelines: `docs/design.md`
- Master Development Roadmap: `docs/plan.md`

### Step 2: Context-Aware Input Parsing
Analyze the user's raw input query. Cross-reference it with the master documents you just read to automatically extract and generate the following structured variables required by our skeleton:
1. **PHASE_NAME:** Extract the exact phase and component identifier.
2. **ASSOCIATED_TASKS:** Map the explicit task numbers from `docs/plan.md`.
3. **CORE_SCOPE:** Identify the specific constraints, database tables, tool schemas, or API hooks tied to this phase from `docs/design.md`.
4. **FILE_PREFIX:** Determine a clean, sequential snake-case prefix (e.g., `01-postgres-mcp`).

### Step 3: Pipeline to the Skeleton
Load the target template skeleton file from the repository:
- Template Location: `.claude/templates/spec-skeleton.md`

Directly command the skeleton file to ingest your newly generated structured variables (`PHASE_NAME`, `ASSOCIATED_TASKS`, `CORE_SCOPE`, and `FILE_PREFIX`). Do not perform any further document lookups.

### Step 4: Write and Store the Specification
Generate the fully detailed, production-grade architectural specification:
- Populate every section of the `spec-skeleton.md` blueprint.
- Ensure the target folder exists and save the output exactly to: `.claude/specs/[FILE_PREFIX]-spec.md`

### Step 5: Final Confirmation
Print a clean notification block stating that the context-grounded specification file has been successfully stored under `.claude/specs/` and is ready for manual verification.