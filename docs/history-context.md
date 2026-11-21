# Platform history compaction

`history_context()` replaces the former last-100,000-character slice when
building the platform replay prompt. Below that limit messages are unchanged.
Above it, the prompt contains a bounded extractive digest of older messages and
up to 60,000 characters of complete recent messages. The first older message is
prioritized, followed by the most recent older messages. Excerpts carry source
message positions and explicit omission markers. Original messages are not
modified or deleted. Account/project instructions remain outside this history
prompt in the executor instructions.

This is deterministic extractive compaction, not an LLM-generated semantic
summary. Excerpts can omit important constraints; the prompt explicitly warns
about this. Budgets count characters, not tokens. An oversized latest message
that cannot fit the recent-message budget fails explicitly instead of silently
truncating the current requirement. Native session compaction remains owned by
the executor; this fallback bounds platform replay on new sessions/handoff.

## Native summary compaction (Cindy-style ownership)

Normal user turns keep the native executor's own compaction enabled. In addition,
after a successful user turn, the runner checks native usage against
`WORKBENCH_COMPACTION_THRESHOLD_PCT` (default 90, accepted integer range 50–95).
Claude uses `get_context_usage()` and `/compact`; Codex uses
`thread/tokenUsage/updated` and `thread/compact/start`. No model window is guessed.
The existing 70/85/92% signals remain observations, not three compression jobs.
Auxiliary review requests never launch host compaction.

Compaction runs serially after the user turn, with a 120-second maintenance
timeout. Completion boundaries discard the old usage snapshot. Maintenance output
does not replace the user's answer. Failure retains the user result and causes
the next execution to start a new native session using preserved platform history.

When a native completion event supplies nonempty plaintext `summary`, the
workflow saves it as a `reference` memory, with task/engine/session provenance,
credential redaction, a 4,000-character record bound, and exact-content dedup.
This requires the project's **current** memory opt-in; disabled/deleted projects
do not receive digests. Save failure is nonfatal. No summary is manufactured from
completion metadata or encrypted payloads. In particular, the locally generated
Codex schema exposes only `id` and `type` on `contextCompaction`, so current Codex
events normally record completion without creating a plaintext digest. Native
summary structure and retained-message selection remain executor-owned.

A genuine workflow continuation of an existing native session sends only new
user messages, identified using the preceding run's message-ID checkpoint. It
does not reinject consumed history. New sessions and missing-Codex-rollout
recovery receive the bounded full replay instead. Existing API behavior that
resets sessions on changed requirements or revoked tools is preserved.

Validation uses local protocol fixtures, not authenticated paid model calls.
It covers threshold/reset behavior, both executors, successful and failed
compaction, review exclusion, opt-in/dedup/redaction, incremental native resume,
new-session replay, and existing HTTP task behavior. Live summary quality and
actual token savings still require an authenticated long-conversation test.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_native_compaction.py tests/test_history_context.py tests/test_context_signals.py tests/test_runtime.py tests/test_api.py -q --basetemp=.scratch/native-compaction-validation
```
