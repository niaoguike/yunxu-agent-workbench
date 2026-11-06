"""Export sanitized evidence and a report; never substitutes generated scores for runs."""

import argparse
import hashlib
import json
import statistics
import sqlite3
from collections import defaultdict
from pathlib import Path

from .cases import CASES, validate
from .live import policy_pass
from workbench.files import safe_path


def require_complete(rows, metadata):
    expected = {
        (case, mode, repetition)
        for case in metadata["cases"]
        for mode in metadata["modes"]
        for repetition in range(1, metadata["repeats"] + 1)
    }
    actual = [(r["case"], r["mode"], r["repetition"]) for r in rows]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError(
            "Missing or duplicate trials; refuse a partial/cherry-picked report"
        )


def export_artifacts(rows, data_root, output):
    for row in rows:
        exported = {}
        root = data_root / "workspaces" / row["project_id"] / "main"
        for name in CASES[row["case"]]["artifacts"]:
            if name == "checks.md":
                continue  # Local command paths remain private.
            source = safe_path(root, name)
            if not source.is_file():
                continue
            raw = source.read_bytes()
            expected = row.get("artifacts", {}).get(name)
            if expected and hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError("Artifact changed after measurement: " + name)
            relative = (
                Path("artifacts")
                / f"{row['case']}-{row['mode']}-{row['repetition']}"
                / name
            )
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            exported[name] = relative.as_posix()
        row["exported_artifacts"] = exported


def audit_policy(rows, data_root):
    connection = sqlite3.connect(
        f"file:{(data_root / 'workbench.sqlite3').resolve().as_posix()}?mode=ro",
        uri=True,
    )
    for row in rows:
        if not row.get("task_id"):
            continue
        assigned = [
            {"member_id": member}
            for (member,) in connection.execute(
                "SELECT member_id FROM assignments WHERE parent_task_id=?",
                (row["task_id"],),
            )
        ]
        corrected = policy_pass(assigned, row["mode"], row["native_subagent_events"])
        for check in row["checks"]:
            if check["name"] == "execution_policy" and check["passed"] != corrected:
                row["scoring_correction"] = {
                    "version": "1.0.1",
                    "raw_policy_passed": check["passed"],
                    "raw_overall_passed": row["passed"],
                    "corrected_policy_passed": corrected,
                    "reason": "Exactly one persistent worker permits multiple repair assignments; raw results.jsonl retained unchanged",
                }
                check["passed"] = corrected
                row["passed"] = (
                    row["status"] == "delivered"
                    and not row["timed_out"]
                    and all(c["passed"] for c in row["checks"])
                )
    connection.close()


def live_summary(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["mode"]].append(row)
    result = {}
    for mode, values in groups.items():
        token_values = [r["tokens"]["totalTokens"] for r in values if r["tokens"]]
        result[mode] = {
            "runs": len(values),
            "passed": sum(r["passed"] for r in values),
            "pass_rate": sum(r["passed"] for r in values) / len(values),
            "median_seconds_all_runs": statistics.median(
                r["elapsed_seconds"] for r in values
            ),
            "median_total_tokens_observed": statistics.median(token_values)
            if token_values
            else None,
            "runs_with_usage": len(token_values),
            "median_uncached_input_tokens_observed": statistics.median(
                r["tokens"]["inputTokens"] - r["tokens"]["cachedInputTokens"]
                for r in values
                if r["tokens"]
            )
            if token_values
            else None,
            "median_output_tokens_observed": statistics.median(
                r["tokens"]["outputTokens"] for r in values if r["tokens"]
            )
            if token_values
            else None,
        }
    return result


