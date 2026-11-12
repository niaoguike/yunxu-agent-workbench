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
the executor; this change bounds platform replay on task continuation/handoff.

Validation:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_history_context.py tests/test_context_signals.py tests/test_api.py -q --basetemp=.scratch/context-compaction-validation
```
