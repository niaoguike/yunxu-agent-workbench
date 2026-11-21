"""Native compaction policy; never fabricate usage or summary text."""

import math

from .loop_review import redact
from .store import now, uid


class NativeCompaction:
    def __init__(self, threshold=90):
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 50 <= threshold <= 95:
            raise ValueError("Compaction threshold must be an integer from 50 to 95")
        self.threshold = threshold
        self.latest = None
        self.fired = False

    def usage(self, used, window):
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (used, window)):
            return
        if used >= 0 and window > 0:
            self.latest = used / window

    def should_compact(self):
        if self.fired or self.latest is None or self.latest < self.threshold / 100:
            return False
        self.fired = True
        return True

    def boundary(self):
        self.latest = None
        self.fired = False


def save_digest(store, project_id, task_id, engine, session_id, data):
    """Persist only native-provided plaintext, gated by current project opt-in."""
    project = store.one("SELECT memory_enabled,deleted_at FROM projects WHERE id=?", (project_id,))
    summary = data.get("summary")
    if not project or project["deleted_at"] is not None or not project["memory_enabled"]:
        return None
    if not session_id or not isinstance(summary, str) or not summary.strip():
        return None
    # Keep provenance within the same record so retrieval never loses its scope.
    prefix = f"Native compaction digest; task={task_id}; engine={engine}; session={session_id}\n"
    body = redact(summary.strip())
    content = prefix + body[: max(0, 4000 - len(prefix) - 16)]
    if len(content) < len(prefix) + len(body):
        content += "\n[摘要后文省略]"
    previous = store.one("SELECT id FROM memories WHERE project_id=? AND content=?", (project_id, content))
    if previous:
        return previous["id"]
    memory_id = uid()
    store.execute(
        "INSERT INTO memories(id,project_id,category,content,created_at) VALUES(?,?,?,?,?)",
        (memory_id, project_id, "reference", content, now()),
    )
    return memory_id
