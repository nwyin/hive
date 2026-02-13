## 2026-02-13 - [SQLite N+1 Optimization]
**Learning:** Found N+1 query pattern in `get_agent_capability_scores` where capability history was fetched per-agent. Replaced with single JOIN query.
**Action:** Always check loop-based DB access. Use JOINs or `WHERE IN (...)` to batch fetches.

## 2026-02-13 - [Short ID Collisions]
**Learning:** `generate_id` uses 6-char hex, causing frequent collisions in benchmarks (>4k items).
**Action:** Be wary of short hash IDs in high-volume scenarios or benchmarks. Patch ID generation in load tests.
