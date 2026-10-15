"""Compare two test runs:  python tester/compare.py before after"""
import json
import sys
from pathlib import Path

import report

ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    a, b = sys.argv[1], sys.argv[2]
    before = json.loads((ROOT / "reports" / a / "results.json").read_text())
    after = json.loads((ROOT / "reports" / b / "results.json").read_text())
    out = ROOT / "reports" / "comparison.html"
    report.write_comparison(before, after, out)
    print(f"{len(before['findings'])} finding(s) before, {len(after['findings'])} after.\nComparison: {out}")
