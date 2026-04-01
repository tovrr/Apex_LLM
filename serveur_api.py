from fastapi import FastAPI, HTTPException, Security, Request
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
import torch
import os
import logging
from typing import Any, Literal, cast
import asyncio
import time
import json
import uuid
from collections import defaultdict, deque
from threading import Lock
from threading import Thread
from datetime import datetime, timezone
from dotenv import load_dotenv
import key_store

# Les outils des pros (HuggingFace)
from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer
from peft import PeftModel

# ==========================================
# 1. CONFIGURATION DU SERVEUR ET SÉCURITÉ
# ==========================================
app = FastAPI(title="Apex Pro API", description="L'API officielle de Quill AI (Modèle Fine-Tuné)")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(BASE_DIR, "ui")
DATA_DIR = os.path.join(BASE_DIR, "data")
RUNS_FILE = os.path.join(DATA_DIR, "runs_history.jsonl")

if os.path.isdir(UI_DIR):
    app.mount("/ui/assets", StaticFiles(directory=UI_DIR), name="ui-assets")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("apex_api")
load_dotenv()

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

_historique_requetes: defaultdict[str, deque[float]] = defaultdict(deque)
_rate_limit_lock = Lock()
_runs_lock = Lock()


def _log_event(name: str, **fields: Any) -> None:
    payload = {"event": name, **fields}
    logger.info(json.dumps(payload, ensure_ascii=False, default=str))

# Le Videur (CORS) qui autorise ton site Vercel à te parler
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://quill-ai-xi.vercel.app", "http://localhost:3000"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"], 
)


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
print("👨‍🍳 Le serveur s'allume...")


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
DEFAULT_MODEL_NAME = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
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

        if os.getenv("APEX_SKIP_MODEL_LOAD") == "1":
            logger.warning("APEX_SKIP_MODEL_LOAD=1 actif: chargement d'un modèle factice de test.")
            tokenizer = _FakeTokenizer()
            modele_apex = _FakeModel()
            _active_model_tier = selected_tier
            _model_runtime_state_by_tier[selected_tier] = "ready"
            return selected_tier

        try:
            nom_modele_base = MODEL_NAMES[selected_tier]
            tokenizer = AutoTokenizer.from_pretrained(nom_modele_base)

            utilise_cuda = torch.cuda.is_available()
            dtype_modele = torch.float16 if utilise_cuda else torch.float32
            device_map_modele = "auto" if utilise_cuda else "cpu"

            print(f"🧠 Chargement du modèle ({selected_tier}) : {nom_modele_base}")
            modele_base = AutoModelForCausalLM.from_pretrained(
                nom_modele_base,
                torch_dtype=dtype_modele,
                device_map=device_map_modele,
            )

            dossier_lora = MODEL_LORA_DIRS.get(selected_tier, "")
            if dossier_lora and os.path.isdir(dossier_lora):
                print(f"🔌 Branchement LoRA ({selected_tier}) : {dossier_lora}")
                try:
                    modele_apex = PeftModel.from_pretrained(modele_base, dossier_lora)
                except Exception as e:
                    logger.warning(
                        "Chargement LoRA impossible (%s). Fallback sur modèle de base.",
                        e,
                    )
                    modele_apex = modele_base
            else:
                modele_apex = modele_base

            _active_model_tier = selected_tier
            _model_runtime_state_by_tier[selected_tier] = "ready"
            return selected_tier
        except Exception as exc:
            _model_runtime_state_by_tier[selected_tier] = "error"
            _model_runtime_error_by_tier[selected_tier] = str(exc)
            logger.exception("Chargement modèle impossible")
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


def _preparer_inputs(question: str) -> tuple[str, Any]:
    assert tokenizer is not None and modele_apex is not None
    prompt = f"<|user|>\n{question}\n<|assistant|>\n"
    inputs = tokenizer(prompt, return_tensors="pt")
    if hasattr(inputs, "to"):
        inputs = inputs.to(modele_apex.device)
    else:
        inputs = {k: v.to(modele_apex.device) for k, v in inputs.items()}
    return prompt, inputs


async def _generer_reponse(question: str, mots_max: int, model_tier: str | None = None) -> tuple[str, str]:
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
    source: str = Field(..., min_length=1, max_length=512)
    content: str = Field(..., min_length=1, max_length=3000)
    score: float | None = None


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


@app.get("/")
async def ui_index() -> FileResponse:
    index_path = os.path.join(UI_DIR, "index.html")
    if not os.path.isfile(index_path):
        raise HTTPException(status_code=404, detail="UI introuvable.")
    return FileResponse(index_path)


@app.get("/developer")
async def ui_developer() -> FileResponse:
    developer_path = os.path.join(UI_DIR, "developer.html")
    if not os.path.isfile(developer_path):
        raise HTTPException(status_code=404, detail="Page développeur introuvable.")
    return FileResponse(developer_path)


@app.get("/pricing")
async def ui_pricing() -> FileResponse:
    pricing_path = os.path.join(UI_DIR, "pricing.html")
    if not os.path.isfile(pricing_path):
        raise HTTPException(status_code=404, detail="Page pricing introuvable.")
    return FileResponse(pricing_path)