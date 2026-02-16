# Design: Systematic Test Suite Review & Cleanup

## Problem

The test suite has ~450+ tests across 21 files. Many were generated during rapid development without consistent quality criteria. The result is a mix of high-signal invariant tests and low-signal noise: trivial assertions, brittle string matching, timing-dependent checks, excessive mocking, and duplicate coverage.

Bad tests are a tax. They break on harmless changes, hide real failures in noise, slow down CI, and erode trust in the suite. The goal is a lean, high-signal suite where every test justifies its existence by catching a realistic regression.

## Test Philosophy

Tests exist to catch regressions, not to inflate coverage numbers.

**Quality bar for each test:**
- Must detect a realistic regression
- Must fail for the right reason
- Must be deterministic
- Must be maintainable/readable

**Principles:**
- Test observable behavior, not implementation details
- Avoid over-mocking; use real collaborators where cheap
- Prioritize invariants, boundaries, and failure paths over happy-path duplication
- Prefer fewer high-signal tests over many shallow ones
- No trivial tests (getters, setters, obvious passthroughs)

**Red flags — rewrite or delete when you see:**
- Asserting on internal/private calls rather than outcomes
- Massive mock scaffolding for simple behavior
- Duplicate happy-path tests with tiny permutations
- Timing sleeps or race-prone assertions
- No failure-mode coverage at all

**Quick rubric for each test:**
1. Would this fail on a realistic bug?
2. Is it testing behavior users care about?
3. Is this the cheapest reliable layer to prove it?
4. Is it deterministic and readable?
5. If deleted, what concrete risk increases?

If 2+ answers are weak, the test is probably noise.

## Current Test Inventory

### Summary

| File | Tests | Signal | Key Issues |
|------|-------|--------|------------|
| `test_db.py` | 130+ | High | Core invariants, some timing issues |
| `test_prompts.py` | 64 | Low-Med | Brittle string assertions |
| `test_backend_opencode.py` | 60 | Med | Many trivial init tests, overlaps test_sse |
| `test_cost_guardrails.py` | 60+ | Med | Global state mutation, unmocked clocks |
| `test_metrics.py` | 32 | Med | Timing-dependent, float fragility |
| `test_cli.py` | 30+ | Med-High | Reasonable structure |
| `test_claude_ws.py` | 30+ | Med | Real server tests, port-binding risk |
| `test_merge.py` | 30+ | Med-High | Real git operations |
| `test_doctor.py` | 27 | Med-High | Good invariant coverage |
| `test_race_conditions.py` | 21 | Low-Med | Heavily mocked, may not catch real races |
| `test_git.py` | 21 | Med | Real subprocess, slow, timing-dependent |
| `test_multiworker.py` | 20+ | Med | Dependency ordering |
| `test_orchestrator.py` | 15+ | Med | Few unit tests, mostly integration |
| `test_opencode.py` | 14 | Low-Med | Superficial happy-path |
| `test_integration.py` | 12+ | High | Full pipeline validation |
| `test_sse.py` | 9 | Low | Duplicate coverage with test_backend_opencode |
| `test_ids.py` | 9 | Med | Reasonable, minor |
| `test_queen.py` | 3 | Low | Brittle file path assertions |

### Red Flags by Category

| Category | Severity | Files Affected |
|----------|----------|----------------|
| Timing-dependent (`time.sleep`) | HIGH | test_metrics, test_git, test_db, test_race_conditions |
| Brittle string matching | HIGH | test_prompts (64 tests!) |
| Global state mutation | MEDIUM | test_cost_guardrails (Config class) |
| Excessive mocking | MEDIUM | test_race_conditions |
| Duplicate coverage | MEDIUM | test_sse ↔ test_backend_opencode |
| Floating-point assertions | MEDIUM | test_metrics |
| Trivial single-assertion tests | LOW | test_backend_opencode, test_opencode |
| Environmental dependencies | LOW | test_integration (needs OpenCode server) |

