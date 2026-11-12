import pytest

from workbench.history_context import history_context, continuation_context


def test_resume_does_not_reinject_consumed_history():
    messages = [
        {"id": "1", "role": "user", "content": "old requirement"},
        {"id": "2", "role": "assistant", "content": "native result"},
        {"id": "3", "role": "user", "content": "new requirement"},
    ]
    assert continuation_context(messages, {"1"}) == "user: new requirement"


def test_small_history_is_unchanged():
    assert history_context([]) == ""
    assert history_context([
        {"role": "user", "content": "目标"},
        {"role": "assistant", "content": "收到"},
    ]) == "user: 目标\n\nassistant: 收到"


def test_long_history_keeps_goal_recent_messages_and_originals():
    messages = [{"role": "user", "content": "最初目标：保留兼容性。" + "甲" * 2000}]
    messages += [{"role": "assistant", "content": "日志" * 1000} for _ in range(80)]
    messages += [{"role": "user", "content": "最新要求：补充测试"}]
    before = [dict(m) for m in messages]
    result = history_context(messages)
    assert len(result) <= 100000
    assert "最初目标：保留兼容性。" in result
    assert result.endswith("user: 最新要求：补充测试")
    assert "后文省略" in result
    assert messages == before


def test_escaped_text_stays_within_budget():
    messages = [{"role": "user", "content": "\\\n\"" * 2000} for _ in range(30)]
    messages.append({"role": "user", "content": "继续"})
    assert len(history_context(messages, limit=4000, recent_budget=2000)) <= 4000


def test_oversized_latest_message_is_not_silently_cut():
    with pytest.raises(ValueError, match="Latest message"):
        history_context([{"role": "user", "content": "x" * 110000}])
