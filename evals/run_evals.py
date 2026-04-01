"""evals/run_evals.py — Apex evaluation runner.

Runs all prompts in golden_prompts.jsonl against a live Apex instance,
scores each response, and writes a timestamped JSON report to evals/reports/.

Usage
-----
  python evals/run_evals.py --url http://127.0.0.1:8000 --key YOUR_API_KEY

Options
-------
  --url       Apex base URL (default: http://127.0.0.1:8000)
  --key       Apex API key (default: reads APEX_API_KEY from .env)
  --prompts   Path to golden prompts file (default: evals/golden_prompts.jsonl)
  --out       Output directory for reports (default: evals/reports/)
  --timeout   Per-request timeout in seconds (default: 30)
  --stream    Use /chat/stream endpoint instead of /chat (default: False)

Exit code
---------
  0  — all prompts passed
  1  — one or more prompts failed
  2  — could not reach the Apex server
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Scoring ────────────────────────────────────────────────────────────────────


def score_response(response_text: str, case: dict[str, Any]) -> dict[str, Any]:
    """
    Evaluate a model response against the golden case rules.

    Returns a result dict with:
      passed        : bool   — overall pass/fail
      must_contain  : list of missing phrases (empty = all found)
      must_not_contain: list of forbidden phrases found (empty = none found)
      score         : float  — fraction of checks passed (0.0–1.0)
    """
    text_lower = response_text.lower()

    must_contain: list[str] = case.get("must_contain", [])
    must_not_contain: list[str] = case.get("must_not_contain", [])

    missing   = [p for p in must_contain     if p.lower() not in text_lower]
    forbidden = [p for p in must_not_contain if p.lower() in text_lower]

    total_checks = len(must_contain) + len(must_not_contain)
    passed_checks = (len(must_contain) - len(missing)) + (len(must_not_contain) - len(forbidden))
    score = passed_checks / total_checks if total_checks > 0 else 1.0

    passed = len(missing) == 0 and len(forbidden) == 0

    return {
        "passed": passed,
        "score": round(score, 3),
        "missing_phrases": missing,
        "forbidden_found": forbidden,
    }


# ── API calls ──────────────────────────────────────────────────────────────────


def call_apex(
    base_url: str,
    api_key: str,
    question: str,
    mots_max: int = 200,
    timeout: int = 30,
) -> tuple[str, float]:
    """
    Call POST /chat. Returns (response_text, latency_ms).
    Raises urllib.error.URLError on network failure.
    """
    payload = json.dumps({"question": question, "mots_max": mots_max}).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat",
        data=payload,
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode())
    latency_ms = (time.perf_counter() - t0) * 1000
    return body.get("reponse_apex", ""), round(latency_ms, 1)


# ── Runner ─────────────────────────────────────────────────────────────────────


def load_prompts(path: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def run_evals(
    base_url: str,
    api_key: str,
    prompts_path: str,
    out_dir: str,
    timeout: int,
) -> int:
    """
    Core eval loop. Returns exit code (0 = all passed, 1 = failures, 2 = unreachable).
    """
    cases = load_prompts(prompts_path)
    print(f"Apex Eval Runner — {len(cases)} prompts — {base_url}")
    print("-" * 60)

    results: list[dict[str, Any]] = []
    total = len(cases)
    passed_count = 0
    failed_count = 0
    error_count  = 0

    for case in cases:
        pid   = case["id"]
        ttype = case.get("task_type", "unknown")
        prompt = case["prompt"]

        try:
            response_text, latency_ms = call_apex(base_url, api_key, prompt, timeout=timeout)
            verdict = score_response(response_text, case)
            status = "pass" if verdict["passed"] else "fail"
            if verdict["passed"]:
                passed_count += 1
            else:
                failed_count += 1
        except urllib.error.URLError as exc:
            response_text = ""
            latency_ms    = 0.0
            verdict       = {"passed": False, "score": 0.0, "missing_phrases": [], "forbidden_found": []}
            status        = "error"
            error_count  += 1
            print(f"  [{pid}] ERROR: {exc}")

        icon = "✓" if status == "pass" else ("✗" if status == "fail" else "!")
        score_pct = f"{verdict['score']*100:.0f}%"
        print(f"  {icon} [{pid}] {ttype:<22} {score_pct:>5}   {latency_ms:>7.0f}ms   {prompt[:50]}")

        if status == "fail":
            if verdict["missing_phrases"]:
                print(f"      missing : {verdict['missing_phrases']}")
            if verdict["forbidden_found"]:
                print(f"      forbidden found : {verdict['forbidden_found']}")

        results.append({
            "id":              pid,
            "task_type":       ttype,
            "prompt":          prompt,
            "response":        response_text[:500],
            "status":          status,
            "score":           verdict["score"],
            "latency_ms":      latency_ms,
            "missing_phrases": verdict.get("missing_phrases", []),
            "forbidden_found": verdict.get("forbidden_found", []),
            "notes":           case.get("notes", ""),
        })

    # ── Summary ───────────────────────────────────────────────────────────────
    overall_score = sum(r["score"] for r in results) / total if total else 0
    median_latency = sorted(r["latency_ms"] for r in results)[total // 2] if total else 0

    print("-" * 60)
    print(f"Results: {passed_count}/{total} passed  |  score {overall_score*100:.1f}%  |  median latency {median_latency:.0f}ms")
    if error_count:
        print(f"  {error_count} request(s) failed to reach the server.")

    # ── Write report ──────────────────────────────────────────────────────────
    report = {
        "timestamp":      datetime.now(timezone.utc).isoformat(),
        "apex_url":       base_url,
        "total":          total,
        "passed":         passed_count,
        "failed":         failed_count,
        "errors":         error_count,
        "overall_score":  round(overall_score, 4),
        "median_latency_ms": median_latency,
        "results":        results,
    }

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = os.path.join(out_dir, f"eval_{ts}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"Report written → {report_path}")

    if error_count == total:
        return 2  # server completely unreachable
    return 0 if failed_count == 0 else 1


# ── CLI ────────────────────────────────────────────────────────────────────────


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)

    # Load .env from project root so users don't have to pass --key every time.
    env_path = os.path.join(repo, ".env")
    if os.path.isfile(env_path):
        with open(env_path, encoding="utf-8") as ef:
            for line in ef:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())

    parser = argparse.ArgumentParser(description="Apex evaluation runner")
    parser.add_argument("--url",     default="http://127.0.0.1:8000", help="Apex base URL")
    parser.add_argument("--key",     default=os.getenv("APEX_API_KEY", ""), help="Apex API key")
    parser.add_argument("--prompts", default=os.path.join(here, "golden_prompts.jsonl"))
    parser.add_argument("--out",     default=os.path.join(here, "reports"))
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    if not args.key:
        print("ERROR: No API key. Pass --key or set APEX_API_KEY in .env", file=sys.stderr)
        sys.exit(1)

    sys.exit(run_evals(args.url, args.key, args.prompts, args.out, args.timeout))


if __name__ == "__main__":
    main()
