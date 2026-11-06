"""Per-run evidence-based retry limits; a warning is not proof of a loop."""

import hashlib
import json


class ToolLoopGuard:
    def __init__(self, retry_limit=8, action_limit=100):
        self.retry_limit = retry_limit
        self.action_limit = action_limit
        self.actions = 0
        self.repeats = 0
        self.previous = None
        self.stopped = False

    def observe(self, tool, inputs, result, *, failed=False):
        if self.stopped:
            return None
        self.actions += 1
        raw = json.dumps(
            [tool, inputs, result], sort_keys=True, ensure_ascii=True, default=str
        )
        fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        self.repeats = (
            self.repeats + 1 if failed and fingerprint == self.previous else int(failed)
        )
        self.previous = fingerprint if failed else None
        decision, reason = None, None
        if self.actions >= self.action_limit:
            decision, reason = (
                "stop",
                f"本轮达到 {self.action_limit} 次工具动作上限，已有文件保留。",
            )
        elif failed and self.repeats >= self.retry_limit:
            decision, reason = (
                "stop",
                f"同一工具以相同参数连续失败 {self.repeats} 次且结果未变化，已停止重复重试，请检查原因后继续。",
            )
        elif failed and self.repeats == 3:
            decision, reason = (
                "warn",
                "同一工具连续三次返回相同失败，请调整方法，避免继续重复重试。",
            )
        if decision is None:
            return None
        self.stopped = decision == "stop"
        return {
            "decision": decision,
            "reason": reason,
            "tool": tool,
            "actions": self.actions,
            "repeats": self.repeats,
            "evidence_hash": fingerprint,
        }
