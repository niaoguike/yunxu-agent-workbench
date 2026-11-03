The runtime uses a separate JSONL worker process. Execution is disabled by default;
Docker is the preferred explicitly enabled runner;
its image must contain Python, this backend, claude-agent-sdk and Codex CLI.
No host credentials or Docker socket are mounted. Provision authentication in the
runner image/environment through your deployment's secret mechanism. Image
availability does not prove account authentication; startup reports native errors.
trusted-local explicitly opts into host execution and host credentials.
Task permission mode is persisted as auto/ask. Claude Auto uses the SDK native
classifier; its hook requires human approval for unknown/MCP tools and protected
or external file paths. Ask retains per-tool confirmation. Native settings
sources are disabled. Codex uses on-request + workspace-write, with explicit
auto_review or user reviewer on both session and turn creation/resume. Unsupported
reviewer protocols fall back to human approval, never Full Access. Unknown native
server requests are denied. Approval timeouts decline. Stop requests native
interrupt and waits for the executor terminal result. Unexpected EOF is failure.
Live Codex trusted-local authentication, streamed reply, and a workspace file
creation/readback have passed; the file check requested zero human approvals in
Auto mode. Native dangerous-action rejection and credentialed Claude Auto remain
separate unverified checks.
Build the runner with docker build -f Dockerfile.runner -t agent-workbench-runner:local .
It runs as uid 1000; mounted workspaces must be writable by that uid.
Native configuration persists beneath /workspace/.native. Do not expose this
reserved credential directory through project file APIs. Provision authentication
there through an operator-controlled workflow. Never commit native secrets.
The stdin reader is a daemon thread so native termination can close stdout even
when the supervisor retains its stdin pipe.

Claude SDK is pinned to 0.2.177 and the runner's Codex CLI to 0.166.0.
The Claude hook configuration was checked against the installed SDK signature;
credentialed tool behavior still requires live verification. Docker readiness
checks daemon/image availability; it does not claim authentication success.
Native completion closes a run, while the platform separately validates the
delivery manifest and handles configured acceptance gates.
