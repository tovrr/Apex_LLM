# GGUF Export Guide: Apex LoRA to Offline Model

## Overview

Convert your fine-tuned Apex (Phi-4 + LoRA V4) into a **GGUF quantized model** that runs offline in Ollama.

**Benefits:**
- ✅ No internet required (Groq, HuggingFace)
- ✅ Lower cost (~$0, one-time conversion)
- ✅ Full Apex identity (LoRA baked in)
- ✅ 8x faster inference than HF transformers

---

## Step 1: Merge LoRA into Base Model

```bash
cd C:\Users\omero\GitHub\repos\Apex-llm

# Activate venv
.\venv\Scripts\activate

# Run merge script (outputs: ./apex_merged/)
python scripts/merge_lora_to_gguf.py --lora-dir ./apex_lora_sauvegarde --output ./apex_merged
```

**Expected output:**
```
Loading base model: microsoft/Phi-4-instruct
Loading LoRA adapter from: ./apex_lora_sauvegarde
Merging LoRA into base model...
Saving merged model to: ./apex_merged
✅ Merged model saved to ./apex_merged
```

**Disk usage:** ~25 GB (full precision F32)

---

## Step 2: Clone llama.cpp (one-time)

```bash
cd ~\Downloads
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp

# Build (requires MSVC or MinGW)
cmake -B build
cmake --build build --config Release
```

**Alternative (if build fails):** Download pre-built exe from [llama.cpp releases](https://github.com/ggerganov/llama.cpp/releases).

---

## Step 3: Convert HF → GGUF F32

```bash
cd ~\Downloads\llama.cpp

python convert-hf-to-gguf.py C:\Users\omero\GitHub\repos\Apex-llm\apex_merged --outfile apex_F32.gguf
```

**Expected output:**
```
GGUF: Writing header
GGUF: Adding tokens
GGUF: Adding metadata
GGUF: Writing apex_F32.gguf
```

**Disk usage:** ~25 GB (same as merged)

---

## Step 4: Quantize to Q4_K_M (8-bit, best balance)

```bash
# In llama.cpp directory
.\build\Release\quantize.exe apex_F32.gguf apex_Q4_K_M.gguf Q4_K_M
```

**Expected output:**
```
quantizing 'apex_F32.gguf' using Q4_K_M
Progress: 100%
Quantization complete
```

**Disk usage:** ~4-5 GB (compressed)

**Quantization options (if you want alternatives):**
- `Q5_K_M` — 5 bits, better quality (~6 GB)
- `Q4_K_S` — smaller Q4 variant (~3 GB, faster)
- `IQ4_XS` — extreme compression (~2-3 GB, lower quality)

---

## Step 5: Register with Ollama

```bash
# Copy GGUF to a safe location
cp apex_Q4_K_M.gguf C:\Users\omero\GitHub\repos\Apex-llm\

# Register as Ollama model
cd C:\Users\omero\GitHub\repos\Apex-llm
ollama create apex:prod -f Modelfile.apex.gguf
```

**Expected:**
```
transferring model data
creating model layer
creating config layer
creating manifest layer
success
```

---

## Step 6: Test Locally

```bash
# Test inference (no internet needed!)
ollama run apex:prod "What is SRE in 2 sentences?"
```

**Expected response:**
```
Site Reliability Engineering (SRE) is a discipline that applies software engineering 
principles to infrastructure and operations to build scalable, reliable systems. 
It emphasizes automation, monitoring, and incident response to reduce toil and 
ensure service availability.
```

---

## Step 7: Deploy to Quill (via Ollama proxy)

Update your `.env` to use local Apex:

```env
APEX_OLLAMA_URL=http://127.0.0.1:11434
APEX_OLLAMA_MODEL_FAST=apex:prod
APEX_OLLAMA_MODEL_DEFAULT=apex:prod
APEX_OLLAMA_MODEL_REASONING=apex:prod
```

Start server:
```bash
uvicorn serveur_api:app --reload
```

---

## Troubleshooting

**Error: "adapter_config.json not found"**
- Ensure LoRA is in `./apex_lora_sauvegarde/`
- Check: `ls apex_lora_sauvegarde/adapter_config.json`

**Error: "cannot convert to GGUF" (OOM)**
- Reduce batch size in convert script
- Use a machine with ≥32 GB RAM

**Ollama can't load GGUF**
- Verify path: `ollama show apex:prod`
- Rebuild: `ollama delete apex:prod && ollama create apex:prod -f Modelfile.apex.gguf`

---

## File Locations

| File | Location | Size | Purpose |
|------|----------|------|---------|
| LoRA V4 | `./apex_lora_sauvegarde/` | 51 MB | Fine-tuned adapter |
| Merged HF | `./apex_merged/` | ~25 GB | Phi-4 + LoRA fused |
| GGUF F32 | `./apex_F32.gguf` | ~25 GB | Unquantized GGUF |
| **GGUF Q4** | `./apex_Q4_K_M.gguf` | **~4 GB** | **← Production** |

---

## Timeline

- **Merge + F32 conversion:** ~30 min (depends on GPU)
- **Quantization:** ~10 min
- **Total:** ~45 min one-time

---

## Next Steps

1. Run the merge script on a machine with GPU (faster) or CPU (slower)
2. Quantize to Q4_K_M
3. Commit `apex_Q4_K_M.gguf` to git-lfs (for distribution)
4. Update `.env` to point to `apex:prod`
5. Test in Quill

After you improve LoRA to V5 (85%+ quality), repeat this process → `apex:prod-v5`.
