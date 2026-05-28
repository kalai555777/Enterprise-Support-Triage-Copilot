# Execution Plan: Phase [PHASE_NUMBER] — [TASK_NAME]
**Source spec:** `[PATH_TO_SPEC]`
**Source plan section:** `docs/plan.md` § Phase [PHASE_NUMBER] (tasks [FIRST_TASK_ID] – [LAST_TASK_ID])
**Status:** AWAITING APPROVAL — no code to be executed until user replies `Proceed`.

---

## Context

This plan operationalizes Phase [PHASE_NUMBER] of the ESTC roadmap: [ONE_SENTENCE_DESCRIPTION_OF_THE_DELIVERABLE_AND_ITS_CONSUMER]. The work has [N] threads that must be done in order:

1. **[THREAD_1_NAME]** ([THREAD_1_KIND]): [THREAD_1_DESCRIPTION].
2. **[THREAD_2_NAME]** ([THREAD_2_KIND]): [THREAD_2_DESCRIPTION].
3. **[THREAD_3_NAME]** ([THREAD_3_KIND]): [THREAD_3_DESCRIPTION].

[OPTIONAL_DESIGN_NOTE — e.g. what this plan deliberately mirrors from a prior phase, or a key structural decision such as single-file vs. split modules.]

Every step below ends with a **Verify** command. The shell is **PowerShell 5.1**. A step is "done" only when its verification passes.

---

## Pre-Flight (read-only sanity checks before any change)

- [ ] **PF-1** [SANITY_CHECK_DESCRIPTION].
  **Verify:** `[VERIFY_COMMAND]` [EXPECTED_RESULT].
- [ ] **PF-2** [SANITY_CHECK_DESCRIPTION].
  **Verify:** `[VERIFY_COMMAND]` [EXPECTED_RESULT].
- [ ] **PF-3** [SANITY_CHECK_DESCRIPTION].
  **Verify:** `[VERIFY_COMMAND]` [EXPECTED_RESULT].
<!-- Add as many PF-N checks as needed: spec presence, prior-phase artifacts, toolchain/venv, required packages, seed/fixture data, network/compose prerequisites. Each one is read-only and ends with a Verify. -->

---

## Task [PHASE_NUMBER].1 — [TASK_TITLE]

### [PHASE_NUMBER].1-a [SUBTASK_TITLE]
- [ ] [ACTION_DESCRIPTION].
  **Verify:** `[VERIFY_COMMAND]` [EXPECTED_RESULT]. [OPTIONAL_AC_REFERENCE — e.g. "Matches AC-T1."]

### [PHASE_NUMBER].1-b [SUBTASK_TITLE]
- [ ] [ACTION_DESCRIPTION].
  **Verify:** `[VERIFY_COMMAND]` [EXPECTED_RESULT].

<!-- A task may be a single checklist item with one Verify, or split into lettered subtasks (-a, -b, -c …) each with their own Verify. Use subtasks when the task has distinct sequential steps. -->

---

## Task [PHASE_NUMBER].2 — [TASK_TITLE]

- [ ] [ACTION_DESCRIPTION].
- [ ] [ACTION_DESCRIPTION].
  **Verify:** `[VERIFY_COMMAND]` [EXPECTED_RESULT]. [OPTIONAL_AC_REFERENCE]

---

<!-- Repeat the "## Task [PHASE_NUMBER].N — [TASK_TITLE]" block for every task in the phase. Common late tasks: a test-harness task listing each required test case as its own checkbox, a static-audit task, and a containerization/compose task. -->

## Task [PHASE_NUMBER].N — [TEST_HARNESS_TASK_TITLE]

### [PHASE_NUMBER].N-a [FIXTURE_OR_CONFTEST_SUBTASK]
- [ ] [ACTION_DESCRIPTION].
  **Verify:** `[VERIFY_COMMAND]` [EXPECTED_RESULT].

### [PHASE_NUMBER].N-b Test cases — must include at minimum ([AC_BAR_REFERENCE])
- [ ] `[test_name_1]` → [WHAT_IT_ASSERTS].
- [ ] `[test_name_2]` → [WHAT_IT_ASSERTS].
- [ ] `[test_name_3]` → [WHAT_IT_ASSERTS].

  **Verify:** `[VERIFY_COMMAND]` reports **all green** with at least [N] passed.

---

## Phase [PHASE_NUMBER] Exit Gate

- [ ] **EG-1 ([GATE_NAME], [AC_BAR_REFERENCE])** — [WHAT_THIS_GATE_PROVES].
  **Verify:** `[VERIFY_COMMAND]` [EXPECTED_RESULT]. **Fallback if [TOOL] is unavailable:** [FALLBACK_DESCRIPTION].

- [ ] **EG-2 ([GATE_NAME])** — [WHAT_THIS_GATE_PROVES].
  **Verify:** `[VERIFY_COMMAND]` reports **0 failed**.

- [ ] **EG-3 (clean-boot regression)** — Confirm everything still passes from a clean state.
  **Verify:** `[CLEAN_BOOT_COMMAND]` then re-run EG-2. Both must succeed.

<!-- Add EG-N gates for any cross-phase / joint readiness checks. -->

---

## Risks & Open Questions

1. **[RISK_TITLE]** — [RISK_DESCRIPTION_AND_MITIGATION].
2. **[RISK_TITLE]** — [RISK_DESCRIPTION_AND_MITIGATION].
3. **[RISK_TITLE]** — [RISK_DESCRIPTION_AND_MITIGATION].

---

## Out of Scope (explicitly deferred)

- [DEFERRED_ITEM] — [WHERE_IT_LIVES — e.g. "Phase X.Y"].
- [DEFERRED_ITEM] — [WHERE_IT_LIVES].
- [DEFERRED_ITEM] — [WHERE_IT_LIVES].

---

**Awaiting `Proceed` to begin execution at PF-1.**
