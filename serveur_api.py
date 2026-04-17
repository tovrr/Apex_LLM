from fastapi import FastAPI, HTTPException, Security, Request
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
import torch
import os
import logging
from typing import Any, Literal, AsyncIterator, cast
import asyncio
import time
import json
import uuid
from collections import defaultdict, deque
from threading import Lock
from threading import Thread
from datetime import datetime, timezone
from dotenv import load_dotenv
import httpx
import key_store
from key_store import _connect  # for freemium quota checks

# Les outils des pros (HuggingFace)
from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer
from peft import PeftModel

# ==========================================
# 1. CONFIGURATION DU SERVEUR ET SÉCURITÉ
# ==========================================
app = FastAPI(title="Apex Pro API", description="L'API officielle de Quill AI (Modèle Fine-Tuné)")

APEX_DEFAULT_SYSTEM_PROMPT = """You are Apex, an advanced AI assistant created and trained by the user.

## Core Identity
- You are Apex, a capable AI assistant designed to assist with a wide range of tasks
- You are helpful, honest, direct, and thoughtful in all interactions
- You prioritize accuracy and clarity over brevity

## Your Capabilities
You excel at:
- Software development and coding across multiple languages
- Technical analysis and problem-solving
- Creative writing and content generation
- Research and information synthesis
- Data analysis and explanations
- General knowledge and reasoning

## Behavioral Guidelines
1. **Honesty First**: If you don't know something, say so clearly. Never fabricate information or pretend certainty you don't have
2. **Precision**: Use exact terminology. When technical precision matters, prioritize it over simplicity
3. **Reasoning**: Show your thinking. Explain your logic and reasoning when relevant
4. **Completeness**: Provide thorough answers. Include context and edge cases when important
5. **Clarification**: Ask clarifying questions when requests are ambiguous
6. **Nuance**: Acknowledge complexity and multiple perspectives when they exist

## What NOT to Do
- Do not claim capabilities you lack
- Do not make up facts, articles, citations, or code examples
- Do not ignore important context or disclaimers
- Do not be unnecessarily verbose
- Do not refuse legitimate requests that are safe and legal
- Do not lecture or condescend

## Response Style
- Be natural and conversational while remaining professional
- Match the user's technical level when possible
- Use formatting (code blocks, lists, emphasis) to improve clarity
- Keep responses focused on what was asked
- Provide working examples when relevant to the request

## For Coding Tasks
- Provide complete, working code examples
- Explain trade-offs and alternatives when relevant
- Include error handling and edge cases
- Comment complex logic
- Suggest testing approaches when appropriate

## For Creative Tasks
- Respect the user's creative vision
- Offer variations and alternatives
- Build on feedback iteratively
- Maintain consistency within the creative work

## For Analysis Tasks
- Present multiple viewpoints when applicable
- Support claims with reasoning
- Acknowledge uncertainty and limitations
- Provide actionable insights when possible

You are direct and efficient—avoid unnecessary pleasantries while remaining respectful. Your goal is to be genuinely useful."""

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(BASE_DIR, "ui")
DATA_DIR = os.path.join(BASE_DIR, "data")
RUNS_FILE = os.path.join(DATA_DIR, "runs_history.jsonl")

if os.path.isdir(UI_DIR):
    app.mount("/ui/assets", StaticFiles(directory=UI_DIR), name="ui-assets")

# Configure structured logging with timestamp and level
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("apex_api")
load_dotenv(override=True)

# ── Key store bootstrap ────────────────────────────────────────────────────────
key_store.init_db()
_LEGACY_RAW_KEY = (os.getenv("APEX_API_KEY") or "").strip()
if not _LEGACY_RAW_KEY:
    raise RuntimeError("APEX_API_KEY est manquant. Définis-la dans le fichier .env.")

# Seed the legacy env key as an 'internal' plan key (idempotent).
try:
    key_store.add_key(_LEGACY_RAW_KEY, label="legacy-env", plan="internal")
    logger.info("Key store: APEX_API_KEY seeded as 'internal' plan.")
except ValueError:
    pass  # Already seeded on a previous startup — fine.

header_cle = APIKeyHeader(name="X-API-Key")

LIMITE_REQUETES = int(os.getenv("APEX_RATE_LIMIT_PER_WINDOW", "30"))
FENETRE_REQUETES_SEC = int(os.getenv("APEX_RATE_WINDOW_SECONDS", "60"))
DELAI_GENERATION_SEC = float(os.getenv("APEX_GENERATION_TIMEOUT_SECONDS", "45"))

# ── Application Startup Logging ────────────────────────────────────────────────
logger.info("=" * 80)
logger.info("APEX API SERVER STARTUP")
logger.info("=" * 80)
logger.info("Environment: %s", os.getenv("ENVIRONMENT", "development"))
logger.info("Python: %s", torch.__version__)
logger.info("Working directory: %s", os.getcwd())
logger.info("Key store initialization complete")

header_cle = APIKeyHeader(name="X-API-Key")

LIMITE_REQUETES = int(os.getenv("APEX_RATE_LIMIT_PER_WINDOW", "30"))
FENETRE_REQUETES_SEC = int(os.getenv("APEX_RATE_WINDOW_SECONDS", "60"))
DELAI_GENERATION_SEC = float(os.getenv("APEX_GENERATION_TIMEOUT_SECONDS", "45"))

_historique_requetes: defaultdict[str, deque[float]] = defaultdict(deque)
_rate_limit_lock = Lock()
_runs_lock = Lock()

# ── Freemium tier quotas (per-day limits) ──────────────────────────────────────
# These come from key_store.py PLANS; checked on every /chat request.
FREEMIUM_QUOTAS = {
    "free": {"requests_per_day": 10, "tokens_per_day": 5_000},
    "pro": {"requests_per_day": 1_000, "tokens_per_day": 500_000},
    "internal": {"requests_per_day": -1, "tokens_per_day": -1},  # unlimited
}
_freemium_usage_lock = Lock()


def _check_freemium_quota(api_key_hash: str, plan: str, tokens_this_request: int) -> dict[str, Any]:
    """
    Check if the key has exceeded daily quota for requests or tokens.
    Returns {"allowed": bool, "reason": str, "usage": {...}}
    """
    quota = FREEMIUM_QUOTAS.get(plan, {})
    req_limit = quota.get("requests_per_day", -1)
    token_limit = quota.get("tokens_per_day", -1)

    # Unlimited plans (-1) always allowed
    if req_limit < 0 and token_limit < 0:
        return {"allowed": True, "reason": "unlimited_plan", "usage": {}}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _freemium_usage_lock:
        with _connect() as conn:
            row = conn.execute(
                "SELECT requests_used, tokens_used FROM usage_daily WHERE key_hash = ? AND date = ?",
                (api_key_hash, today),
            ).fetchone()
        used_req = row[0] if row else 0
        used_tok = row[1] if row else 0

    # Check request quota
    if req_limit > 0 and used_req >= req_limit:
        return {
            "allowed": False,
            "reason": f"exceeded_request_quota_{used_req}/{req_limit}",
            "usage": {"requests_used": used_req, "requests_limit": req_limit},
        }

    # Check token quota
    if token_limit > 0 and (used_tok + tokens_this_request) > token_limit:
        return {
            "allowed": False,
            "reason": f"exceeded_token_quota_{used_tok + tokens_this_request}/{token_limit}",
            "usage": {"tokens_used": used_tok, "tokens_limit": token_limit, "tokens_requested": tokens_this_request},
        }

    return {"allowed": True, "reason": "within_quota", "usage": {"requests_used": used_req, "tokens_used": used_tok}}


def _record_freemium_usage(api_key_hash: str, tokens_used: int) -> None:
    """Increment daily usage counters."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _freemium_usage_lock:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO usage_daily (key_hash, date, requests_used, tokens_used) VALUES (?, ?, 1, ?) "
                "ON CONFLICT(key_hash, date) DO UPDATE SET requests_used = requests_used + 1, tokens_used = tokens_used + ?",
                (api_key_hash, today, tokens_used, tokens_used),
            )
            conn.commit()

# ── Freemium tier quotas (per-day limits) ──────────────────────────────────────
# These come from key_store.py PLANS; checked on every /chat request.
FREEMIUM_QUOTAS = {
    "free": {"requests_per_day": 10, "tokens_per_day": 5_000},
    "pro": {"requests_per_day": 1_000, "tokens_per_day": 500_000},
    "internal": {"requests_per_day": -1, "tokens_per_day": -1},  # unlimited
}
_freemium_usage_lock = Lock()


def _check_freemium_quota(api_key_hash: str, plan: str, tokens_this_request: int) -> dict[str, Any]:
    """
    Check if the key has exceeded daily quota for requests or tokens.
    Returns {"allowed": bool, "reason": str, "usage": {...}}
    """
    quota = FREEMIUM_QUOTAS.get(plan, {})
    req_limit = quota.get("requests_per_day", -1)
    token_limit = quota.get("tokens_per_day", -1)

    # Unlimited plans (-1) always allowed
    if req_limit < 0 and token_limit < 0:
        return {"allowed": True, "reason": "unlimited_plan", "usage": {}}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _freemium_usage_lock:
        with _connect() as conn:
            row = conn.execute(
                "SELECT requests_used, tokens_used FROM usage_daily WHERE key_hash = ? AND date = ?",
                (api_key_hash, today),
            ).fetchone()
        used_req = row[0] if row else 0
        used_tok = row[1] if row else 0

    # Check request quota
    if req_limit > 0 and used_req >= req_limit:
        return {
            "allowed": False,
            "reason": f"exceeded_request_quota_{used_req}/{req_limit}",
            "usage": {"requests_used": used_req, "requests_limit": req_limit},
        }

    # Check token quota
    if token_limit > 0 and (used_tok + tokens_this_request) > token_limit:
        return {
            "allowed": False,
            "reason": f"exceeded_token_quota_{used_tok + tokens_this_request}/{token_limit}",
            "usage": {"tokens_used": used_tok, "tokens_limit": token_limit, "tokens_requested": tokens_this_request},
        }

    return {"allowed": True, "reason": "within_quota", "usage": {"requests_used": used_req, "tokens_used": used_tok}}


def _record_freemium_usage(api_key_hash: str, tokens_used: int) -> None:
    """Increment daily usage counters."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _freemium_usage_lock:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO usage_daily (key_hash, date, requests_used, tokens_used) VALUES (?, ?, 1, ?) "
                "ON CONFLICT(key_hash, date) DO UPDATE SET requests_used = requests_used + 1, tokens_used = tokens_used + ?",
                (api_key_hash, today, tokens_used, tokens_used),
            )
            conn.commit()


def _log_event(name: str, **fields: Any) -> None:
    payload = {"event": name, **fields}
    logger.info(json.dumps(payload, ensure_ascii=False, default=str))