def loop_summary(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["strategy"]].append(row)
    result = {}
    for strategy, values in groups.items():
        positives = [r for r in values if r["expected_stop"]]
        negatives = [r for r in values if not r["expected_stop"]]
        reviews = [review for row in values for review in row["reviews"]]
        result[strategy] = {
            "traces": len(values),
            "loop_detected": sum(r["stopped"] for r in positives),
            "loop_traces": len(positives),
            "false_stops": sum(r["stopped"] for r in negatives),
            "nonloop_traces": len(negatives),
            "median_actions_to_stop_true_loops": statistics.median(
                r["actions"] for r in positives if r["stopped"]
            )
            if any(r["stopped"] for r in positives)
            else None,
            "review_calls": len(reviews),
            "review_failures": sum(bool(r["error"]) for r in reviews),
            "median_review_seconds": statistics.median(
                r["elapsed_seconds"] for r in reviews
            )
            if reviews
            else None,
            "review_tool_actions": sum(r["tool_actions"] for r in reviews),
            "review_total_tokens_observed": sum(
                r["tokens"]["totalTokens"] for r in reviews if r["tokens"]
            ),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", type=Path, required=True)
    parser.add_argument("--loops", type=Path, required=True)
    parser.add_argument("--reliability", type=Path, required=True)
    parser.add_argument("--diagnostic", type=Path, action="append", default=[])
    parser.add_argument(
        "--output", type=Path, default=Path("docs/evaluations/2026-11-06")
    )
    args = parser.parse_args()
    for root in (args.live, args.loops):
        if not (root / "complete.json").is_file():
            parser.error(f"Incomplete run: {root}")
    live = [
        json.loads(line)
        for line in (args.live / "results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    loops = json.loads((args.loops / "results.json").read_text(encoding="utf-8"))
    reliability = json.loads(
        (args.reliability / "results.json").read_text(encoding="utf-8")
    )
    args.output.mkdir(parents=True, exist_ok=True)
    metadata = json.loads((args.live / "metadata.json").read_text(encoding="utf-8"))
    require_complete(live, metadata)
    audit_policy(live, args.live / "data")
    export_artifacts(live, args.live / "data", args.output)
    connection = sqlite3.connect(
        f"file:{(args.live / 'data' / 'workbench.sqlite3').resolve().as_posix()}?mode=ro",
        uri=True,
    )
    for row in live:
        final = connection.execute(
            "SELECT status FROM tasks WHERE id=?", (row.get("task_id"),)
        ).fetchone()
        row["status_after_stop"] = final[0] if final else None
        row["run_errors"] = [
            error
            for (error,) in connection.execute(
                "SELECT r.error FROM runs r JOIN tasks t ON t.id=r.task_id WHERE t.project_id=? AND r.error IS NOT NULL",
                (row["project_id"],),
            )
        ]
        row["native_timeout"] = any(
            "TimeoutError" in error for error in row["run_errors"]
        )
        if not row["passed"]:
            row["member_checks_after_stop"] = []
            for member_id, status in connection.execute(
                "SELECT member_id,status FROM tasks WHERE project_id=? AND member_id IS NOT NULL",
                (row["project_id"],),
            ):
                root = (
                    args.live
                    / "data"
                    / "workspaces"
                    / row["project_id"]
                    / "members"
                    / member_id
                )
                row["member_checks_after_stop"].append(
                    {"status": status, "checks": validate(row["case"], root)}
                )
    connection.close()
    summary = {
        "live": live_summary(live),
        "loops": loop_summary(loops),
        "reliability": {
            "retry_attempts_per_strategy": sum(
                r["attempts"] for r in reliability if r.get("strategy") == "stable_key"
            ),
            "duplicates": {
                strategy: sum(
                    r.get("duplicate_logical_assignments", 0)
                    for r in reliability
                    if r.get("strategy") == strategy
                )
                for strategy in ("fresh_key_baseline", "stable_key")
            },
            "invariants_passed": sum(r.get("passed", False) for r in reliability),
            "invariants": sum("passed" in r for r in reliability),
        },
    }
    evidence = {
        "metadata": metadata,
        "summary": summary,
        "live_rows": live,
        "loop_rows": loops,
        "reliability_rows": reliability,
    }
    diagnostics = []
    for index, diagnostic_root in enumerate(args.diagnostic, 1):
        if not (diagnostic_root / "complete.json").is_file():
            parser.error("Diagnostic incomplete")
        diagnostic = [
            json.loads(line)
            for line in (diagnostic_root / "results.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        diagnostic_meta = json.loads(
            (diagnostic_root / "metadata.json").read_text(encoding="utf-8")
        )
        require_complete(diagnostic, diagnostic_meta)
        audit_policy(diagnostic, diagnostic_root / "data")
        connection = sqlite3.connect(
            f"file:{(diagnostic_root / 'data' / 'workbench.sqlite3').resolve().as_posix()}?mode=ro",
            uri=True,
        )
        for row in diagnostic:
            row["run_errors"] = [
                error
                for (error,) in connection.execute(
                    "SELECT r.error FROM runs r JOIN tasks t ON t.id=r.task_id WHERE t.project_id=? AND r.error IS NOT NULL",
                    (row["project_id"],),
                )
            ]
        connection.close()
        artifact_base = f"diagnostic/{index}"
        export_artifacts(
            diagnostic, diagnostic_root / "data", args.output / artifact_base
        )
        diagnostics.append(
            {
                "label": diagnostic_root.name,
                "artifact_base": artifact_base,
                "metadata": diagnostic_meta,
                "summary": live_summary(diagnostic),
                "rows": diagnostic,
            }
        )
    evidence["supplementary_diagnostics"] = diagnostics
    (args.output / "results.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 典型任务验收与对照评测",
        "",
        "本报告由实际运行记录生成；失败、超时均计入分母。原始内部运行数据位于 .data/evaluations，公开证据仅导出固定测试数据的指标、检查结果与成果摘要哈希。",
        "",
        "## 真实任务对照",
        "",
        "| 策略 | 通过 / 总数 | 全样本耗时中位数（秒） | 已观测总 Token 中位数 |",
        "| --- | --- | --- | --- |",
    ]
    for mode, row in summary["live"].items():
        lines.append(
            f"| {mode} | {row['passed']} / {row['runs']} | {row['median_seconds_all_runs']:.2f} | {row['median_total_tokens_observed']} |"
        )
    for experiment in diagnostics:
        diagnostic_meta = experiment["metadata"]
        lines += [
            "",
            f"## 补充验收：{experiment['label']}（不计入主实验成功率）",
            "",
            f"固定任务输入和判分，时限 {diagnostic_meta['timeout']} 秒，所选策略每种 {diagnostic_meta['repeats']} 次。运行修复版本及各轮发现的问题见 methodology.md；这些追加样本不能替换原失败或混算主实验分数。判分规则更正如有发生，会保留 scoring_correction 审计。",
            "",
            "| 策略 | 状态 | 独立验收 | 耗时（秒） |",
            "| --- | --- | --- | --- |",
        ]
        for row in experiment["rows"]:
            lines.append(
                f"| {row['mode']} | {row['status']} | {'通过' if row['passed'] else '失败'} | {row['elapsed_seconds']:.2f} |"
            )
    lines += [
        "",
        "耗时包含原生进程启动、平台编排、成员等待及交付；失败样本为到失败/截止时长，不能当作成功交付耗时。Token 使用原生会话累计值按会话去重后汇总主任务、成员及辅助复核，不将缓存命中等同免费；未获取实际账单，美元费用为空。",
        "",
        "| 任务 | 策略 | 轮次 | 截止状态 | 停止后状态 | 外部成果检查 | 整组验收 | 耗时（秒） |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in live:
        artifact_pass = all(
            check["passed"]
            for check in row["checks"]
            if check["name"] != "execution_policy"
        )
        lines.append(
            f"| {row['case']} | {row['mode']} | {row['repetition']} | {row['status']} | {row['status_after_stop']} | {'通过' if artifact_pass else '失败'} | {'通过' if row['passed'] else '失败'} | {row['elapsed_seconds']:.2f} |"
        )
    lines += [
        "",
        "## 循环治理对照",
        "",
        "固定轨迹回放，调用真实受限辅助模型；不是让真实工具故意制造永久失败。标签在运行前固定。",
        "",
        "| 策略 | 永久失败检出 | 非循环误停 | 真循环停止动作中位数 | 复核调用 / 失败 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for strategy, row in summary["loops"].items():
        lines.append(
            f"| {strategy} | {row['loop_detected']} / {row['loop_traces']} | {row['false_stops']} / {row['nonloop_traces']} | {row['median_actions_to_stop_true_loops']} | {row['review_calls']} / {row['review_failures']} |"
        )
    lines += [
        "",
        "## 指派与合并故障注入",
        "",
        f"每策略 {summary['reliability']['retry_attempts_per_strategy']} 次请求。稳定 ID 重复逻辑指派 {summary['reliability']['duplicates']['stable_key']}；每次新 ID 基线重复逻辑指派 {summary['reliability']['duplicates']['fresh_key_baseline']}。重启回执、载荷冲突、文件并发冲突与重复应用共 {summary['reliability']['invariants_passed']} / {summary['reliability']['invariants']} 项不变量通过。",
        "",
        "此项使用真实控制面代码和 SQLite，调度出口被替换为不执行模型的记录边界。统计是重复逻辑指派，不能表述为已避免同等数量真实模型执行。",
        "",
        "## 结论边界",
        "",
        f"这是 {len(metadata['cases'])} 个固定任务、每模式 {metadata['repeats']} 次的小样本工程验收，不能代表开放任务泛化成功率。没有显著性检验、吞吐压测或付费成本结论。循环数据有意对比永久失败与明确授权的暂时重试，不代表业务错误分布。证据引用检查核对结构和必要引用覆盖，不替代专家对报告质量的盲评。",
        "",
        "更多协议、运行环境、并发干扰与失败分析见同目录 methodology.md。",
        "",
    ]
    (args.output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
