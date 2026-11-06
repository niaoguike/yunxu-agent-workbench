"""Native risk review and human gates are separate from workflow acceptance."""

from pathlib import Path

from .files import safe_path


def codex_permissions(mode: str, auto_review_supported: bool = True) -> dict:
    if mode not in {"auto", "ask"}:
        raise ValueError("Unknown permission mode")
    # Always override a resumed thread's sticky reviewer; never widen the sandbox.
    return {
        "approvalPolicy": "on-request",
        "sandbox": "workspace-write",
        **(
            {
                "approvalsReviewer": "auto_review"
                if mode == "auto" and auto_review_supported
                else "user"
            }
            if auto_review_supported
            else {}
        ),
    }


NATIVE_CLAUDE_TOOLS = {
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "Glob",
    "Grep",
    "Bash",
    "NotebookEdit",
    "WebSearch",
    "WebFetch",
    "Task",
    "Agent",
    "TodoWrite",
    "TaskCreate",
    "TaskUpdate",
    "TaskGet",
    "TaskList",
    "TaskOutput",
    "TaskStop",
}


def claude_needs_human(mode: str, tool: str, inputs: dict, workspace: str) -> bool:
    if mode == "auto" and tool.startswith("mcp__workbench__"):
        from .bridge import TOOLS

        if tool.removeprefix("mcp__workbench__") in {entry["name"] for entry in TOOLS}:
            return False
    if mode != "auto" or tool not in NATIVE_CLAUDE_TOOLS:
        return True
    for key in ("file_path", "notebook_path", "path"):
        value = inputs.get(key)
        if value:
            if not isinstance(value, str):
                return True
            try:
                root = Path(workspace).resolve()
                path = Path(value)
                relative = (
                    path.relative_to(root).as_posix() if path.is_absolute() else value
                )
                safe_path(root, relative)
            except (ValueError, OSError):
                return True
    # Defer ordinary native tools to Claude's own auto classifier. This is not
    # PermissionResultAllow: native risk evaluation and sandbox rules still apply.
    return False
