#!/usr/bin/env python3
"""
merge_lora_to_gguf.py

Merges Apex LoRA adapter with Phi-4 base model and converts to GGUF format.

Usage:
    python scripts/merge_lora_to_gguf.py --lora-dir ./apex_lora_sauvegarde --output ./apex_merged

Prerequisites:
    pip install peft transformers bitsandbytes torch
    git clone https://github.com/ggerganov/llama.cpp.git (for quantization)

Output:
    1. Merged HF model: ./apex_merged/
    2. GGUF (F32): ./apex_merged_F32.gguf (from llama.cpp)
    3. GGUF (Q4_K_M): ./apex_Q4_K_M.gguf (quantized, ~4-8 GB)
"""

import argparse
import os
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def merge_lora_to_hf(lora_dir: str, output_dir: str, base_model: str = "microsoft/Phi-4-instruct") -> None:
    """Merge LoRA adapter into base model and save as HF."""
    try:
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError:
        raise ImportError("Install peft and transformers: pip install peft transformers")

    logger.info(f"Loading base model: {base_model}")
    base = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype="auto",
        device_map="auto",
    )

    logger.info(f"Loading LoRA adapter from: {lora_dir}")
    model = PeftModel.from_pretrained(base, lora_dir)

    logger.info("Merging LoRA into base model...")
    merged = model.merge_and_unload()

    logger.info(f"Saving merged model to: {output_dir}")
    merged.save_pretrained(output_dir)

    logger.info(f"Saving tokenizer to: {output_dir}")
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    tokenizer.save_pretrained(output_dir)

    logger.info(f"✅ Merged model saved to {output_dir}")
    logger.info("Next: convert to GGUF using llama.cpp:")
    logger.info(f"  python llama.cpp/convert-hf-to-gguf.py {output_dir} --outfile apex_merged.gguf")
    logger.info("  ./llama.cpp/quantize apex_merged.gguf apex_Q4_K_M.gguf Q4_K_M")


def main():
    parser = argparse.ArgumentParser(description="Merge Apex LoRA into GGUF")
    parser.add_argument("--lora-dir", default="./apex_lora_sauvegarde", help="Path to LoRA adapter")
    parser.add_argument("--output", default="./apex_merged", help="Output directory for merged model")
    parser.add_argument("--base-model", default="microsoft/Phi-4-instruct", help="Base model name")

    args = parser.parse_args()

    lora_path = Path(args.lora_dir)
    if not lora_path.exists():
        raise FileNotFoundError(f"LoRA directory not found: {args.lora_dir}")

    adapter_config = lora_path / "adapter_config.json"
    if not adapter_config.exists():
        raise FileNotFoundError(f"adapter_config.json not found in {args.lora_dir}")

    output_path = Path(args.output)
    output_path.mkdir(parents=True, exist_ok=True)

    merge_lora_to_hf(str(lora_path), str(output_path), args.base_model)


if __name__ == "__main__":
    main()
