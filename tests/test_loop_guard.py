from workbench.loop_guard import ToolLoopGuard


def test_same_failure_warns_then_stops_without_exposing_tool_payload():
    guard = ToolLoopGuard()
    verdicts = [
        guard.observe("shell", {"command": "private input"}, "same error", failed=True)
        for _ in range(8)
    ]
    assert verdicts[2]["decision"] == "warn"
    assert verdicts[-1]["decision"] == "stop"
    assert verdicts[-1]["repeats"] == 8
    assert "private input" not in str(verdicts)
    assert guard.observe("shell", {}, "error", failed=True) is None


def test_changed_results_or_success_reset_repeated_failure_count():
    guard = ToolLoopGuard(action_limit=1000)
    for index in range(30):
        assert (
            guard.observe("shell", {"command": "test"}, str(index), failed=True) is None
        )
    assert guard.observe("shell", {}, "success") is None
    for _ in range(2):
        assert guard.observe("shell", {}, "same", failed=True) is None
    assert guard.observe("shell", {}, "same", failed=True)["decision"] == "warn"


def test_successful_repeated_actions_only_stop_at_explicit_action_budget():
    guard = ToolLoopGuard(action_limit=10)
    for _ in range(9):
        assert guard.observe("read", {}, "unchanged") is None
    result = guard.observe("read", {}, "unchanged")
    assert result["decision"] == "stop"
    assert result["actions"] == 10


def test_new_run_has_independent_budget():
    first = ToolLoopGuard(retry_limit=2)
    first.observe("tool", {}, "error", failed=True)
    assert first.observe("tool", {}, "error", failed=True)["decision"] == "stop"
    assert (
        ToolLoopGuard(retry_limit=2).observe("tool", {}, "error", failed=True) is None
    )
