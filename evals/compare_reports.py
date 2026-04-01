import argparse
import json
from pathlib import Path


def load_report(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Report not found: {path}")
    return json.loads(p.read_text(encoding="utf-8"))


def fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two Apex eval reports")
    parser.add_argument("--before", required=True, help="Path to baseline report JSON")
    parser.add_argument("--after", required=True, help="Path to new report JSON")
    args = parser.parse_args()

    before = load_report(args.before)
    after = load_report(args.after)

    b_score = float(before.get("overall_score", 0.0))
    a_score = float(after.get("overall_score", 0.0))
    b_pass = int(before.get("passed", 0))
    a_pass = int(after.get("passed", 0))
    b_total = int(before.get("total", 0))
    a_total = int(after.get("total", 0))
    b_latency = float(before.get("median_latency_ms", 0))
    a_latency = float(after.get("median_latency_ms", 0))

    print("Apex Eval Report Comparison")
    print("=" * 40)
    print(f"before: {args.before}")
    print(f"after : {args.after}")
    print("-" * 40)
    print(f"score   : {fmt_pct(b_score)} -> {fmt_pct(a_score)}  (delta {fmt_pct(a_score - b_score)})")
    print(f"passed  : {b_pass}/{b_total} -> {a_pass}/{a_total}  (delta {a_pass - b_pass})")
    print(f"latency : {b_latency:.0f}ms -> {a_latency:.0f}ms  (delta {a_latency - b_latency:+.0f}ms)")

    if a_score > b_score:
        print("result  : IMPROVED")
        return 0
    if a_score < b_score:
        print("result  : REGRESSION")
        return 1

    print("result  : NO CHANGE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
