#!/usr/bin/env bash
set -euo pipefail

# Reproducible GPU distillation entrypoint for Colab/RunPod/Kaggle.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[1/6] Python version"
python --version

echo "[2/6] Install/refresh dependencies"
pip install -r requirements.txt

echo "[3/6] Build v3 failure-focused dataset"
python evals/generate_dataset_expert_v3_from_hard_failures.py

echo "[4/6] Validate dataset schema"
python evals/validate_dataset_expert.py --file dataset_expert_v3.json --min-count 150

echo "[5/6] Train LoRA with v3 dataset"
export APEX_DATASET_FILE=dataset_expert_v3.json
python apex_lora.py

echo "[6/6] Package adapter artifact"
if [ -d "apex_lora_final" ]; then
  rm -f apex_lora_final.zip
  (cd apex_lora_final && zip -r ../apex_lora_final.zip .)
  echo "artifact=apex_lora_final.zip"
else
  echo "ERROR: apex_lora_final folder not found after training" >&2
  exit 1
fi
