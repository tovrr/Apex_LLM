# Apex LLM

Private AI workspace for builders and teams.

![Apex LLM Hero](assets/apex-hero.svg)

[![Tests](https://github.com/tovrr/Apex_LLM/actions/workflows/ci.yml/badge.svg)](https://github.com/tovrr/Apex_LLM/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![API](https://img.shields.io/badge/API-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![Status](https://img.shields.io/badge/status-active-success.svg)](https://github.com/tovrr/Apex_LLM)

Apex LLM is a production-minded local AI platform with streaming chat, secure API keys, usage metering, evals, and model-tier routing.

## Live Demo

![Apex Demo](assets/apex-demo.svg)

Live endpoints:

- Control Deck: http://127.0.0.1:8000/
- Developer docs: http://127.0.0.1:8000/developer
- Pricing draft: http://127.0.0.1:8000/pricing

## Architecture

![Apex Architecture](assets/architecture.svg)

## Why Apex

- Modern API stack: FastAPI + SSE streaming + request IDs.
- Commercial-ready controls: multi-tenant keys, quotas, usage ledger, usage endpoint.
- Eval-first workflow: golden prompts, regression scoring, report artifacts.
- Model routing: fast/default/reasoning tiers with per-request selection.
- Quill integration: server-side proxy routes for secure web and mobile clients.

## What Is Already Built

- Chat endpoints: /chat, /chat/stream, /chat/v2.
- Tool and retrieval-ready contract: /chat/v2 + /api/tools.
- Usage and billing primitives: /api/usage + usage_events ledger.
- Local control UI: /, developer portal: /developer, pricing draft: /pricing.
- Test suite: 35 passing tests.

## Benchmark Snapshot

The table below is a practical target profile for local developer setups.

| Tier | Typical Model Class | Target Throughput (tok/s) | Target First-Token Latency |
| --- | --- | ---: | ---: |
| fast | Phi-3 Mini 3.8B | 35-90 | 250-900 ms |
| default | 7B-8B | 18-45 | 500-1800 ms |
| reasoning | 20B-30B | 8-25 | 1200-5000 ms |

How to benchmark Apex consistently:

1. Warm each tier with one request.
2. Run 20 prompts per tier (task_type mixed: code, reasoning, factual).
3. Compute p50 and p95 for first-token latency and output throughput.
4. Publish a monthly snapshot in README to build trust with visitors.

Reference command:

python evals/run_evals.py --url http://127.0.0.1:8000 --key YOUR_API_KEY

## Quick Start

1. Clone and enter project

   git clone https://github.com/tovrr/Apex_LLM.git
   cd Apex_LLM

2. Create environment and install dependencies

   python -m venv venv
   .\venv\Scripts\activate
   pip install -r requirements.txt

3. Configure environment

   copy .env.example .env
   then edit .env and set APEX_API_KEY

4. Run API

   uvicorn serveur_api:app --reload

5. Open local apps

- Control Deck: http://127.0.0.1:8000/
- Developer docs: http://127.0.0.1:8000/developer
- Pricing draft: http://127.0.0.1:8000/pricing
- Swagger: http://127.0.0.1:8000/docs

## API Snapshot

POST /chat
- question
- mots_max
- task_type
- model_tier: fast | default | reasoning

POST /chat/v2
- messages
- context_chunks
- tools
- tool_choice
- model_tier

GET /api/tools
- discover tool-calling + retrieval + model routing capabilities

## Model Tiers

- **fast**: [Phi-3 Mini 4k (3.8B)](https://huggingface.co/microsoft/Phi-3-mini-4k-instruct) — best intelligence/memory ratio for CPU-only (i7 + 16 GB RAM).
- **default**: 7B/8B sweet spot for daily usage (Qwen 2.5 7B recommended).
- **reasoning**: heavy model for complex coding and reasoning tasks (Qwen 3.5 27B Opus Distilled).

Configured in .env with:
- APEX_MODEL_DEFAULT_TIER
- APEX_MODEL_FAST_NAME
- APEX_MODEL_DEFAULT_NAME
- APEX_MODEL_REASONING_NAME

## Evaluation Loop

Run regression evals against your live API:

python evals/run_evals.py --url http://127.0.0.1:8000 --key YOUR_API_KEY

Reports are saved to evals/reports.

## Quill Integration

Proxy route templates are included in quill-proxy:

- quill-proxy/api/apex/chat/route.ts
- quill-proxy/api/apex/stream/route.ts
- quill-proxy/lib/apex-client.ts

These routes inject secrets server-side and keep browser/mobile clients key-safe.

## Repository Map

- serveur_api.py: main API server.
- key_store.py: key, quota, and usage ledger backend.
- manage_keys.py: key management CLI.
- evals/run_evals.py: eval runner.
- tests/test_api_smoke.py: test suite.
- ui/: local product UI.

## Current Priorities

- Public API docs hardening.
- Billing provider wiring on top of usage_events.
- Retrieval layer and enterprise controls.

## Public Roadmap

### Q2 2026

- Harden API docs and SDK examples.
- Add production billing hooks and token bundles.
- Launch retrieval v1 with citations and scoped context.

### Q3 2026

- Team workspaces with RBAC and audit trails.
- Latency and quality dashboards by model tier.
- Reliability targets and incident runbook publication.

### Q4 2026

- Advanced tool orchestration for coding workflows.
- Enterprise controls: retention and policy layers.
- Public benchmark board for monthly model comparisons.

## Contributing

Issues and PRs are welcome. If you open a PR, include:

- problem statement
- before/after behavior
- test evidence

## Acknowledgement

Built as an open, practical path toward a private AI workspace product and API business.