## Review Priority

### P0 — High noise, high risk of false failures

**`test_prompts.py`** (64 tests)
- Problem: String presence assertions on prompt content. Every prompt wording change breaks tests. Tests don't validate that prompts produce good model behavior — they just check strings exist.
- Expected outcome: Reduce to ~10-15 tests covering structural invariants (required sections present, template variables substituted, version hashing works). Delete string content checks.

**`test_race_conditions.py`** (21 tests)
- Problem: Heavy AsyncMock usage means tests validate mock wiring, not actual race behavior. The "event loop blocking" test uses sleep+counter which is non-deterministic.
- Expected outcome: Keep regression tests for known bugs (BUG-1 through BUG-4) but rewrite to use real async primitives where possible. Delete tests that only assert mock call counts.

### P1 — Moderate noise, worth cleaning

**`test_metrics.py`** (32 tests)
- Fix: Replace `time.sleep(0.01)` with explicit timestamp control. Use `pytest.approx()` for float comparisons.

**`test_backend_opencode.py`** (60 tests)
- Fix: Consolidate trivial init/header tests into parameterized suites. Merge overlapping coverage from `test_sse.py`.

**`test_cost_guardrails.py`** (60+ tests)
- Fix: Use Config fixture with proper teardown instead of mutating global state. Mock `datetime.now()` for time-window tests.

**`test_sse.py`** (9 tests)
- Fix: Merge into `test_backend_opencode.py`. Remove duplicate handler dispatch tests.

### P2 — Mostly good, minor fixes

**`test_db.py`** (130+ tests) — Fix timing issues, otherwise high-signal.
**`test_git.py`** (21 tests) — Fix timeout test, otherwise reasonable.
**`test_doctor.py`** (27 tests) — Minor cleanup, good invariant coverage.
**`test_cli.py`** (30+ tests) — Reasonable structure.

### P3 — Low priority

**`test_queen.py`** (3 tests) — Trivial but harmless.
**`test_ids.py`** (9 tests) — Fine as-is.

## Co-Review Process

### Phase 1: Scorecard Generation (AI)

For each file (starting with P0), generate a markdown scorecard:

```markdown
## test_prompts.py Scorecard

| Test | Signal | Deterministic | Layer | Regression Caught | Verdict |
|------|--------|---------------|-------|-------------------|---------|
| test_worker_prompt_contains_title | Low | Yes | Unit | None realistic | DELETE |
| test_prompt_version_changes_on_edit | High | Yes | Unit | Template cache staleness | KEEP |
| ... | ... | ... | ... | ... | ... |

### Proposed Actions
- DELETE: 45 tests (string presence checks)
- KEEP: 12 tests (structural invariants)
- REWRITE: 7 tests (timing-dependent → deterministic)
```

### Phase 2: Human Review

Human reviews the scorecard, overrides verdicts where they disagree. Calibration happens here — if the human keeps tests the AI flagged, the rubric adjusts for subsequent files.

### Phase 3: Execute Cleanup

For each reviewed file:
1. Delete flagged tests
2. Rewrite flagged tests
3. Consolidate duplicates
4. Run full suite to confirm no regression
5. Commit as a single atomic change per file

### Phase 4: Validate

After cleanup, run mutation testing (`mutmut` or similar) on critical modules to verify the leaner suite still catches real bugs. If mutation score drops significantly, targeted tests were deleted too aggressively.

## Metrics

Track before/after:
- **Test count**: Expect 30-40% reduction
- **Suite runtime**: Expect faster (fewer slow git/subprocess tests)
- **Flake rate**: Expect near-zero (timing dependencies removed)
- **Mutation score**: Must not drop significantly on critical modules (db, orchestrator, merge)

## Complexity

Medium — spans all test files but each file is an independent unit of work. Can be done incrementally, one file per session. P0 files first, then P1, etc.
