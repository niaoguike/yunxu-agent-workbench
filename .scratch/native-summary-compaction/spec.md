# Native summary compaction

Follow Cindy's executor ownership: run native compaction at a safe turn boundary,
default 90% with configurable 50–95%, and preserve native automatic compaction.
Observe completion independently of memory opt-in. Save only returned plaintext
digests when current project opt-in allows it. Keep original history, prevent
repeated history reinjection on native continuation, and preserve successful
user results when auxiliary maintenance fails. Never fabricate summaries when
the executor's protocol exposes only a completion marker.

Scope: Codex App Server and Claude SDK adapters, workflow integration, local
protocol/HTTP tests and operator documentation. Pi is not an installed Yunxu
executor and is not added. No front-end controls or new provider are required.
