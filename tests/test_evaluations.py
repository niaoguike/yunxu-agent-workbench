import json

from evaluations.cases import CASES, validate
from evaluations.live import metrics
from evaluations.reliability import run as run_faults
from evaluations.report import live_summary, loop_summary, require_complete


def test_independent_csv_oracle_rejects_plausible_wrong_totals(tmp_path):
    for name, text in CASES["csv"]["files"].items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "checks.md").write_text("Agent claims all passed", encoding="utf-8")
    result = {
        "currency": "CNY",
        "paid_order_count": 5,
        "revenue": "39.50",
        "customers": [
            {"customer": "Alice", "orders": 2, "revenue": "16.00"},
            {"customer": "Bob", "orders": 2, "revenue": "20.00"},
            {"customer": "Carol", "orders": 1, "revenue": "3.50"},
        ],
    }
    (tmp_path / "summary.json").write_text(json.dumps(result))
    assert all(check["passed"] for check in validate("csv", tmp_path))
    result["revenue"] = "1038.50"
    (tmp_path / "summary.json").write_text(json.dumps(result))
    assert not all(check["passed"] for check in validate("csv", tmp_path))


def test_billing_oracle_catches_original_bug(tmp_path):
    (tmp_path / "billing.py").write_text(CASES["billing"]["files"]["billing.py"])
    (tmp_path / "checks.md").write_text("self reported passed")
    assert not all(c["passed"] for c in validate("billing", tmp_path))


def test_usage_is_deduplicated_by_session_and_includes_auxiliary():
    events = [
        (
            "task",
            {
                "kind": "usage",
                "data": {"session_id": session, "cumulative": {"totalTokens": tokens}},
            },
        )
        for session, tokens in [
            ("root", 10),
            ("root", 20),
            ("worker", 30),
            ("review", 5),
        ]
    ]
    assert metrics(events)["tokens"]["totalTokens"] == 55
    assert metrics(events)["usage_sessions"] == 3
    assert metrics([])["tokens"] is None


def test_evidence_oracle_requires_grounding_not_only_recommendation(tmp_path):
    (tmp_path / "sources.json").write_text(
        CASES["evidence"]["files"]["sources.json"], encoding="utf-8"
    )
    (tmp_path / "checks.md").write_text("checked")
    output = {
        "recommendation": "Alpha",
        "monthly_costs": {"Alpha": 120, "Beta": 130},
        "eligible": {"Alpha": True, "Beta": False},
        "availability_percentage": None,
        "findings": [{"claim": "all done", "source_ids": ["S2"]}],
    }
    (tmp_path / "report.json").write_text(json.dumps(output))
    assert not all(c["passed"] for c in validate("evidence", tmp_path))


async def test_fault_harness_exercises_real_dispatch_and_merge(tmp_path):
    rows = await run_faults(tmp_path, batches=1, attempts=5)
    retries = {
        row["strategy"]: row for row in rows if row["scenario"] == "response_lost_retry"
    }
    assert retries["fresh_key_baseline"]["duplicate_logical_assignments"] == 4
    assert retries["stable_key"]["duplicate_logical_assignments"] == 0
    assert all(row["passed"] for row in rows if "passed" in row)


def test_report_keeps_failures_and_missing_usage_in_denominator():
    rows = [
        {
            "mode": "single",
            "passed": True,
            "elapsed_seconds": 10,
            "tokens": {
                "totalTokens": 30,
                "inputTokens": 20,
                "cachedInputTokens": 5,
                "outputTokens": 10,
            },
        },
        {"mode": "single", "passed": False, "elapsed_seconds": 30, "tokens": None},
    ]
    summary = live_summary(rows)["single"]
    assert summary["runs"] == 2 and summary["pass_rate"] == 0.5
    assert summary["runs_with_usage"] == 1
    assert summary["median_seconds_all_runs"] == 20
    loop = loop_summary(
        [
            {
                "strategy": "fixed_eight",
                "expected_stop": False,
                "stopped": True,
                "actions": 8,
                "reviews": [],
            }
        ]
    )["fixed_eight"]
    assert loop["false_stops"] == 1 and loop["nonloop_traces"] == 1


def test_report_rejects_partial_or_duplicate_trials():
    import pytest

    meta = {"cases": ["csv"], "modes": ["single", "team"], "repeats": 1}
    rows = [{"case": "csv", "mode": mode, "repetition": 1} for mode in meta["modes"]]
    require_complete(rows, meta)
    for invalid in [rows[:1], rows + rows[:1]]:
        with pytest.raises(ValueError):
            require_complete(invalid, meta)


def test_report_exports_completed_evidence_without_raw_dialogue(tmp_path, monkeypatch):
    import sys
    from workbench.store import Store
    from evaluations.report import main

    live, loops, faults, output = [
        tmp_path / name for name in ("live", "loops", "faults", "report")
    ]
    for folder in (live, loops, faults):
        folder.mkdir()
    Store(live / "data" / "workbench.sqlite3").connection.close()
    (live / "complete.json").write_text("{}")
    (loops / "complete.json").write_text("{}")
    (live / "metadata.json").write_text(
        json.dumps({"cases": ["csv"], "modes": ["single"], "repeats": 1})
    )
    row = {
        "case": "csv",
        "mode": "single",
        "repetition": 1,
        "project_id": "fixture",
        "passed": True,
        "elapsed_seconds": 2,
        "tokens": None,
        "status": "delivered",
    }
    (live / "results.jsonl").write_text(json.dumps(row) + "\n")
    (loops / "results.json").write_text("[]")
    (faults / "results.json").write_text("[]")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "report",
            "--live",
            str(live),
            "--loops",
            str(loops),
            "--reliability",
            str(faults),
            "--output",
            str(output),
        ],
    )
    main()
    evidence = json.loads((output / "results.json").read_text(encoding="utf-8"))
    assert evidence["summary"]["live"]["single"]["passed"] == 1
    assert "events" not in evidence and "messages" not in evidence
    assert (output / "report.md").is_file()
