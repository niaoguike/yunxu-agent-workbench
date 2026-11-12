Status: ready-for-agent

# Native compaction and digest integration

Implement turn-boundary native compact, configurable threshold, completion reset,
opt-in digest persistence, recoverable failure, and bounded history recovery.

## Comments

Implemented locally. Protocol fixtures and HTTP regressions have passed.
Codex's current schema does not expose plaintext summary; no digest is invented.

## Answer

Completed native turn-boundary compaction for Codex and Claude, default 90%
(50–95% configurable), boundary reset, opt-in/redacted/deduplicated digest
storage, auxiliary-failure session rebuild, and incremental native continuation.
Changed-requirement/session-reset behavior in the API remains intact.

Validation: combined targeted suite passed 54 tests and 2 subtests. After adding
missing-rollout recovery/output-isolation coverage, the final native/runtime
suite passed 16 tests and 2 subtests. HTTP suite passed 33 tests. Compileall and
git diff --check passed. Fixtures validate protocol behavior, not live model
summary quality, retained-message policy, or measured token savings.
