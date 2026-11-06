"""Offline replay: python -m evaluations.verify docs/evaluations/2026-11-06/results.json."""

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from .cases import CASES, validate


def verify_rows(rows, archive_root):
    results = []
    for row in rows:
        with tempfile.TemporaryDirectory(prefix="workbench-oracle-") as temp:
            root = Path(temp)
            for name, content in CASES[row["case"]]["files"].items():
                (root / name).write_text(content, encoding="utf-8")
            hashes_match = True
            for name, relative in row.get("exported_artifacts", {}).items():
                source = (archive_root / relative).resolve()
                if not source.is_relative_to(archive_root.resolve()):
                    raise ValueError("Archive path escapes report directory")
                raw = source.read_bytes()
                hashes_match &= (
                    hashlib.sha256(raw).hexdigest() == row["artifacts"][name]
                )
                (root / name).write_bytes(raw)

            def semantic(check):
                return (
                    check["name"] != "artifact:checks.md"
                    and not check["name"].startswith("input_unchanged:")
                    and check["name"] != "execution_policy"
                )

            actual = {
                c["name"]: c["passed"]
                for c in validate(row["case"], root)
                if semantic(c)
            }
            recorded = {c["name"]: c["passed"] for c in row["checks"] if semantic(c)}
            results.append(
                {
                    "case": row["case"],
                    "mode": row["mode"],
                    "repetition": row["repetition"],
                    "hashes_match": bool(hashes_match),
                    "semantic_checks_match_record": actual == recorded,
                    "semantic_all_pass": all(actual.values()),
                }
            )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    results = verify_rows(evidence["live_rows"], args.evidence.parent)
    for experiment in evidence.get("supplementary_diagnostics", []):
        results += verify_rows(
            experiment["rows"],
            args.evidence.parent / experiment["artifact_base"],
        )
    print(
        json.dumps(
            {
                "scope": "archived artifact hashes and semantic oracle only; not full runtime replay",
                "trials": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not all(
        r["hashes_match"] and r["semantic_checks_match_record"] for r in results
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
