import pytest

from workbench.executors import (
    ClaudeExecutor, CodexExecutor, EXECUTOR_TYPES, create_executor,
)


@pytest.mark.parametrize('engine, expected', [('claude', ClaudeExecutor), ('codex', CodexExecutor)])
def test_factory_creates_native_adapter(engine, expected):
    assert isinstance(create_executor(engine), expected)
    assert create_executor(engine) is not create_executor(engine)


def test_factory_rejects_unknown_engine():
    with pytest.raises(ValueError, match='Unknown engine'):
        create_executor('unknown')


async def test_registered_executor_runs_without_dispatch_changes(monkeypatch):
    calls = []

    class ExtraExecutor:
        async def execute(self, context):
            calls.append(context)

    monkeypatch.setitem(EXECUTOR_TYPES, 'extra', ExtraExecutor)
    context = object()
    await create_executor('extra').execute(context)
    assert calls == [context]
