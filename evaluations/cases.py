"""Frozen task inputs and independent oracles. Expected outputs never enter task files."""

import json
import subprocess
import sys
from pathlib import Path

VERSION = "1.0"
CASES = {
    "csv": {
        "title": "订单清洗与汇总",
        "files": {
            "orders.csv": """order_id,customer,amount,status
1,Alice,10.25,paid
2,Bob,20.00,paid
3,Alice,5.75,PAID
2,Bob,999.00,paid
4,Carol,8.00,refunded
5,Bob,-1.00,paid
6,Alice,oops,paid
7, Carol ,3.50, paid
8,Bob,0.00,paid
9,,15.00,paid
10,Alice,NaN,paid
11,Bob,1.001,paid
"""
        },
        "prompt": "Read orders.csv. Produce summary.json and checks.md. Trim every field; order_id dedup keeps the first occurrence before any filtering. Include only case-insensitive paid rows, nonempty customer, finite nonnegative decimal amount with at most two decimal places. Customer names stay case-sensitive. Exact JSON schema: {currency:'CNY',paid_order_count:integer,revenue:'0.00',customers:[{customer:string,orders:integer,revenue:'0.00'}]}. Sort customers alphabetically. Money must use decimal arithmetic and fixed two-place strings. Document actual checks. Do not change orders.csv.",
        "artifacts": ["summary.json", "checks.md"],
    },
    "billing": {
        "title": "计费函数缺陷修复",
        "files": {
            "billing.py": "def invoice_total(items, discount='0'):\n    return str(sum(float(i['price']) * i['qty'] for i in items) * (1 - float(discount) / 100))\n"
        },
        "prompt": "Fix billing.py invoice_total(items, discount='0'). Return exact two-decimal string using Decimal and ROUND_HALF_UP once after discount on total. items is a list of dicts: price is a decimal string (finite, nonnegative, at most two decimal places); qty is a positive int, never bool. Discount is a finite decimal string from 0 to 100 inclusive. Empty list returns '0.00'. Invalid types, missing fields, nonfinite values, invalid precision and out-of-range numbers raise ValueError. No third-party dependencies. Preserve function signature. Create tests you actually run and checks.md with evidence. Deliver billing.py and checks.md.",
        "artifacts": ["billing.py", "checks.md"],
    },
    "evidence": {
        "title": "离线资料证据核验",
        "files": {
            "sources.json": json.dumps(
                [
                    {
                        "id": "S1",
                        "date": "2026-10-01",
                        "type": "superseded",
                        "text": "Alpha monthly price 80 CNY; replaced by S2.",
                    },
                    {
                        "id": "S2",
                        "date": "2026-11-01",
                        "type": "contract",
                        "text": "Alpha monthly price 120 CNY. CSV export included. Data region CN.",
                    },
                    {
                        "id": "S3",
                        "date": "2026-11-01",
                        "type": "contract",
                        "text": "Beta monthly price 90 CNY. CSV export costs another 40 CNY per month. Data region EU.",
                    },
                    {
                        "id": "S4",
                        "date": "2026-11-02",
                        "type": "marketing",
                        "text": "Beta has no downtime ever. No measured availability evidence provided.",
                    },
                    {
                        "id": "S5",
                        "date": "2026-11-03",
                        "type": "incident",
                        "text": "Beta incident lasted 30 minutes. Alpha incident lasted 10 minutes. No observation window is specified.",
                    },
                    {
                        "id": "S6",
                        "date": "2026-11-04",
                        "type": "requirement",
                        "text": "Require CSV export and CN data residency. Monthly budget 150 CNY.",
                    },
                ],
                ensure_ascii=False,
                indent=2,
            )
        },
        "prompt": "Use only sources.json, no browsing. Produce report.json and checks.md. Schema: {recommendation:'Alpha'|'Beta'|'insufficient',monthly_costs:{Alpha:number,Beta:number},eligible:{Alpha:boolean,Beta:boolean},availability_percentage:null,findings:[{claim:string,source_ids:[string]}]}. Include required export in costs, apply residency and budget, never use superseded prices. Availability percent is unknown without an observation window. Findings must explicitly cite current pricing for each vendor, the residency requirement, and the contradiction between Beta marketing and its incident evidence. Mention both S4 and S5 in one finding for that contradiction. Do not modify sources.json. Document actual checks.",
        "artifacts": ["report.json", "checks.md"],
    },
}

