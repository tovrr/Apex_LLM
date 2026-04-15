# Distillation Progress Report (2026-04-05)

## Baseline vs Current Hard Eval

- Baseline report: evals/reports/baseline_official.json
- Current report: evals/reports/eval_20260405T000200Z.json

### Deltas

- score delta: 75.8% -> 73.8% (delta -2.1 pts)
- pass delta: 13/20 -> 11/20 (delta -2)
- latency delta: 16930 ms -> 18164 ms (delta +1234 ms median)

## Remaining Failure Categories

- reasoning: 4
- code: 2
- language: 1
- safety: 1
- instruction_following: 1

Failed IDs: h001, h002, h003, h007, h009, h010, h012, h016, h017

Most frequent missing phrases:
minimum, maximum, cache, timeout, return, 18000, jeu, in_degree, queue, trace, 0.9

## Actions Executed

- Validated v2 dataset integrity (100 examples, schema clean).
- Confirmed local machine has no CUDA GPU.
- Generated v3 focused dataset with 180 targeted examples:
  - dataset_expert_v3.json
- Added hard-failure analysis helper:
  - evals/report_hard_failures.py
- Added reproducible GPU distillation launcher:
  - evals/run_gpu_distill_v3.sh
- Ran CPU smoke path for training pipeline with APEX_DATASET_FILE=dataset_expert_v3.json.

## Next Iteration Plan

1. Run evals/run_gpu_distill_v3.sh on GPU runtime (Colab/RunPod/Kaggle).
2. Replace local adapter folder with generated apex_lora_final artifact.
3. Re-run hard eval and compare against baseline_official.json.
4. If score < 90%, add 40-60 examples only for still-failing IDs and repeat.
