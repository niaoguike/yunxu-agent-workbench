import pytest
from evaluations.judge import evidence, validate_grade, judge, DIMENSIONS
from workbench.runtime import RuntimeEvent
from workbench.config import Settings


def test_call_events_merge_and_grade_validation():
    events = [("t", {"run_id": "r", "kind": "tool_activity", "data": {
        "item_id": "i", "status": status, "arguments": {"order": "A"}}})
        for status in ("started", "completed")]
    calls = evidence(events)
    assert len(calls) == 1 and calls[0]["status"] == "completed"
    grade = {"calls": [{"id": calls[0]["id"], "correct": False, "reason": "wrong order"}],
             "quality": {**dict.fromkeys(DIMENSIONS, 3), "reason": "partial"}}
    assert validate_grade(grade, calls) == grade
    grade["calls"] *= 2
    with pytest.raises(ValueError):
        validate_grade(grade, calls)


async def test_judge_success_missing_and_failure(tmp_path):
    class Fake:
        async def execute(self, request):
            assert request.review_only and request.review_schema
            yield RuntimeEvent("message", {"text": '{"calls":[],"quality":{"correctness":1,"completeness":1,"grounding":1,"clarity":1,"reason":"empty"}}'})
            yield RuntimeEvent("completed", {"status": "completed"})
    result = await judge(Settings(), tmp_path, "task", [], "", [], {}, Fake())
    assert result["status"] == "completed"
    assert result["accuracy"] is None and result["quality"]["correctness"] == 1
    class Broken:
        async def execute(self, request):
            yield RuntimeEvent("message", {"text": "invalid JSON"})
            yield RuntimeEvent("completed", {"status": "completed"})
    result = await judge(Settings(), tmp_path, "task", [], "", [], {}, Broken())
    assert result["status"] == "error" and result["quality"] is None
