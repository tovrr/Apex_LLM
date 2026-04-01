import argparse
import json
from pathlib import Path


def validate_item(item: object, idx: int) -> list[str]:
    errors: list[str] = []
    if not isinstance(item, dict):
        return [f"[{idx}] item is not an object"]

    instruction = item.get("instruction")
    output = item.get("output")

    if not isinstance(instruction, str) or not instruction.strip():
        errors.append(f"[{idx}] missing/invalid instruction")

    if not isinstance(output, str) or not output.strip():
        errors.append(f"[{idx}] missing/invalid output")
        return errors

    if "<think>" not in output or "</think>" not in output:
        errors.append(f"[{idx}] output missing <think>...</think> block")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate distillation dataset_expert.json")
    parser.add_argument("--file", default="dataset_expert.json", help="Path to dataset JSON file")
    parser.add_argument("--min-count", type=int, default=100, help="Minimum number of examples")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"ERROR: file not found: {path}")
        return 1

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR: invalid JSON: {exc}")
        return 1

    if not isinstance(data, list):
        print("ERROR: root must be a JSON array")
        return 1

    errors: list[str] = []
    for idx, item in enumerate(data):
        errors.extend(validate_item(item, idx))

    print(f"count={len(data)}")
    print(f"schema_errors={len(errors)}")

    if errors:
        for err in errors[:25]:
            print(err)

    if len(data) < args.min_count:
        print(f"ERROR: dataset has {len(data)} examples; requires at least {args.min_count}")
        return 2

    if errors:
        return 3

    print("OK: dataset is valid for distillation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
