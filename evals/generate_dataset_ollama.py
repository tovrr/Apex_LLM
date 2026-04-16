#!/usr/bin/env python3
"""
Generate Alpaca-format QA dataset from Apex docs using Ollama (phi3:mini).
Reads markdown files, splits into chunks, generates QA pairs per chunk.
Output: data/dataset_apex_easy_v1.json
"""

import json
import re
import sys
import time
from pathlib import Path

import requests

# --- Config ---
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen2.5:1.5b"
DOCS_DIR = Path(__file__).parent.parent  # repo root
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "dataset_apex_easy_v1.json"
CHUNK_SIZE = 800   # chars per chunk (overlap-free)
MAX_CHUNKS_PER_DOC = 8
QA_PER_CHUNK = 2
TIMEOUT = 120

TARGET_DOCS = [
    "SOUL.md",
    "IDENTITY.md",
    "STRATEGY.md",
    "EXPLOITATION.md",
    "AGENTS.md",
    "TOOLS.md",
    "README.md",
]

SYSTEM_PROMPT = """You are an expert AI trainer. Given a text chunk from an AI assistant's documentation, generate {n} question-answer pairs that could be used to train the AI.

Rules:
- Questions must be specific and answerable from the text
- Answers must be factual and based solely on the provided text
- Return a JSON array: [{{"question": "...", "answer": "..."}}, ...]
- No explanations outside the JSON
- Each answer should be 1-3 sentences"""


def chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """Split text into chunks at paragraph boundaries."""
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    chunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 > size and current:
            chunks.append(current.strip())
            current = para
        else:
            current = (current + "\n\n" + para).strip() if current else para
    if current:
        chunks.append(current.strip())
    return chunks


def generate_qa(chunk: str, doc_name: str) -> list[dict]:
    """Call Ollama to generate QA pairs for a chunk."""
    prompt = SYSTEM_PROMPT.format(n=QA_PER_CHUNK) + f"\n\nText chunk from {doc_name}:\n\n{chunk}\n\nJSON:"
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 512},
    }
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        # Extract JSON array from response
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if match:
            pairs = json.loads(match.group())
            return pairs if isinstance(pairs, list) else []
        return []
    except Exception as e:
        print(f"  ⚠ Error generating QA: {e}", file=sys.stderr)
        return []


def main():
    dataset = []
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    for doc_name in TARGET_DOCS:
        doc_path = DOCS_DIR / doc_name
        if not doc_path.exists():
            print(f"⏭ Skipping missing file: {doc_name}")
            continue

        text = doc_path.read_text(encoding="utf-8")
        chunks = chunk_text(text)[:MAX_CHUNKS_PER_DOC]
        print(f"\n📄 {doc_name}: {len(chunks)} chunks")

        for i, chunk in enumerate(chunks):
            print(f"  Chunk {i+1}/{len(chunks)} ({len(chunk)} chars) ...", end=" ", flush=True)
            pairs = generate_qa(chunk, doc_name)
            for pair in pairs:
                q = pair.get("question", "").strip()
                a = pair.get("answer", "").strip()
                if q and a:
                    dataset.append({
                        "instruction": q,
                        "input": "",
                        "output": a,
                    })
            print(f"→ {len(pairs)} pairs (total: {len(dataset)})")
            time.sleep(0.5)  # gentle throttle

    # Write output
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Done! {len(dataset)} QA pairs saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
