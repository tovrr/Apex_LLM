import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def load_report(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Report not found: {path}")
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize failed hard-eval categories and rubric misses")
    parser.add_argument("--report", required=True, help="Path to eval report JSON")
    args = parser.parse_args()

    report = load_report(args.report)
    results = report.get("results", [])
    failed = [r for r in results if r.get("status") == "fail"]

    by_task = Counter(str(r.get("task_type", "unknown")) for r in failed)
    missing = Counter()
    forbidden = Counter()
    for row in failed:
        missing.update(row.get("missing_phrases", []))
        forbidden.update(row.get("forbidden_found", []))

    print("Hard Failure Summary")
    print("=" * 40)
    print(f"report: {args.report}")
    print(f"failed: {len(failed)}/{len(results)}")

    print("\nFailed IDs:")
    print(", ".join(str(r.get("id", "?")) for r in failed) or "none")

    print("\nBy task_type:")
    if by_task:
        for task, count in by_task.most_common():
            print(f"- {task}: {count}")
    else:
        print("- none")

    print("\nTop missing must_contain phrases:")
    if missing:
        for phrase, count in missing.most_common(12):
            print(f"- {phrase}: {count}")
    else:
        print("- none")

    print("\nTop forbidden_found phrases:")
    if forbidden:
        for phrase, count in forbidden.most_common(12):
            print(f"- {phrase}: {count}")
    else:
        print("- none")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
