# Implementation contract

Implement the PRD as an executable modular monolith. Python FastAPI, SQLite WAL for single-process development, React TypeScript Vite. A Docker runner is the default; trusted local execution requires explicit configuration. Never manufacture engine results when credentials or binaries are missing.

## Runtime module (backend/workbench/runtime.py)

Use dataclasses `RunRequest(run_id: str, engine: str, workspace: str, prompt: str, session_id: str | None = None, model: str | None = None, instructions: str = '', mcp_servers: dict = field(default_factory=dict))` and `RuntimeEvent(kind: str, data: dict)`.

`RuntimeManager(settings)` exposes `async readiness() -> list[dict]` (engine, available, reason), `async execute(request: RunRequest) -> AsyncIterator[RuntimeEvent]`, `async stop(run_id: str)`, `async approve(run_id: str, approval_id: str, decision: str)`. Events: session {session_id}; delta {text}; message {text}; approval {approval_id, tool, details}; completed {status: completed|interrupted|failed, error?}; diagnostic {message}. Never call completed before native terminal event. Unknown native requests denied. Settings exposes data_dir: Path, runner: str (docker/trusted-local/disabled), codex_command: str, docker_image: str, approval_timeout: int. Implement exact real adapters, container launcher, tests; no other files except backend/workbench/runtime.py, backend/workbench/runner.py, tests/test_runtime.py and docs/runtime.md. Root owns packaging and service.

## HTTP contract (frontend client)

All API paths /api. Cookie sessions. POST /auth/register and /auth/login {email,password} -> user; GET /auth/me -> user; POST /auth/logout. GET /engines -> list. GET/POST /projects ({name,instructions?}) -> object/list; GET/PATCH /projects/{id}. GET/POST /projects/{id}/tasks ({title,mode:free|development,engine:codex|claude,model?}) -> object/list. GET /tasks/{id} -> task including messages, runs, gates. POST /tasks/{id}/messages {content} -> message, queues native run. POST /tasks/{id}/stop. POST /tasks/{id}/gates/{gate_id} {decision:approve|reject,comment?}. POST /tasks/{id}/approvals/{approval_id} {decision:accept|decline}. GET /tasks/{id}/events?after=ID -> list {id,kind,data,created_at}; polling allowed. Task fields id,title,mode,engine,status,stage (0..6),version,project_id. Message role/content/id; run id/status/error; gate id/stage/version/status.

GET /projects/{id}/files -> list {path,size}; GET /projects/{id}/file?path=... -> download; PUT same with {content}; POST /projects/{id}/upload multipart file. GET /projects/{id}/memories; POST {category,content}; PATCH/DELETE /memories/{id}. GET /projects/{id}/extensions; POST {name,kind:skill|mcp,content,enabled}; PATCH/DELETE /extensions/{id}. GET /projects/{id}/members; POST {name,role,engine}; POST /members/{id}/assign {content} -> delegated task in isolated member workspace. POST /projects/{id}/integrate/{task_id} {path} -> apply one member file if original baseline unchanged. PATCH /projects/{id} accepts instructions,memory_enabled. GET /health -> status. API errors {detail:string}. No mocked application results.

Frontend owns frontend/** only, tests/build config there. Root owns backend API/database/orchestration/tests except runtime files. Document any necessary contract adjustment.