# Security: CORS configuration with explicit permissions
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://quill-ai-xi.vercel.app", "http://localhost:3000"], 
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],  # Explicit method list
    allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],  # Explicit headers
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
)


# Security: Add security headers to all responses
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next: Any) -> Any:
    """Add security headers to all responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubdomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


@app.middleware("http")
async def request_context_middleware(request: Request, call_next: Any) -> Any:
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = request_id
    t0 = time.perf_counter()

    _log_event(
        "request_started",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        client_ip=request.client.host if request.client and request.client.host else "unknown",
    )

    try:
        response = await call_next(request)
    except Exception:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        _log_event(
            "request_failed",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            latency_ms=latency_ms,
        )
        raise

    latency_ms = int((time.perf_counter() - t0) * 1000)
    response.headers["X-Request-ID"] = request_id
    _log_event(
        "request_completed",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        latency_ms=latency_ms,
    )
    return response

# ==========================================
# 2. LE CHEF CUISINIER (Fusion Base + LoRA)
# ==========================================
# Debug print removed - use logger instead (see startup_event for comprehensive logging)


class _FakeModel:
    def __init__(self):
        self.device = torch.device("cpu")

    def generate(self, **_: Any) -> torch.Tensor:
        return torch.tensor([[0, 1, 2]], dtype=torch.long)


class _FakeTokenizer:
    def __call__(self, _: str, return_tensors: str = "pt") -> dict[str, torch.Tensor]:
        if return_tensors != "pt":
            raise ValueError("FakeTokenizer supporte uniquement return_tensors='pt'.")
        return {"input_ids": torch.tensor([[0, 1]], dtype=torch.long)}

    def decode(self, _: Any, skip_special_tokens: bool = True) -> str:
        _ = skip_special_tokens
        return "<|assistant|> reponse de test"


MODEL_TIERS = ("fast", "default", "reasoning")
DEFAULT_MODEL_NAME = "microsoft/Phi-3-mini-4k-instruct"
DEFAULT_MODEL_TIER = os.getenv("APEX_MODEL_DEFAULT_TIER", "default")

MODEL_NAMES: dict[str, str] = {
    "fast": os.getenv("APEX_MODEL_FAST_NAME", DEFAULT_MODEL_NAME),
    "default": os.getenv("APEX_MODEL_DEFAULT_NAME", DEFAULT_MODEL_NAME),
    "reasoning": os.getenv("APEX_MODEL_REASONING_NAME", DEFAULT_MODEL_NAME),
}
MODEL_LORA_DIRS: dict[str, str] = {
    "fast": os.getenv("APEX_MODEL_FAST_LORA_DIR", ""),
    "default": os.getenv("APEX_MODEL_DEFAULT_LORA_DIR", "./apex_lora_sauvegarde"),
    "reasoning": os.getenv("APEX_MODEL_REASONING_LORA_DIR", ""),
}

# ── Ollama backend (optional) ─────────────────────────────────────────────────
# Set APEX_OLLAMA_URL=http://127.0.0.1:11434 to delegate inference to Ollama.
# Ollama model names per tier (override with APEX_OLLAMA_MODEL_FAST, etc.)
APEX_OLLAMA_URL = os.getenv("APEX_OLLAMA_URL", "").rstrip("/")
OLLAMA_MODEL_NAMES: dict[str, str] = {
    "fast": os.getenv("APEX_OLLAMA_MODEL_FAST", "phi3:mini"),
    "default": os.getenv("APEX_OLLAMA_MODEL_DEFAULT", "qwen2.5:7b"),
    "reasoning": os.getenv("APEX_OLLAMA_MODEL_REASONING", "qwen2.5:14b"),
}
OLLAMA_NUM_CTX = int(os.getenv("APEX_OLLAMA_NUM_CTX", "4096"))
APEX_OLLAMA_COMPAT_REQUIRE_KEY = (os.getenv("APEX_OLLAMA_COMPAT_REQUIRE_KEY", "0") or "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# ── OpenAI-compatible backend (optional, e.g. LM Studio) ─────────────────────
# Set APEX_OPENAI_COMPAT_URL=http://localhost:1234 to delegate inference to any
# OpenAI-compatible server (LM Studio, vLLM, text-generation-webui, etc.).
# Takes priority over Ollama when both are set.
APEX_OPENAI_COMPAT_URL = os.getenv("APEX_OPENAI_COMPAT_URL", "").rstrip("/")
OPENAI_COMPAT_MODEL_NAMES: dict[str, str] = {
    "fast": os.getenv("APEX_OPENAI_COMPAT_MODEL_FAST", "phi-4"),
    "default": os.getenv("APEX_OPENAI_COMPAT_MODEL_DEFAULT", "phi-4"),
    "reasoning": os.getenv("APEX_OPENAI_COMPAT_MODEL_REASONING", "phi-4"),
}
APEX_OPENAI_COMPAT_API_KEY = os.getenv("APEX_OPENAI_COMPAT_API_KEY", "lm-studio")

# ── Xiaomi MiMo fallback (quota MiniMax dépassé) ───────────────────────────────
XIAOMI_MIMO_API_KEY = os.getenv("XIAOMI_MIMO_API_KEY", "").strip()
XIAOMI_MIMO_BASE_URL = os.getenv("XIAOMI_MIMO_BASE_URL", "https://api.minimax.chat/v1").rstrip("/")
XIAOMI_MIMO_MODEL = os.getenv("XIAOMI_MIMO_MODEL", "MiMo-7B-Instruct")

# ── Application Startup Logging ────────────────────────────────────────────────
logger.info("=" * 80)
logger.info("APEX API SERVER STARTUP")
logger.info("=" * 80)
logger.info("Environment: %s", os.getenv("ENVIRONMENT", "development"))
logger.info("Python: %s", torch.__version__)
logger.info("Working directory: %s", os.getcwd())
logger.info("Config file: %s", os.path.join(BASE_DIR, ".env"))

# Log environment configuration (sanitized - no secrets)
logger.info("-" * 80)
logger.info("ENVIRONMENT CONFIGURATION")
logger.info("-" * 80)
logger.info("Rate limit: %s requests per %s seconds", LIMITE_REQUETES, FENETRE_REQUETES_SEC)
logger.info("Generation timeout: %s seconds", DELAI_GENERATION_SEC)
logger.info("Skip model load: %s", os.getenv("APEX_SKIP_MODEL_LOAD", "0"))
logger.info("Ollama URL: %s", APEX_OLLAMA_URL or "not configured")
logger.info("OpenAI compat URL: %s", APEX_OPENAI_COMPAT_URL or "not configured")

# Log model tier configuration
logger.info("-" * 80)
logger.info("MODEL TIER CONFIGURATION")
logger.info("-" * 80)
for tier, model_name in MODEL_NAMES.items():
    lora_dir = MODEL_LORA_DIRS.get(tier, "")
    lora_status = "configured" if lora_dir and os.path.isdir(lora_dir) else "not found"
    logger.info("Tier '%s': %s (LoRA: %s)", tier, model_name, lora_status)

# Log key store summary
logger.info("-" * 80)
logger.info("KEY STORE INITIALIZED")
logger.info("-" * 80)
logger.info("Database path: %s", key_store.DB_PATH)
logger.info("Legacy API key configured: [REDACTED]")  # Security: Never log any part of API keys


def _openai_compat_active() -> bool:
    return bool(APEX_OPENAI_COMPAT_URL)


def _ollama_active() -> bool:
    return bool(APEX_OLLAMA_URL) and not _openai_compat_active()

tokenizer: Any | None = None
modele_apex: Any | None = None
_active_model_tier = ""
_model_lock = Lock()
_model_runtime_state_by_tier: dict[str, str] = {tier: "cold" for tier in MODEL_TIERS}
_model_runtime_error_by_tier: dict[str, str] = {tier: "" for tier in MODEL_TIERS}


def _normalize_tier(tier: str | None) -> str:
    candidate = (tier or DEFAULT_MODEL_TIER).strip().lower()
    if candidate not in MODEL_TIERS:
        return "default"
    return candidate


def _tier_from_ollama_model(model: str | None) -> str:
    candidate = (model or "").strip().lower()
    # Strip provider prefix — e.g. "ollama/apex:fast" → "apex:fast"
    if "/" in candidate:
        candidate = candidate.rsplit("/", 1)[-1]
    if not candidate:
        return _normalize_tier(None)

    static_map = {
        "apex:fast": "fast",
        "apex:default": "default",
        "apex:reasoning": "reasoning",
        # Accept raw Ollama model names as tier aliases
        "phi3:mini": "fast",
        "phi3": "fast",
        "qwen2.5:7b": "default",
        "qwen2.5:14b": "reasoning",
    }
    if candidate in static_map:
        return static_map[candidate]

    if "reason" in candidate or "14b" in candidate or "27b" in candidate:
        return "reasoning"
    if "7b" in candidate or "8b" in candidate or "default" in candidate:
        return "default"
    if "fast" in candidate or "mini" in candidate or "3b" in candidate:
        return "fast"
    return _normalize_tier(None)


def _verifier_cle_api_compat(request: Request) -> None:
    if not APEX_OLLAMA_COMPAT_REQUIRE_KEY:
        return

    cle_api = (request.headers.get("X-API-Key") or "").strip()
    if not cle_api:
        # Anthropic-style header
        cle_api = (request.headers.get("x-api-key") or "").strip()
    if not cle_api:
        auth = (request.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            cle_api = auth[7:].strip()

    if not cle_api or key_store.verify_key(cle_api) is None:
        raise HTTPException(status_code=403, detail="Accès refusé : Clé API invalide pour mode Ollama compatible.")


def _extraire_question_depuis_prompt(prompt: str) -> str:
    texte = (prompt or "").strip()
    marqueur = "<|user|>"
    if marqueur in texte:
        texte = texte.split(marqueur)[-1]
    if "<|assistant|>" in texte:
        texte = texte.split("<|assistant|>")[0]
    return texte.strip() or (prompt or "")


def _extraire_systeme_depuis_prompt(prompt: str) -> tuple[str, str]:
    """Extract system prompt (if present) and return (system_prompt, remaining_prompt)."""
    import re
    match = re.match(r'<system>\n(.*?)\n</system>\n(.*)', prompt, re.DOTALL)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "", prompt.strip()


def _messages_vers_prompt(messages: list[dict[str, Any]], system: str | None = None) -> str:
    lignes: list[str] = []
    # Use provided system prompt or fall back to Apex default
    effective_system = system if system else APEX_DEFAULT_SYSTEM_PROMPT
    if effective_system:
        lignes.append(f"<system>\n{effective_system.strip()}\n</system>")
    for message in messages:
        role = str(message.get("role", "user")).strip().lower()
        raw_content = message.get("content", "")
        # OpenClaw / newer Ollama clients may send content as a list of objects
        if isinstance(raw_content, list):
            parts: list[str] = []
            for part in raw_content:
                if isinstance(part, dict):
                    parts.append(str(part.get("text", part.get("content", ""))))
                else:
                    parts.append(str(part))
            content = " ".join(parts).strip()
        else:
            content = str(raw_content).strip()
        if not content:
            continue
        lignes.append(f"<{role}>\n{content}\n</{role}>")
    return "\n".join(lignes).strip()


def _tools_vers_prompt(tools: list[dict[str, Any]] | None) -> str:
    if not tools:
        return ""

    lignes: list[str] = ["[TOOLS_AVAILABLE]"]
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function", tool)
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name", "tool")).strip()
        desc = str(fn.get("description", "")).strip()
        params = fn.get("parameters", {})
        lignes.append(f"- {name}: {desc}")
        if params:
            try:
                lignes.append(f"  params={json.dumps(params, ensure_ascii=False)}")
            except Exception:
                lignes.append("  params=<unserializable>")
    lignes.append("[/TOOLS_AVAILABLE]")
    return "\n".join(lignes)


def _mots_max_depuis_options(options: dict[str, Any]) -> int:
    brut = options.get("num_predict", options.get("max_tokens", 512))
    try:
        valeur = int(brut)
    except (TypeError, ValueError):
        valeur = 512
    # -1 means "no limit" in Ollama — cap at 2048 for safety
    if valeur < 0:
        valeur = 2048
    return max(1, min(valeur, 4096))


def _ndjson(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False) + "\n"


def _charger_modele_runtime(tier: str | None = None) -> str:
    global tokenizer, modele_apex, _active_model_tier
    selected_tier = _normalize_tier(tier)

    if tokenizer is not None and modele_apex is not None and _active_model_tier == selected_tier:
        return selected_tier

    with _model_lock:
        if tokenizer is not None and modele_apex is not None and _active_model_tier == selected_tier:
            return selected_tier

        _model_runtime_state_by_tier[selected_tier] = "loading"
        _model_runtime_error_by_tier[selected_tier] = ""

        logger.info("-" * 80)
        logger.info("MODEL LOADING: tier='%s'", selected_tier)
        logger.info("-" * 80)

        if os.getenv("APEX_SKIP_MODEL_LOAD") == "1":
            logger.warning("APEX_SKIP_MODEL_LOAD=1 active: loading fake test model instead of real model")
            tokenizer = _FakeTokenizer()
            modele_apex = _FakeModel()
            _active_model_tier = selected_tier
            _model_runtime_state_by_tier[selected_tier] = "ready"
            logger.info("Fake model loaded successfully (skip mode)")
            return selected_tier

        start_time = time.time()
        try:
            nom_modele_base = MODEL_NAMES[selected_tier]
            logger.info("Loading tokenizer for model: %s", nom_modele_base)
            tokenizer = AutoTokenizer.from_pretrained(nom_modele_base)
            logger.info("Tokenizer loaded successfully (vocab size: %d)", tokenizer.vocab_size)

            utilise_cuda = torch.cuda.is_available()
            dtype_modele = torch.float16 if utilise_cuda else torch.float32
            device_map_modele = "auto" if utilise_cuda else "cpu"

            logger.info("Device detection: CUDA=%s, dtype=%s, device_map=%s",
                       utilise_cuda, dtype_modele, device_map_modele)
            logger.info("Loading base model (%s): %s", selected_tier, nom_modele_base)
            
            modele_base = AutoModelForCausalLM.from_pretrained(
                nom_modele_base,
                torch_dtype=dtype_modele,
                device_map=device_map_modele,
                attn_implementation="eager",
            )
            logger.info("Base model loaded successfully")

            dossier_lora = MODEL_LORA_DIRS.get(selected_tier, "")
            if dossier_lora and os.path.isdir(dossier_lora):
                logger.info("LoRA adapter found: %s", dossier_lora)
                logger.info("Applying LoRA adapter to base model...")
                try:
                    modele_apex = PeftModel.from_pretrained(modele_base, dossier_lora)
                    logger.info("LoRA adapter applied successfully")
                except Exception as e:
                    logger.warning(
                        "LoRA adapter loading failed (%s). Falling back to base model.",
                        e,
                    )
                    modele_apex = modele_base
            else:
                logger.info("No LoRA adapter configured for tier '%s'", selected_tier)
                modele_apex = modele_base

            load_duration = time.time() - start_time
            logger.info("Model loading complete in %.2f seconds", load_duration)
            logger.info("Active model tier: %s", selected_tier)

            _active_model_tier = selected_tier
            _model_runtime_state_by_tier[selected_tier] = "ready"
            return selected_tier
        except Exception as exc:
            load_duration = time.time() - start_time
            _model_runtime_state_by_tier[selected_tier] = "error"
            _model_runtime_error_by_tier[selected_tier] = str(exc)
            logger.exception("Model loading failed after %.2f seconds: %s", load_duration, exc)
            raise


def _verifier_rate_limit(cle_api: str, ip_client: str) -> None:
    identite = f"{cle_api}:{ip_client}"
    maintenant = time.time()
    seuil = maintenant - FENETRE_REQUETES_SEC

    with _rate_limit_lock:
        historique = _historique_requetes[identite]
        while historique and historique[0] < seuil:
            historique.popleft()

        if len(historique) >= LIMITE_REQUETES:
            raise HTTPException(
                status_code=429,
                detail="Trop de requêtes. Réessaie dans quelques instants.",
            )

        historique.append(maintenant)


def _append_run(record: dict[str, Any]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with _runs_lock:
        with open(RUNS_FILE, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _charger_runs(limit: int = 20) -> list[dict[str, Any]]:
    if not os.path.isfile(RUNS_FILE):
        return []

    with _runs_lock:
        with open(RUNS_FILE, "r", encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip()]

    runs = []
    for line in lines[-max(1, min(limit, 200)) :]:
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(runs))


def _infos_modele() -> dict[str, Any]:
    if tokenizer is None or modele_apex is None:
        return {
            "loaded": False,
            "active_tier": _active_model_tier or "none",
            "default_tier": _normalize_tier(DEFAULT_MODEL_TIER),
            "tiers": {
                tier: {
                    "model_name": MODEL_NAMES[tier],
                    "mode": _model_runtime_state_by_tier.get(tier, "cold"),
                    "error": _model_runtime_error_by_tier.get(tier, ""),
                    "lora_dir": MODEL_LORA_DIRS.get(tier) or "",
                }
                for tier in MODEL_TIERS
            },
            "adapter": "unknown",
            "device": "unknown",
        }

    adapter_charge = bool(MODEL_LORA_DIRS.get(_active_model_tier))
    return {
        "loaded": True,
        "active_tier": _active_model_tier,
        "default_tier": _normalize_tier(DEFAULT_MODEL_TIER),
        "tiers": {
            tier: {
                "model_name": MODEL_NAMES[tier],
                "mode": _model_runtime_state_by_tier.get(tier, "cold"),
                "error": _model_runtime_error_by_tier.get(tier, ""),
                "lora_dir": MODEL_LORA_DIRS.get(tier) or "",
            }
            for tier in MODEL_TIERS
        },
        "adapter": MODEL_LORA_DIRS.get(_active_model_tier, "") if adapter_charge else "base-only",
        "device": str(getattr(modele_apex, "device", "unknown")),
        "error": _model_runtime_error_by_tier.get(_active_model_tier, ""),
    }


# Security: Maximum prompt length to prevent resource exhaustion
MAX_PROMPT_LENGTH = 8000

# Security: Patterns that indicate prompt injection attempts
PROMPT_INJECTION_PATTERNS = [
    "<|assistant|>",  # Model response tag
    "<|system|>",     # System prompt tag (when not expected)
    "IGNORE ABOVE",   # Common injection attempt
    "IGNORE PREVIOUS", # Common injection attempt
    "NEW INSTRUCTION", # Common injection attempt
]


def _validate_prompt(question: str) -> None:
    """
    Validate user prompt for security issues.
    
    Checks:
    - Maximum length (8000 chars)
    - Prompt injection patterns
    - Malicious content
    
    Raises HTTPException(400) if validation fails.
    """
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    
    if len(question) > MAX_PROMPT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Question too long. Maximum length is {MAX_PROMPT_LENGTH} characters. Current: {len(question)}"
        )
    
    # Check for prompt injection patterns (case-insensitive)
    question_upper = question.upper()
    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern in question_upper:
            logger.warning(
                "Prompt injection attempt detected: pattern='%s' in question (truncated: %s...)",
                pattern,
                question[:50]
            )
            raise HTTPException(
                status_code=400,
                detail="Invalid input: detected potential prompt injection attempt"
            )


def _preparer_inputs(question: str) -> tuple[str, Any]:
    assert tokenizer is not None and modele_apex is not None
    
    # Security: Validate prompt before processing
    _validate_prompt(question)
    
    prompt = f"<|user|>\n{question}\n<|assistant|>\n"
    inputs = tokenizer(prompt, return_tensors="pt")
    if hasattr(inputs, "to"):
        inputs = inputs.to(modele_apex.device)
    else:
        inputs = {k: v.to(modele_apex.device) for k, v in inputs.items()}
    return prompt, inputs


async def _generer_reponse_ollama(question: str, mots_max: int, selected_tier: str) -> str:
    """Delegate generation to a local Ollama instance."""
    ollama_model = OLLAMA_MODEL_NAMES[selected_tier]
    
    # Extract system prompt if present
    system_prompt, remaining_prompt = _extraire_systeme_depuis_prompt(question)
    if not system_prompt:
        system_prompt = APEX_DEFAULT_SYSTEM_PROMPT
    
    payload = {
        "model": ollama_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": remaining_prompt},
        ],
        "stream": False,
        "options": {"num_predict": mots_max, "temperature": 0.7, "num_ctx": OLLAMA_NUM_CTX},
    }
    try:
        async with httpx.AsyncClient(timeout=DELAI_GENERATION_SEC) as client:
            r = await client.post(f"{APEX_OLLAMA_URL}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
            message = data.get("message") or {}
            content = (message.get("content") or "").strip()
            if content:
                return content

            # Some models may emit alternate fields when reasoning is enabled.
            for key in ("reasoning_content", "thinking", "reasoning"):
                alt = (message.get(key) or data.get(key) or "").strip()
                if alt:
                    return alt

            return ""
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Temps de génération dépassé (Ollama).") from exc
    except Exception as exc:
        logger.exception("Erreur Ollama pendant la génération")
        raise HTTPException(status_code=500, detail=f"Erreur Ollama: {exc}") from exc


async def _generer_reponse_openai_compat(question: str, mots_max: int, selected_tier: str) -> str:
    """Delegate generation to an OpenAI-compatible server (e.g. LM Studio, vLLM)."""
    model_name = OPENAI_COMPAT_MODEL_NAMES[selected_tier]
    system_prompt, user_text = _extraire_systeme_depuis_prompt(question)
    if not system_prompt:
        system_prompt = APEX_DEFAULT_SYSTEM_PROMPT

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": mots_max,
        "temperature": 0.7,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {APEX_OPENAI_COMPAT_API_KEY}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=DELAI_GENERATION_SEC) as client:
            r = await client.post(f"{APEX_OPENAI_COMPAT_URL}/v1/chat/completions", json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}

            content = (message.get("content") or "").strip()
            if content:
                return content

            # Some backends expose hidden reasoning under alternate fields.
            for key in ("reasoning_content", "reasoning", "thinking"):
                alt = (message.get(key) or choice.get(key) or data.get(key) or "").strip()
                if alt:
                    return alt

            # Legacy/non-chat fallback.
            text = (choice.get("text") or "").strip()
            if text:
                return text

            return ""
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Temps de génération dépassé (OpenAI-compat).") from exc
    except Exception as exc:
        logger.exception("Erreur OpenAI-compat pendant la génération")
        raise HTTPException(status_code=500, detail=f"Erreur OpenAI-compat: {exc}") from exc


async def _streamer_tokens_openai_compat(question: str, mots_max: int, selected_tier: str) -> Any:
    """Stream tokens from an OpenAI-compatible server."""
    model_name = OPENAI_COMPAT_MODEL_NAMES[selected_tier]
    system_prompt, user_text = _extraire_systeme_depuis_prompt(question)
    if not system_prompt:
        system_prompt = APEX_DEFAULT_SYSTEM_PROMPT

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": mots_max,
        "temperature": 0.7,
        "stream": True,
    }
    headers = {"Authorization": f"Bearer {APEX_OPENAI_COMPAT_API_KEY}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=DELAI_GENERATION_SEC) as client:
            async with client.stream("POST", f"{APEX_OPENAI_COMPAT_URL}/v1/chat/completions", json=payload, headers=headers) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    raw = line[len("data: "):].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                        delta = chunk["choices"][0].get("delta", {})
                        fragment = (
                            delta.get("content")
                            or delta.get("reasoning_content")
                            or delta.get("reasoning")
                            or delta.get("thinking")
                            or ""
                        )
                        if fragment:
                            yield fragment, selected_tier
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Temps de génération dépassé (OpenAI-compat stream).") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur OpenAI-compat stream: {exc}") from exc


async def _generer_reponse(question: str, mots_max: int, model_tier: str | None = None) -> tuple[str, str]:
    selected_tier = _normalize_tier(model_tier)

    if _openai_compat_active():
        reponse = await _generer_reponse_openai_compat(question, mots_max, selected_tier)
        _model_runtime_state_by_tier[selected_tier] = "ready"
        return reponse, selected_tier

    if _ollama_active():
        reponse = await _generer_reponse_ollama(question, mots_max, selected_tier)
        _model_runtime_state_by_tier[selected_tier] = "ready"
        return reponse, selected_tier

    selected_tier = _charger_modele_runtime(model_tier)
    assert tokenizer is not None and modele_apex is not None

    prompt, inputs = _preparer_inputs(question)

    try:
        outputs = await asyncio.wait_for(
            asyncio.to_thread(
                lambda: cast(Any, modele_apex).generate(
                    **inputs,
                    max_new_tokens=mots_max,
                    temperature=0.7,
                    do_sample=True,
                )
            ),
            timeout=DELAI_GENERATION_SEC,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Temps de génération dépassé.") from exc
    except Exception as exc:
        logger.exception("Erreur interne pendant la génération")
        raise HTTPException(status_code=500, detail="Erreur interne de génération.") from exc

    reponse_brute = tokenizer.decode(outputs[0], skip_special_tokens=True)
    reponse_finale = reponse_brute.split("<|assistant|>")[-1].strip()
    if not reponse_finale and prompt in reponse_brute:
        reponse_finale = reponse_brute.replace(prompt, "").strip()
    return reponse_finale, selected_tier


async def _streamer_tokens(question: str, mots_max: int, model_tier: str | None = None) -> Any:
    selected_tier = _normalize_tier(model_tier)

    if _openai_compat_active():
        async for fragment, tier in _streamer_tokens_openai_compat(question, mots_max, selected_tier):
            yield fragment, tier
        return

    if _ollama_active():
        # Ollama streaming: use /api/chat with messages for better compatibility.
        ollama_model = OLLAMA_MODEL_NAMES[selected_tier]
        
        # Extract system prompt if present
        system_prompt, remaining_prompt = _extraire_systeme_depuis_prompt(question)
        if not system_prompt:
            system_prompt = APEX_DEFAULT_SYSTEM_PROMPT
        
        payload = {
            "model": ollama_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": remaining_prompt},
            ],
            "stream": True,
            "options": {"num_predict": mots_max, "temperature": 0.7, "num_ctx": OLLAMA_NUM_CTX},
        }
        try:
            async with httpx.AsyncClient(timeout=DELAI_GENERATION_SEC) as client:
                async with client.stream("POST", f"{APEX_OLLAMA_URL}/api/chat", json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                            message = chunk.get("message") or {}
                            fragment = (
                                message.get("content")
                                or message.get("reasoning_content")
                                or message.get("thinking")
                                or message.get("reasoning")
                                or ""
                            )
                            if fragment:
                                yield fragment, selected_tier
                            if chunk.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="Temps de génération dépassé (Ollama).") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Erreur Ollama stream: {exc}") from exc
        return

    selected_tier = _charger_modele_runtime(model_tier)
    assert tokenizer is not None and modele_apex is not None

    _, inputs = _preparer_inputs(question)

    # Fake mode for tests: deterministic short stream.
    if isinstance(tokenizer, _FakeTokenizer):
        for fragment in ["reponse ", "de ", "test "]:
            yield fragment, selected_tier
            await asyncio.sleep(0.01)
        return

    streamer = TextIteratorStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)
    generation_error: dict[str, str] = {}

    def generation_job() -> None:
        try:
            cast(Any, modele_apex).generate(
                **inputs,
                max_new_tokens=mots_max,
                temperature=0.7,
                do_sample=True,
                streamer=streamer,
            )
        except Exception as exc:
            generation_error["detail"] = str(exc)
            logger.exception("Erreur interne pendant la génération stream")

    worker = Thread(target=generation_job, daemon=True)
    worker.start()

    iterator = iter(streamer)
    while True:
        try:
            fragment = await asyncio.wait_for(
                asyncio.to_thread(next, iterator),
                timeout=DELAI_GENERATION_SEC,
            )
            yield fragment, selected_tier
        except StopIteration:
            break
        except asyncio.TimeoutError as exc:
            raise HTTPException(status_code=504, detail="Temps de génération dépassé.") from exc

    worker.join(timeout=0.1)

    if generation_error.get("detail"):
        raise HTTPException(status_code=500, detail="Erreur interne de génération.")

# ==========================================
# 3. LE MENU (Format de la requête)
# ==========================================
class RequeteClient(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    mots_max: int = Field(default=50, ge=1, le=500)
    task_type: str = Field(default="default", max_length=64)
    model_tier: Literal["fast", "default", "reasoning"] = "default"


class MessageV2(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


class ContextChunkV2(BaseModel):
    source: str = Field(
        ...,
        min_length=1,
        max_length=512,
        pattern=r"^[a-zA-Z0-9_./-]+$",  # Prevent path traversal in source
        description="Source file path (alphanumeric, dots, slashes, hyphens only)"
    )
    content: str = Field(
        ...,
        min_length=1,
        max_length=3000,
        description="Context content (max 3000 chars)"
    )
    score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Relevance score (0-1)"
    )
    
    def validate_content(self) -> None:
        """Additional content validation."""
        # Check for prompt injection in context
        if "<|assistant|>" in self.content.upper():
            raise ValueError("Context contains invalid model tags")


class ToolSpecV2(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)
    input_schema: dict[str, Any] = Field(default_factory=dict)


class RequeteClientV2(BaseModel):
    messages: list[MessageV2] = Field(..., min_length=1, max_length=30)
    mots_max: int = Field(default=120, ge=1, le=500)
    task_type: str = Field(default="default", max_length=64)
    model_tier: Literal["fast", "default", "reasoning"] = "default"
    context_chunks: list[ContextChunkV2] = Field(default_factory=list, max_length=8)
    tools: list[ToolSpecV2] = Field(default_factory=list, max_length=12)
    tool_choice: Literal["auto", "none"] = "auto"


class OllamaGenerateRequest(BaseModel):
    model: str = "apex:default"
    prompt: str = ""
    system: str | None = None
    template: str | None = None
    context: list[int] | None = None
    stream: bool = False
    raw: bool = False
    format: str | None = None
    keep_alive: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


class OllamaMessage(BaseModel):
    role: str = "user"
    content: Any = ""          # str OR list[{type, text}] for multimodal
    images: list[str] | None = None
    tool_calls: list[dict[str, Any]] | None = None

    model_config = {"extra": "ignore"}


class OllamaChatRequest(BaseModel):
    model: str = "apex:default"
    messages: list[OllamaMessage] = Field(default_factory=list)
    tools: list[dict[str, Any]] | None = None   # tool definitions from client
    system: str | None = None                   # optional system prompt override
    stream: bool = False
    format: str | None = None
    keep_alive: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


class OpenAIChatMessage(BaseModel):
    role: str = "user"
    content: Any = ""
    name: str | None = None
    tool_call_id: str | None = None

    model_config = {"extra": "ignore"}


class OpenAIChatRequest(BaseModel):
    model: str = "apex:default"
    messages: list[OpenAIChatMessage] = Field(default_factory=list)
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None

    model_config = {"extra": "ignore"}


class AnthropicMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: Any

    model_config = {"extra": "ignore"}


class AnthropicMessagesRequest(BaseModel):
    model: str = "apex:default"
    max_tokens: int = 512
    system: str | list[dict[str, Any]] | None = None
    messages: list[AnthropicMessage] = Field(default_factory=list)
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None

    model_config = {"extra": "ignore"}


def _content_vers_texte(raw_content: Any) -> str:
    if isinstance(raw_content, str):
        return raw_content.strip()
    if isinstance(raw_content, list):
        parts: list[str] = []
        for part in raw_content:
            if isinstance(part, dict):
                text = part.get("text") or part.get("content") or part.get("value") or ""
                if text:
                    parts.append(str(text))
            elif part is not None:
                parts.append(str(part))
        return " ".join(parts).strip()
    if isinstance(raw_content, dict):
        text = raw_content.get("text") or raw_content.get("content") or raw_content.get("value") or ""
        return str(text).strip()
    if raw_content is None:
        return ""
    return str(raw_content).strip()


def _normaliser_max_tokens(max_tokens: int | None, fallback: int = 512) -> int:
    raw_value = fallback if max_tokens is None else max_tokens
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = fallback
    return max(1, min(value, 4096))


def _prompt_depuis_openai(payload: OpenAIChatRequest) -> str:
    system_prompt = ""
    converted: list[dict[str, Any]] = []

    for message in payload.messages:
        role = (message.role or "user").strip().lower()
        text = _content_vers_texte(message.content)
        if not text:
            continue
        if role == "system":
            system_prompt = text
            continue
        if role not in {"user", "assistant", "tool"}:
            role = "user"
        converted.append({"role": role, "content": text})

    prompt = _messages_vers_prompt(converted, system=system_prompt or None)
    tools_hint = _tools_vers_prompt(payload.tools)
    if tools_hint:
        prompt = f"{tools_hint}\n\n{prompt}" if prompt else tools_hint
    return prompt


def _normaliser_tool_choice_openai(tool_choice: str | dict[str, Any] | None) -> tuple[str, str | None]:
    if tool_choice is None:
        return "auto", None

    if isinstance(tool_choice, str):
        choice = tool_choice.strip().lower()
        if choice in {"none", "auto", "required"}:
            return choice, None
        return "auto", None

    if not isinstance(tool_choice, dict):
        return "auto", None

    if str(tool_choice.get("type", "")).strip().lower() == "function":
        function = tool_choice.get("function", {})
        if isinstance(function, dict):
            forced_name = str(function.get("name", "")).strip()
            if forced_name:
                return "required", forced_name

    return "auto", None


def _detect_tool_calls_openai(payload: OpenAIChatRequest) -> list[dict[str, Any]]:
    if not payload.tools:
        return []

    mode, forced_name = _normaliser_tool_choice_openai(payload.tool_choice)
    if mode == "none":
        return []

    user_text_parts: list[str] = []
    for message in payload.messages:
        role = (message.role or "").strip().lower()
        if role != "user":
            continue
        text = _content_vers_texte(message.content)
        if text:
            user_text_parts.append(text.lower())
    user_text = "\n".join(user_text_parts)

    def _extract_name(tool: dict[str, Any]) -> str:
        fn = tool.get("function", tool)
        if isinstance(fn, dict):
            return str(fn.get("name", "")).strip()
        return ""

    declared_names = {
        name
        for name in (_extract_name(tool) for tool in payload.tools if isinstance(tool, dict))
        if name
    }
    if forced_name and forced_name not in declared_names:
        forced_name = None
        mode = "auto"

    selected: list[str] = []
    if forced_name and forced_name in declared_names:
        selected = [forced_name]
    else:
        for tool in payload.tools:
            if not isinstance(tool, dict):
                continue
            name = _extract_name(tool)
            if not name:
                continue
            if mode == "required":
                selected.append(name)
                continue
            name_parts = [p for p in name.lower().replace("-", "_").split("_") if p]
            if name.lower() in user_text or any(p in user_text for p in name_parts):
                selected.append(name)

    # Keep responses deterministic and bounded.
    unique_names = list(dict.fromkeys(selected))[:3]
    tool_calls: list[dict[str, Any]] = []
    for name in unique_names:
        tool_calls.append(
            {
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": "{}",
                },
            }
        )
    return tool_calls


def _detect_tool_calls_ollama(payload: OllamaChatRequest) -> list[dict[str, Any]]:
    if not payload.tools:
        return []

    user_text_parts: list[str] = []
    for message in payload.messages:
        role = (message.role or "").strip().lower()
        if role != "user":
            continue
        text = _content_vers_texte(message.content)
        if text:
            user_text_parts.append(text.lower())
    user_text = "\n".join(user_text_parts)

    selected: list[str] = []
    for tool in payload.tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function", tool)
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name", "")).strip()
        if not name:
            continue
        name_parts = [p for p in name.lower().replace("-", "_").split("_") if p]
        if name.lower() in user_text or any(p in user_text for p in name_parts):
            selected.append(name)

    unique_names = list(dict.fromkeys(selected))[:3]
    tool_calls: list[dict[str, Any]] = []
    for name in unique_names:
        tool_calls.append(
            {
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": {},
                },
            }
        )
    return tool_calls


def _anthropic_system_vers_texte(system: str | list[dict[str, Any]] | None) -> str:
    if isinstance(system, str):
        return system.strip()
    if isinstance(system, list):
        parts: list[str] = []
        for block in system:
            if isinstance(block, dict):
                text = block.get("text") or block.get("content") or ""
                if text:
                    parts.append(str(text))
        return " ".join(parts).strip()
    return ""


def _prompt_depuis_anthropic(payload: AnthropicMessagesRequest) -> str:
    system_prompt = _anthropic_system_vers_texte(payload.system)
    converted: list[dict[str, Any]] = []

    for message in payload.messages:
        role = (message.role or "user").strip().lower()
        text = _content_vers_texte(message.content)
        if not text:
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        converted.append({"role": role, "content": text})

    prompt = _messages_vers_prompt(converted, system=system_prompt or None)
    tools_hint = _tools_vers_prompt(payload.tools)
    if tools_hint:
        prompt = f"{tools_hint}\n\n{prompt}" if prompt else tools_hint
    return prompt


def _build_prompt_v2(requete: RequeteClientV2) -> str:
    lines: list[str] = []

    if requete.context_chunks:
        lines.append("[CONTEXT]")
        for chunk in requete.context_chunks:
            score_txt = f" score={chunk.score:.3f}" if chunk.score is not None else ""
            lines.append(f"- source={chunk.source}{score_txt}")
            lines.append(chunk.content)
        lines.append("[/CONTEXT]")

    if requete.tools:
        lines.append("[TOOLS]")
        for tool in requete.tools:
            lines.append(f"- {tool.name}: {tool.description}")
        lines.append("[/TOOLS]")

    for message in requete.messages:
        lines.append(f"<{message.role}>\n{message.content}\n</{message.role}>")

    return "\n".join(lines)


def _detect_tool_calls(requete: RequeteClientV2) -> list[dict[str, Any]]:
    if requete.tool_choice == "none" or not requete.tools:
        return []

    question = " ".join([m.content.lower() for m in requete.messages if m.role == "user"])
    calls: list[dict[str, Any]] = []
    for tool in requete.tools:
        if tool.name.lower() in question:
            calls.append(
                {
                    "name": tool.name,
                    "arguments": {},
                    "reason": "tool name matched in user message",
                }
            )
    return calls[:3]

# ==========================================
# 4. LA ROUTE DE L'API (L'Endpoint de discussion)
# ==========================================
@app.post("/chat")
async def discuter_avec_ia(requete: RequeteClient, request: Request, cle_api: str = Security(header_cle)):
    request_id = getattr(request.state, "request_id", "unknown")
    key_info = key_store.verify_key(cle_api.strip())
    if key_info is None:
        raise HTTPException(status_code=403, detail="Accès refusé : Clé API invalide !")

    try:
        key_store.check_quota(key_info.key_hash)
    except key_store.QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    # ── Freemium tier quota check ──────────────────────────────────────────────────────────
    # Estimate token usage (input + output)
    tokens_estimated = len(requete.question.split()) + requete.mots_max
    quota_check = _check_freemium_quota(key_info.key_hash, key_info.plan, tokens_estimated)
    if not quota_check["allowed"]:
        _log_event(
            "freemium_quota_exceeded",
            request_id=request_id,
            plan=key_info.plan,
            reason=quota_check["reason"],
            usage=quota_check["usage"],
        )
        raise HTTPException(
            status_code=429,
            detail=f"Quota dépassé pour le plan {key_info.plan}: {quota_check['reason']}. "
                   f"Upgrade ou attendez demain pour un renouvellement.",
        )

    ip_client = request.client.host if request.client and request.client.host else "unknown"
    _verifier_rate_limit(key_info.key_hash, ip_client)

    run_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    try:
        reponse_finale, selected_tier = await _generer_reponse(requete.question, requete.mots_max, requete.model_tier)
    except HTTPException as exc:
        _append_run(
            {
                "id": run_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "error",
                "http_status": exc.status_code,
                "question": requete.question,
                "mots_max": requete.mots_max,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "error": exc.detail,
                "plan": key_info.plan,
                "model_tier": requete.model_tier,
            }
        )
        raise

    key_store.record_usage(
        key_info.key_hash,
        tokens_used=requete.mots_max,
        endpoint="/chat",
        latency_ms=int((time.perf_counter() - t0) * 1000),
        task_type=requete.task_type,
    )
    
    # Record freemium tier usage
    tokens_final = len(requete.question.split()) + requete.mots_max
    _record_freemium_usage(key_info.key_hash, tokens_final)
    _append_run(
        {
            "id": run_id,
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "success",
            "http_status": 200,
            "question": requete.question,
            "mots_max": requete.mots_max,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "response_preview": reponse_finale[:300],
            "plan": key_info.plan,
            "label": key_info.label,
            "model_tier": selected_tier,
        }
    )

    return {
        "run_id": run_id,
        "request_id": request_id,
        "status": "succes",
        "question": requete.question,
        "reponse_apex": reponse_finale,
        "model_tier": selected_tier,
    }


@app.post("/chat/v2")
async def discuter_avec_ia_v2(requete: RequeteClientV2, request: Request, cle_api: str = Security(header_cle)) -> dict[str, Any]:
    request_id = getattr(request.state, "request_id", "unknown")
    key_info = key_store.verify_key(cle_api.strip())
    if key_info is None:
        raise HTTPException(status_code=403, detail="Accès refusé : Clé API invalide !")

    try:
        key_store.check_quota(key_info.key_hash)
    except key_store.QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    ip_client = request.client.host if request.client and request.client.host else "unknown"
    _verifier_rate_limit(key_info.key_hash, ip_client)

    run_id = str(uuid.uuid4())
    t0 = time.perf_counter()
    prompt_v2 = _build_prompt_v2(requete)
    tool_calls = _detect_tool_calls(requete)

    try:
        reponse_finale, selected_tier = await _generer_reponse(prompt_v2, requete.mots_max, requete.model_tier)
    except HTTPException as exc:
        _append_run(
            {
                "id": run_id,
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "error",
                "http_status": exc.status_code,
                "question": requete.messages[-1].content,
                "mots_max": requete.mots_max,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "error": exc.detail,
                "plan": key_info.plan,
                "task_type": requete.task_type,
                "model_tier": requete.model_tier,
                "v2": True,
            }
        )
        raise

    key_store.record_usage(
        key_info.key_hash,
        tokens_used=requete.mots_max,
        endpoint="/chat/v2",
        latency_ms=int((time.perf_counter() - t0) * 1000),
        task_type=requete.task_type,
    )

    citations = [chunk.source for chunk in requete.context_chunks]
    _append_run(
        {
            "id": run_id,
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "success",
            "http_status": 200,
            "question": requete.messages[-1].content,
            "mots_max": requete.mots_max,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "response_preview": reponse_finale[:300],
            "plan": key_info.plan,
            "label": key_info.label,
            "task_type": requete.task_type,
            "model_tier": selected_tier,
            "tool_calls": tool_calls,
            "citations": citations,
            "v2": True,
        }
    )

    return {
        "run_id": run_id,
        "request_id": request_id,
        "status": "succes",
        "reponse_apex": reponse_finale,
        "task_type": requete.task_type,
        "model_tier": selected_tier,
        "tool_calls": tool_calls,
        "citations": citations,
    }


@app.post("/chat/stream")
async def discuter_avec_ia_stream(requete: RequeteClient, request: Request, cle_api: str = Security(header_cle)) -> StreamingResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    key_info = key_store.verify_key(cle_api.strip())
    if key_info is None:
        raise HTTPException(status_code=403, detail="Accès refusé : Clé API invalide !")

    try:
        key_store.check_quota(key_info.key_hash)
    except key_store.QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    ip_client = request.client.host if request.client and request.client.host else "unknown"
    _verifier_rate_limit(key_info.key_hash, ip_client)
    run_id = str(uuid.uuid4())

    async def event_stream() -> Any:
        t0 = time.perf_counter()
        morceaux: list[str] = []
        selected_tier = _normalize_tier(requete.model_tier)
        try:
            yield f"data: {json.dumps({'type': 'status', 'value': 'loading', 'run_id': run_id, 'request_id': request_id, 'model_tier': selected_tier})}\n\n"
            async for fragment, selected_tier in _streamer_tokens(requete.question, requete.mots_max, requete.model_tier):
                morceaux.append(fragment)
                yield f"data: {json.dumps({'type': 'delta', 'value': fragment, 'run_id': run_id, 'request_id': request_id, 'model_tier': selected_tier})}\n\n"
            key_store.record_usage(
                key_info.key_hash,
                tokens_used=requete.mots_max,
                endpoint="/chat/stream",
                latency_ms=int((time.perf_counter() - t0) * 1000),
                task_type=requete.task_type,
            )
            _append_run(
                {
                    "id": run_id,
                    "request_id": request_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": "success",
                    "http_status": 200,
                    "question": requete.question,
                    "mots_max": requete.mots_max,
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "response_preview": "".join(morceaux)[:300],
                    "stream": True,
                    "plan": key_info.plan,
                    "label": key_info.label,
                    "model_tier": selected_tier,
                }
            )
            yield f"data: {json.dumps({'type': 'done', 'run_id': run_id, 'request_id': request_id, 'model_tier': selected_tier})}\n\n"
        except HTTPException as exc:
            _append_run(
                {
                    "id": run_id,
                    "request_id": request_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": "error",
                    "http_status": exc.status_code,
                    "question": requete.question,
                    "mots_max": requete.mots_max,
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "error": exc.detail,
                    "stream": True,
                    "model_tier": _normalize_tier(requete.model_tier),
                }
            )
            yield f"data: {json.dumps({'type': 'error', 'status': exc.status_code, 'value': exc.detail, 'request_id': request_id, 'model_tier': _normalize_tier(requete.model_tier)})}\n\n"
        except Exception as exc:
            _append_run(
                {
                    "id": run_id,
                    "request_id": request_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": "error",
                    "http_status": 500,
                    "question": requete.question,
                    "mots_max": requete.mots_max,
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "error": str(exc),
                    "stream": True,
                    "model_tier": _normalize_tier(requete.model_tier),
                }
            )
            logger.exception("Erreur interne pendant le streaming")
            yield f"data: {json.dumps({'type': 'error', 'status': 500, 'value': str(exc), 'request_id': request_id, 'model_tier': _normalize_tier(requete.model_tier)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
async def api_status() -> dict[str, Any]:
    model_info = _infos_modele()
    return {
        "service": "apex-llm",
        "health": "ok",
        "model": model_info,
        "limits": {
            "requests_per_window": LIMITE_REQUETES,
            "window_seconds": FENETRE_REQUETES_SEC,
            "generation_timeout_seconds": DELAI_GENERATION_SEC,
            "max_tokens": 500,
        },
    }


@app.get("/api/runs")
async def api_runs(limit: int = 20) -> dict[str, Any]:
    return {
        "runs": _charger_runs(limit=limit),
    }


@app.get("/api/usage")
async def api_usage(
    request: Request,
    days: int = 30,
    cle_api: str = Security(header_cle),
) -> dict[str, Any]:
    """Return billing-ready usage data for the authenticated key."""
    key_info = key_store.verify_key(cle_api.strip())
    if key_info is None:
        raise HTTPException(status_code=403, detail="Accès refusé : Clé API invalide !")

    summary = key_store.get_usage_summary(key_info.key_hash, days=days)
    return summary


@app.get("/api/tools")
async def api_tools() -> dict[str, Any]:
    return {
        "model_routing": {
            "supported": True,
            "tiers": list(MODEL_TIERS),
            "default_tier": _normalize_tier(DEFAULT_MODEL_TIER),
            "request_field": "model_tier",
        },
        "tool_calling": {
            "supported": True,
            "mode": "client-orchestrated",
            "route": "/chat/v2",
            "tool_choice": ["auto", "none"],
        },
        "retrieval": {
            "supported": True,
            "input_field": "context_chunks",
            "max_chunks": 8,
        },
    }


@app.get("/api/version")
async def ollama_compat_version(request: Request) -> dict[str, str]:
    _verifier_cle_api_compat(request)
    return {"version": "0.3.12"}


@app.get("/v1/models")
async def openai_compat_models(request: Request) -> dict[str, Any]:
    _verifier_cle_api_compat(request)
    created = int(time.time())
    data: list[dict[str, Any]] = []
    for tier in MODEL_TIERS:
        data.append(
            {
                "id": f"apex:{tier}",
                "object": "model",
                "created": created,
                "owned_by": "apex",
            }
        )
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
async def openai_compat_chat_completions(payload: OpenAIChatRequest, request: Request) -> Any:
    _verifier_cle_api_compat(request)
    prompt = _prompt_depuis_openai(payload)
    if not prompt:
        raise HTTPException(status_code=422, detail="messages est vide pour /v1/chat/completions")

    selected_tier = _tier_from_ollama_model(payload.model)
    mots_max = _normaliser_max_tokens(payload.max_tokens)
    chat_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    tool_calls = _detect_tool_calls_openai(payload)

    if not payload.stream:
        if tool_calls:
            return {
                "id": chat_id,
                "object": "chat.completion",
                "created": created,
                "model": f"apex:{selected_tier}",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": tool_calls,
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": len(prompt.split()),
                    "completion_tokens": 0,
                    "total_tokens": len(prompt.split()),
                },
            }

        reponse, selected_tier = await _generer_reponse(prompt, mots_max, selected_tier)
        return {
            "id": chat_id,
            "object": "chat.completion",
            "created": created,
            "model": f"apex:{selected_tier}",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": reponse},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(prompt.split()),
                "completion_tokens": len(reponse.split()),
                "total_tokens": len(prompt.split()) + len(reponse.split()),
            },
        }

    async def stream_openai() -> AsyncIterator[str]:
        total_completion_tokens = 0
        try:
            if tool_calls:
                first_chunk = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": f"apex:{selected_tier}",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "tool_calls": tool_calls,
                            },
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(first_chunk, ensure_ascii=False)}\n\n"

                final_tool_chunk = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": f"apex:{selected_tier}",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                    "usage": {
                        "prompt_tokens": len(prompt.split()),
                        "completion_tokens": 0,
                        "total_tokens": len(prompt.split()),
                    },
                }
                yield f"data: {json.dumps(final_tool_chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                return

            async for fragment, streamed_tier in _streamer_tokens(prompt, mots_max, selected_tier):
                total_completion_tokens += len(fragment.split())
                chunk = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": f"apex:{streamed_tier}",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": fragment},
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

            final_chunk = {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": f"apex:{selected_tier}",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": len(prompt.split()),
                    "completion_tokens": total_completion_tokens,
                    "total_tokens": len(prompt.split()) + total_completion_tokens,
                },
            }
            yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except HTTPException as exc:
            error_chunk = {
                "error": {
                    "message": str(exc.detail),
                    "type": "api_error",
                    "code": exc.status_code,
                }
            }
            yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(stream_openai(), media_type="text/event-stream")


@app.post("/v1/messages")
async def anthropic_compat_messages(payload: AnthropicMessagesRequest, request: Request) -> Any:
    _verifier_cle_api_compat(request)
    prompt = _prompt_depuis_anthropic(payload)
    if not prompt:
        raise HTTPException(status_code=422, detail="messages est vide pour /v1/messages")

    selected_tier = _tier_from_ollama_model(payload.model)
    mots_max = _normaliser_max_tokens(payload.max_tokens)
    message_id = f"msg_{uuid.uuid4().hex}"

    if not payload.stream:
        reponse, selected_tier = await _generer_reponse(prompt, mots_max, selected_tier)
        return {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": reponse}],
            "model": f"apex:{selected_tier}",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": len(prompt.split()),
                "output_tokens": len(reponse.split()),
            },
        }

    async def stream_anthropic() -> AsyncIterator[str]:
        output_tokens = 0
        started = {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": f"apex:{selected_tier}",
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": len(prompt.split()), "output_tokens": 0},
            },
        }
        yield f"event: message_start\ndata: {json.dumps(started, ensure_ascii=False)}\n\n"
        yield "event: content_block_start\ndata: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"text\",\"text\":\"\"}}\n\n"

        try:
            async for fragment, _ in _streamer_tokens(prompt, mots_max, selected_tier):
                output_tokens += len(fragment.split())
                delta = {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": fragment},
                }
                yield f"event: content_block_delta\ndata: {json.dumps(delta, ensure_ascii=False)}\n\n"
        except HTTPException as exc:
            err = {"type": "error", "error": {"type": "api_error", "message": str(exc.detail)}}
            yield f"event: error\ndata: {json.dumps(err, ensure_ascii=False)}\n\n"
            return

        yield "event: content_block_stop\ndata: {\"type\":\"content_block_stop\",\"index\":0}\n\n"
        message_delta = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        }
        yield f"event: message_delta\ndata: {json.dumps(message_delta, ensure_ascii=False)}\n\n"
        yield "event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"

    return StreamingResponse(stream_anthropic(), media_type="text/event-stream")


@app.get("/api/tags")
async def ollama_compat_tags(request: Request) -> dict[str, Any]:
    _verifier_cle_api_compat(request)
    now_iso = datetime.now(timezone.utc).isoformat()
    models: list[dict[str, Any]] = []
    for tier in MODEL_TIERS:
        model_name = f"apex:{tier}"
        base_name = MODEL_NAMES.get(tier, DEFAULT_MODEL_NAME)
        digest = uuid.uuid5(uuid.NAMESPACE_DNS, f"apex-{tier}-{base_name}").hex
        models.append(
            {
                "name": model_name,
                "model": model_name,
                "capabilities": ["completion", "tools"],
                "modified_at": now_iso,
                "size": 0,
                "digest": digest,
                "details": {
                    "format": "gguf",
                    "family": "apex",
                    "families": ["apex"],
                    "parameter_size": "dynamic",
                    "quantization_level": "mixed",
                },
            }
        )
    return {"models": models}


@app.post("/api/generate")
async def ollama_compat_generate(payload: OllamaGenerateRequest, request: Request) -> Any:
    _verifier_cle_api_compat(request)
    selected_tier = _tier_from_ollama_model(payload.model)
    base_prompt = _extraire_question_depuis_prompt(payload.prompt)
    # Prepend system prompt (use default if not provided)
    effective_system = payload.system if payload.system else APEX_DEFAULT_SYSTEM_PROMPT
    question = f"{effective_system.strip()}\n\n{base_prompt}" if effective_system else base_prompt
    mots_max = _mots_max_depuis_options(payload.options)
    created_at = datetime.now(timezone.utc).isoformat()
    t0_ns = time.perf_counter_ns()

    if not payload.stream:
        reponse, selected_tier = await _generer_reponse(question, mots_max, selected_tier)
        total_ns = time.perf_counter_ns() - t0_ns
        return {
            "model": f"apex:{selected_tier}",
            "created_at": created_at,
            "response": reponse,
            "done": True,
            "done_reason": "stop",
            "context": [],
            "total_duration": total_ns,
            "load_duration": 0,
            "prompt_eval_count": len(question.split()),
            "eval_count": len(reponse.split()),
            "eval_duration": total_ns,
        }

    async def stream_generate() -> AsyncIterator[str]:
        eval_count = 0
        try:
            async for fragment, streamed_tier in _streamer_tokens(question, mots_max, selected_tier):
                eval_count += len(fragment.split())
                yield _ndjson(
                    {
                        "model": f"apex:{streamed_tier}",
                        "created_at": created_at,
                        "response": fragment,
                        "done": False,
                    }
                )
            total_ns = time.perf_counter_ns() - t0_ns
            yield _ndjson(
                {
                    "model": f"apex:{selected_tier}",
                    "created_at": created_at,
                    "response": "",
                    "done": True,
                    "done_reason": "stop",
                    "total_duration": total_ns,
                    "load_duration": 0,
                    "prompt_eval_count": len(question.split()),
                    "eval_count": eval_count,
                    "eval_duration": total_ns,
                }
            )
        except HTTPException as exc:
            yield _ndjson(
                {
                    "model": f"apex:{selected_tier}",
                    "created_at": created_at,
                    "response": "",
                    "done": True,
                    "error": str(exc.detail),
                }
            )

    return StreamingResponse(stream_generate(), media_type="application/x-ndjson")


@app.post("/api/chat")
async def ollama_compat_chat(payload: OllamaChatRequest, request: Request) -> Any:
    _verifier_cle_api_compat(request)
    raw_msgs = [m.model_dump() for m in payload.messages]
    selected_tier = _tier_from_ollama_model(payload.model)
    prompt = _messages_vers_prompt(raw_msgs, system=payload.system)
    tools_hint = _tools_vers_prompt(payload.tools)
    if tools_hint:
        prompt = f"{tools_hint}\n\n{prompt}" if prompt else tools_hint
    if not prompt:
        raise HTTPException(status_code=422, detail="messages est vide pour /api/chat compatible Ollama.")
    mots_max = _mots_max_depuis_options(payload.options)
    created_at = datetime.now(timezone.utc).isoformat()
    t0_ns = time.perf_counter_ns()
    tool_calls = _detect_tool_calls_ollama(payload)

    if not payload.stream:
        if tool_calls:
            total_ns = time.perf_counter_ns() - t0_ns
            return {
                "model": f"apex:{selected_tier}",
                "created_at": created_at,
                "message": {"role": "assistant", "content": "", "tool_calls": tool_calls},
                "done": True,
                "done_reason": "tool_calls",
                "total_duration": total_ns,
                "load_duration": 0,
                "prompt_eval_count": len(prompt.split()),
                "eval_count": 0,
                "eval_duration": total_ns,
            }

        reponse, selected_tier = await _generer_reponse(prompt, mots_max, selected_tier)
        total_ns = time.perf_counter_ns() - t0_ns
        return {
            "model": f"apex:{selected_tier}",
            "created_at": created_at,
            "message": {"role": "assistant", "content": reponse},
            "done": True,
            "done_reason": "stop",
            "total_duration": total_ns,
            "load_duration": 0,
            "prompt_eval_count": len(prompt.split()),
            "eval_count": len(reponse.split()),
            "eval_duration": total_ns,
        }

    async def stream_chat() -> AsyncIterator[str]:
        eval_count = 0
        try:
            if tool_calls:
                total_ns = time.perf_counter_ns() - t0_ns
                yield _ndjson(
                    {
                        "model": f"apex:{selected_tier}",
                        "created_at": created_at,
                        "message": {"role": "assistant", "content": "", "tool_calls": tool_calls},
                        "done": True,
                        "done_reason": "tool_calls",
                        "total_duration": total_ns,
                        "load_duration": 0,
                        "prompt_eval_count": len(prompt.split()),
                        "eval_count": 0,
                        "eval_duration": total_ns,
                    }
                )
                return

            async for fragment, streamed_tier in _streamer_tokens(prompt, mots_max, selected_tier):
                eval_count += len(fragment.split())
                yield _ndjson(
                    {
                        "model": f"apex:{streamed_tier}",
                        "created_at": created_at,
                        "message": {"role": "assistant", "content": fragment},
                        "done": False,
                    }
                )
            total_ns = time.perf_counter_ns() - t0_ns
            yield _ndjson(
                {
                    "model": f"apex:{selected_tier}",
                    "created_at": created_at,
                    "message": {"role": "assistant", "content": ""},
                    "done": True,
                    "done_reason": "stop",
                    "total_duration": total_ns,
                    "load_duration": 0,
                    "prompt_eval_count": len(prompt.split()),
                    "eval_count": eval_count,
                    "eval_duration": total_ns,
                }
            )
        except HTTPException as exc:
            yield _ndjson(
                {
                    "model": f"apex:{selected_tier}",
                    "created_at": created_at,
                    "message": {"role": "assistant", "content": ""},
                    "done": True,
                    "error": str(exc.detail),
                }
            )

    return StreamingResponse(stream_chat(), media_type="application/x-ndjson")


class _OllamaShowRequest(BaseModel):
    name: str = "apex:default"
    model: str | None = None
    model_config = {"extra": "ignore"}


@app.post("/api/show")
async def ollama_compat_show(payload: _OllamaShowRequest, request: Request) -> dict[str, Any]:
    _verifier_cle_api_compat(request)
    model_name = payload.model or payload.name
    tier = _tier_from_ollama_model(model_name)
    base_name = MODEL_NAMES.get(tier, DEFAULT_MODEL_NAME)
    digest = uuid.uuid5(uuid.NAMESPACE_DNS, f"apex-{tier}-{base_name}").hex
    return {
        "modelfile": f"# Apex model — tier: {tier}\nFROM {base_name}",
        "capabilities": ["completion", "tools"],
        "parameters": f"num_predict 512\nnum_ctx {OLLAMA_NUM_CTX}\ntemperature 0.7",
        "template": "{{ if .System }}<system>\n{{ .System }}\n</system>\n{{ end }}{{ range .Messages }}<{{ .Role }}>\n{{ .Content }}\n</{{ .Role }}>\n{{ end }}",
        "details": {
            "format": "gguf",
            "family": "apex",
            "families": ["apex"],
            "parameter_size": "dynamic",
            "quantization_level": "none",
        },
        "model_info": {
            "general.architecture": "apex",
            "general.file_type": 0,
            "general.parameter_count": 0,
        },
        "digest": digest,
    }


@app.get("/api/ps")
async def ollama_compat_ps(request: Request) -> dict[str, Any]:
    _verifier_cle_api_compat(request)
    now_iso = datetime.now(timezone.utc).isoformat()
    running = []
    for tier in MODEL_TIERS:
        state = _model_runtime_state_by_tier.get(tier, "cold")
        if state == "ready" or (tier == DEFAULT_MODEL_TIER and _ollama_active()):
            running.append({
                "name": f"apex:{tier}",
                "model": f"apex:{tier}",
                "size": 0,
                "digest": uuid.uuid5(uuid.NAMESPACE_DNS, f"apex-{tier}").hex,
                "details": {"format": "gguf", "family": "apex", "parameter_size": "dynamic"},
                "expires_at": "",
                "size_vram": 0,
            })
    return {"models": running}


class _OllamaPullRequest(BaseModel):
    name: str = ""
    model: str | None = None
    stream: bool = True
    model_config = {"extra": "ignore"}


@app.post("/api/pull")
async def ollama_compat_pull(payload: _OllamaPullRequest, request: Request) -> Any:
    """Fake pull — Apex models are always local."""
    _verifier_cle_api_compat(request)
    model_name = payload.model or payload.name
    if not payload.stream:
        return {"status": "success"}

    async def _stream() -> AsyncIterator[str]:
        yield _ndjson({"status": f"pulling manifest for {model_name}"})
        yield _ndjson({"status": "verifying sha256 digest"})
        yield _ndjson({"status": "writing manifest"})
        yield _ndjson({"status": "success"})

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


@app.delete("/api/delete")
async def ollama_compat_delete(request: Request) -> dict[str, str]:
    """Fake delete — Apex models cannot be deleted via API."""
    _verifier_cle_api_compat(request)
    return {"status": "ok"}


class _OllamaEmbedRequest(BaseModel):
    model: str = "apex:default"
    prompt: str | None = None
    input: Any = None      # str or list[str]
    options: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "ignore"}


def _fake_embedding(text: str) -> list[float]:
    """Deterministic fake 768-dim embedding seeded from text hash."""
    import hashlib
    seed = int(hashlib.sha256(text.encode()).hexdigest(), 16) % (2**31)
    rng = __import__("random").Random(seed)
    return [round(rng.gauss(0, 1), 6) for _ in range(768)]


@app.post("/api/embeddings")
@app.post("/api/embed")
async def ollama_compat_embed(payload: _OllamaEmbedRequest, request: Request) -> dict[str, Any]:
    _verifier_cle_api_compat(request)
    texts: list[str] = []
    if payload.input is not None:
        texts = [payload.input] if isinstance(payload.input, str) else list(payload.input)
    elif payload.prompt:
        texts = [payload.prompt]
    embeddings = [_fake_embedding(t) for t in texts]
    # Ollama /api/embed returns {"embeddings": [[...], ...]}
    # Ollama /api/embeddings (legacy) returns {"embedding": [...]}
    if request.url.path.endswith("/api/embeddings"):
        return {"embedding": embeddings[0] if embeddings else []}
    return {"model": payload.model, "embeddings": embeddings}


# ── Control Deck action endpoints ─────────────────────────────────────────────

class _TierPayload(BaseModel):
    tier: str = "fast"


@app.post("/api/adapter/set-default-tier")
async def set_default_tier(payload: _TierPayload, cle_api: str = Security(header_cle)) -> dict[str, Any]:
    tier = _normalize_tier(payload.tier)
    # Update the global default at runtime (does not persist .env but works until restart)
    global DEFAULT_MODEL_TIER
    DEFAULT_MODEL_TIER = tier
    return {"status": "ok", "default_tier": tier}


@app.post("/api/adapter/reload")
async def reload_adapter(payload: _TierPayload, cle_api: str = Security(header_cle)) -> dict[str, Any]:
    tier = _normalize_tier(payload.tier)
    if _ollama_active():
        ollama_model = OLLAMA_MODEL_NAMES[tier]
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"{APEX_OLLAMA_URL}/api/pull", json={"name": ollama_model, "stream": False})
        except Exception as exc:
            logger.warning("Ollama pull on reload failed: %s", exc)
        _model_runtime_state_by_tier[tier] = "ready"
        return {"status": "ok", "tier": tier, "mode": "ollama", "model": ollama_model}
    # HF path: just reset state so next call re-loads
    _model_runtime_state_by_tier[tier] = "cold"
    return {"status": "ok", "tier": tier, "mode": "hf", "message": "marked cold, will reload on next request"}


class _DatasetValidatePayload(BaseModel):
    file: str = "dataset_expert.json"
    min_count: int = 100


@app.post("/api/dataset/validate")
async def validate_dataset(payload: _DatasetValidatePayload, cle_api: str = Security(header_cle)) -> dict[str, Any]:
    import pathlib, re as _re
    safe_name = pathlib.Path(payload.file).name  # strip any path traversal
    path = pathlib.Path(safe_name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {safe_name}")
    try:
        import json as _json
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"JSON parse error: {exc}") from exc
    entries = data if isinstance(data, list) else data.get("data", [])
    count = len(entries)
    ok = count >= payload.min_count
    return {
        "file": safe_name,
        "count": count,
        "min_count": payload.min_count,
        "valid": ok,
        "message": "OK" if ok else f"Only {count} entries, expected >= {payload.min_count}",
    }


_eval_jobs: list[dict[str, Any]] = []


class _EvalRunPayload(BaseModel):
    tier: str = "fast"
    prompts_file: str = "evals/golden_prompts.jsonl"


@app.post("/api/eval/run")
async def run_eval(payload: _EvalRunPayload, cle_api: str = Security(header_cle)) -> dict[str, Any]:
    import pathlib, uuid as _uuid
    job_id = _uuid.uuid4().hex[:8]
    job: dict[str, Any] = {
        "id": job_id,
        "tier": _normalize_tier(payload.tier),
        "prompts_file": payload.prompts_file,
        "status": "queued",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "result": None,
    }
    _eval_jobs.append(job)

    async def _run_bg() -> None:
        job["status"] = "running"
        prompts_path = pathlib.Path(payload.prompts_file)
        if not prompts_path.exists():
            job["status"] = "error"
            job["result"] = f"Prompts file not found: {payload.prompts_file}"
            return
        try:
            import json as _json
            prompts = [_json.loads(l) for l in prompts_path.read_text(encoding="utf-8").splitlines() if l.strip()]
            results = []
            for p in prompts[:10]:  # Cap at 10 for quick eval
                q = p.get("prompt", p.get("question", ""))
                if not q:
                    continue
                resp, _ = await _generer_reponse(q, 120, job["tier"])
                results.append({"prompt": q[:80], "response": resp[:120]})
            job["status"] = "done"
            job["result"] = {"ran": len(results), "samples": results[:3]}
        except Exception as exc:
            job["status"] = "error"
            job["result"] = str(exc)

    asyncio.create_task(_run_bg())
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/eval/jobs")
async def get_eval_jobs() -> dict[str, Any]:
    return {"jobs": list(reversed(_eval_jobs[-20:]))}


# ── Ollama onboarding endpoints ───────────────────────────────────────────────

@app.get("/api/ollama/status")
async def ollama_status() -> dict[str, Any]:
    """Check Ollama connectivity and which required models are present."""
    required = list(OLLAMA_MODEL_NAMES.values())
    if not APEX_OLLAMA_URL:
        return {
            "connected": False,
            "url": "",
            "error": "APEX_OLLAMA_URL is not set in .env",
            "required_models": {m: "not_checked" for m in required},
        }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{APEX_OLLAMA_URL}/api/tags")
            r.raise_for_status()
            installed_names = {m["name"] for m in r.json().get("models", [])}
    except Exception as exc:
        return {
            "connected": False,
            "url": APEX_OLLAMA_URL,
            "error": str(exc),
            "required_models": {m: "unreachable" for m in required},
        }

    model_status: dict[str, str] = {}
    for m in required:
        # Ollama stores names like "phi3:mini" — check exact and prefix match
        if m in installed_names or any(n.startswith(m.split(":")[0]) for n in installed_names):
            model_status[m] = "ready"
        else:
            model_status[m] = "missing"

    all_ready = all(v == "ready" for v in model_status.values())
    return {
        "connected": True,
        "url": APEX_OLLAMA_URL,
        "error": None,
        "required_models": model_status,
        "all_ready": all_ready,
        "tier_map": {
            "fast": OLLAMA_MODEL_NAMES["fast"],
            "default": OLLAMA_MODEL_NAMES["default"],
            "reasoning": OLLAMA_MODEL_NAMES["reasoning"],
        },
        "hint": None if all_ready else "Pull missing models using the buttons below.",
    }


class _OllamaPullModelPayload(BaseModel):
    model: str


@app.post("/api/ollama/pull")
async def ollama_pull_model(payload: _OllamaPullModelPayload) -> StreamingResponse:
    """Pull a model from Ollama registry, streaming progress as NDJSON."""
    if not APEX_OLLAMA_URL:
        raise HTTPException(status_code=503, detail="APEX_OLLAMA_URL not configured.")

    model = payload.model.strip()
    if not model:
        raise HTTPException(status_code=422, detail="model is required.")

    async def _stream_pull() -> AsyncIterator[str]:
        try:
            async with httpx.AsyncClient(timeout=600) as client:
                async with client.stream(
                    "POST",
                    f"{APEX_OLLAMA_URL}/api/pull",
                    json={"name": model, "stream": True},
                ) as resp:
                    if resp.status_code >= 400:
                        body = await resp.aread()
                        yield _ndjson({"status": "error", "error": body.decode(errors="replace")})
                        return
                    async for line in resp.aiter_lines():
                        if line.strip():
                            yield line + "\n"
        except Exception as exc:
            yield _ndjson({"status": "error", "error": str(exc)})

    return StreamingResponse(_stream_pull(), media_type="application/x-ndjson")


# ─────────────────────────────────────────────────────────────────────────────


def _safe_file_path(base_dir: str, filename: str) -> str:
    """
    Security: Safely resolve file path to prevent path traversal attacks.
    
    Args:
        base_dir: Base directory (e.g., UI_DIR)
        filename: Requested filename
    
    Returns:
        Absolute path if safe, raises HTTPException(403) if traversal detected
    
    Raises:
        HTTPException: 403 if path traversal detected, 404 if file not found
    """
    # Resolve to absolute paths
    base_resolved = os.path.realpath(base_dir)
    requested_path = os.path.realpath(os.path.join(base_dir, filename))
    
    # Security: Ensure the resolved path is within base directory
    if not requested_path.startswith(base_resolved + os.sep) and requested_path != base_resolved:
        logger.warning(
            "Path traversal attempt blocked: base=%s, requested=%s, resolved=%s",
            base_dir, filename, requested_path
        )
        raise HTTPException(status_code=403, detail="Access denied: Invalid path")
    
    if not os.path.isfile(requested_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    return requested_path


@app.get("/")
async def ui_index() -> FileResponse:
    # Security: Use safe path resolution
    index_path = _safe_file_path(UI_DIR, "index.html")
    return FileResponse(index_path, headers={"Content-Type": "text/html; charset=utf-8"})


@app.get("/developer")
async def ui_developer() -> FileResponse:
    # Security: Use safe path resolution
    developer_path = _safe_file_path(UI_DIR, "developer.html")
    return FileResponse(developer_path, headers={"Content-Type": "text/html; charset=utf-8"})


@app.get("/ui/developer.html")
async def ui_developer_legacy() -> FileResponse:
    return await ui_developer()


@app.get("/pricing")
async def ui_pricing() -> FileResponse:
    # Security: Use safe path resolution
    pricing_path = _safe_file_path(UI_DIR, "pricing.html")
    return FileResponse(pricing_path, headers={"Content-Type": "text/html; charset=utf-8"})


@app.get("/ui/pricing.html")
async def ui_pricing_legacy() -> FileResponse:
    return await ui_pricing()


# ── Startup/Shutdown Event Handlers ───────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    """Log comprehensive startup summary."""
    logger.info("=" * 80)
    logger.info("STARTUP COMPLETE")
    logger.info("=" * 80)
    logger.info("Server ready to accept requests")
    logger.info("Endpoints available:")
    logger.info("  - GET  /health")
    logger.info("  - POST /chat")
    logger.info("  - POST /chat/stream")
    logger.info("  - POST /chat/v2")
    logger.info("  - POST /v1/chat/completions (OpenAI compat)")
    logger.info("  - GET  /api/tools")
    logger.info("  - GET  /api/usage")
    logger.info("  - GET  /api/status")
    logger.info("  - GET  /developer")
    logger.info("  - GET  /pricing")
    logger.info("=" * 80)


@app.on_event("shutdown")
async def shutdown_event():
    """Log shutdown event."""
    logger.info("=" * 80)
    logger.info("SHUTDOWN INITIATED")
    logger.info("=" * 80)
    logger.info("Closing connections and cleaning up resources...")