BILLING_ORACLE = r"""
import runpy
f=runpy.run_path('billing.py')['invoice_total']
valid=[([], '0', '0.00'),([{'price':'0.10','qty':3}], '0', '0.30'),([{'price':'10.05','qty':1}], '10', '9.05'),([{'price':'1.00','qty':1}], '100', '0.00'),([{'price':'2.50','qty':2},{'price':'0.25','qty':2}], '0', '5.50')]
invalid=[([{'price':'-1','qty':1}],'0'),([{'price':'NaN','qty':1}],'0'),([{'price':'Infinity','qty':1}],'0'),([{'price':'1.001','qty':1}],'0'),([{'price':'1','qty':True}],'0'),([{'price':'1','qty':0}],'0'),([{'price':'1','qty':1.0}],'0'),([{'price':1,'qty':1}],'0'),([{'qty':1}],'0'),([], '-1'),([], '101'),([], 'NaN'),([], 0),(None,'0')]
for items,d,want in valid:
    got=f(items,d)
    assert got==want,(items,d,got,want)
for items,d in invalid:
    try:f(items,d)
    except ValueError:pass
    else:raise AssertionError(('invalid accepted',items,d))
print('19 hidden billing checks passed')
"""


def validate(case_id: str, root: Path):
    """Independent semantic oracle, never trusts delivery.json check claims."""
    checks = []

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})

    case = CASES[case_id]
    try:
        for name in case["artifacts"]:
            check(
                "artifact:" + name,
                (root / name).is_file() and (root / name).stat().st_size > 0,
            )
        for name, original in case["files"].items():
            if case_id != "billing":
                check(
                    "input_unchanged:" + name,
                    (root / name).read_text(encoding="utf-8-sig") == original,
                )
        if case_id == "csv":
            got = json.loads((root / "summary.json").read_text(encoding="utf-8-sig"))
            expected = {
                "currency": "CNY",
                "paid_order_count": 5,
                "revenue": "39.50",
                "customers": [
                    {"customer": "Alice", "orders": 2, "revenue": "16.00"},
                    {"customer": "Bob", "orders": 2, "revenue": "20.00"},
                    {"customer": "Carol", "orders": 1, "revenue": "3.50"},
                ],
            }
            for key, value in expected.items():
                check("exact:" + key, got.get(key) == value)
        elif case_id == "billing":
            p = subprocess.run(
                [sys.executable, "-I", "-c", BILLING_ORACLE],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            check("19_hidden_billing_cases", p.returncode == 0)
            if p.returncode:
                checks[-1]["detail"] = p.stderr[-1500:]
        elif case_id == "evidence":
            got = json.loads((root / "report.json").read_text(encoding="utf-8-sig"))
            for key, value in {
                "recommendation": "Alpha",
                "monthly_costs": {"Alpha": 120, "Beta": 130},
                "eligible": {"Alpha": True, "Beta": False},
                "availability_percentage": None,
            }.items():
                check("exact:" + key, key in got and got[key] == value)
            findings = got.get("findings", [])
            refs = [
                set(f.get("source_ids", []))
                for f in findings
                if isinstance(f, dict) and f.get("claim")
            ]
            check(
                "valid_citations",
                bool(refs)
                and all(r and r <= {"S1", "S2", "S3", "S4", "S5", "S6"} for r in refs),
            )
            check("required_sources", {"S2", "S3", "S6"} <= set().union(*refs))
            check("contradiction_cited", any({"S4", "S5"} <= r for r in refs))
    except Exception as error:
        checks.append(
            {"name": "validator_error", "passed": False, "detail": str(error)[:1500]}
        )
    return checks
