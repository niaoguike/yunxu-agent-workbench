"""Observe pre-compaction windows; does not silently invent or write memories."""

import json
import math


class ContextSignals:
    def __init__(self, store):
        self.store = store

    def usage(self, project_id, engine, session_id, used, window):
        if (
            not session_id
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in (used, window)
            )
            or used < 0
            or window <= 0
        ):
            return []
        row = self.store.one(
            "SELECT * FROM memory_context_state WHERE project_id=? AND engine=? AND session_id=?",
            (project_id, engine, session_id),
        )
        fired = set(json.loads(row["fired"])) if row else set()
        epoch = row["epoch"] if row else 0
        signals = []
        for threshold in (0.7, 0.85, 0.92):
            if used / window >= threshold and threshold not in fired:
                fired.add(threshold)
                signals.append(
                    {
                        "threshold": threshold,
                        "ratio": round(used / window, 3),
                        "context_tokens": used,
                        "context_window": window,
                        "epoch": epoch,
                        "session_id": session_id,
                        "action": "observe_only",
                    }
                )
        self.store.execute(
            "INSERT INTO memory_context_state VALUES(?,?,?,?,?) ON CONFLICT(project_id,engine,session_id) DO UPDATE SET fired=excluded.fired",
            (project_id, engine, session_id, epoch, json.dumps(sorted(fired))),
        )
        return signals

    def compact(self, project_id, engine, session_id):
        if session_id:
            self.store.execute(
                "INSERT INTO memory_context_state VALUES(?,?,?,1,'[]') ON CONFLICT(project_id,engine,session_id) DO UPDATE SET epoch=epoch+1,fired='[]'",
                (project_id, engine, session_id),
            )
