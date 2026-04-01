# Apex AI Project - TODO & Findings

## ✅ Completed

- [x] API security baseline: env key, input validation, fallback warning
- [x] API hardening baseline: rate limit + timeout + health/status endpoints
- [x] Local UI Control Deck with streaming, runtime badges, and run export
- [x] Run history persistence and `/api/runs` endpoint
- [x] Smoke tests passing (`pytest`)
- [x] Workspace environment lock on `./venv`
- [x] Repository hygiene: `.gitignore`, checkpoint cleanup, cache cleanup

## 🔄 In Progress / Pending

- [ ] Validate end-to-end UX with real prompts on CPU/GPU host and tune defaults (`mots_max`, timeout, rate limits)
- [ ] Stabilize LoRA compatibility warnings during load on local hardware
- [ ] Finalize public docs for web + mobile integration path

## 📝 Technical Findings & Considerations

- Current architecture is strong for local alpha, but commercialization requires multi-tenant API keys and billing controls.
- Next.js frontend should call Apex through server-side proxy routes only (never expose provider keys in browser/mobile clients).
- Streaming path is implemented; true token-level quality depends on runtime hardware/model behavior.
- Product positioning and business direction are documented in `STRATEGY.md`.
- Capability growth order, retrieval roadmap, expert roadmap, datasets, and eval gates are documented in `CAPABILITY_ROADMAP.md`.

## 🚀 Commercialization Roadmap (90 Days)

### Phase 1 (Weeks 1-4): Product Foundation

- [x] Multi-tenant API keys (not single global key)
- [x] Per-key quotas and plan limits
- [x] Usage metering (requests, tokens, latency)
- [x] Per-request event ledger (Stripe-ready) + `GET /api/usage`
- [x] Eval instrumentation layer (golden prompts, runner, task_type tagging, 26 tests)
- [ ] Structured logs with request IDs
- [ ] Public API docs + pricing page draft

### Phase 2 (Weeks 5-8): Billing + Beta

- [ ] Stripe integration (subscriptions + overage)
- [ ] Plan enforcement middleware (Free/Pro/Team)
- [ ] Usage dashboard endpoint and UI panel
- [ ] Beta onboarding flow (self-serve)
- [ ] Alerting for quota, payment, and runtime failures

### Phase 3 (Weeks 9-12): Public Launch

- [ ] Team workspace basics (owner/member roles)
- [ ] Production deployment profile (container + reverse proxy + TLS)
- [ ] SLA targets and reliability checks (p95 latency/availability)
- [ ] Terms/Privacy/DPA publication
- [ ] Launch checklist and incident runbook

## 📱 Web + Mobile Integration (Quill AI)

- [ ] Build Next.js server routes as secure Apex proxy (`/api/apex/chat`, `/api/apex/stream`)
- [ ] Enforce user auth/session before proxying requests
- [ ] Implement per-user usage attribution for billing
- [ ] Reuse same proxy contract for iOS + Android clients
- [ ] Add mobile-safe retries and timeout UX states

## 🧠 Future Evolutions: MoE (Mixture of Experts)

- [ ] Optional smart router layer (after commercialization baseline)
- [ ] Expert adapters registry (`math`, `code`, `creative`, `default`)
- [ ] Dynamic adapter switching policy + benchmarking
- [ ] Safety and cost controls for expert routing

## 🎯 Immediate Next 3 Tasks

- [x] S1: Implement multi-tenant API keys + quotas in `serveur_api.py`
      → `key_store.py` (SQLite, hash-only storage, per-plan quotas, usage recording)
      → `manage_keys.py` (CLI: add / list / revoke)
      → 14/14 tests passing
- [x] S2: Add billing hooks and usage meter schema
      → `usage_events` table in key_store (per-request, Stripe-ready: event_id, endpoint, tokens, latency, status)
      → `get_usage_summary()` (30-day daily breakdown + recent events)
      → `GET /api/usage` endpoint (auth-gated, returns full billing summary)
      → 19/19 tests passing
- [x] S3: Create Next.js proxy integration spec for `quill-ai-xi.vercel.app`
      → `quill-proxy/lib/apex-client.ts` (shared config, types, key injection)
      → `quill-proxy/api/apex/chat/route.ts` (POST proxy, user auth guard)
      → `quill-proxy/api/apex/stream/route.ts` (SSE pass-through proxy)
      → EXPLOITATION.md §6 updated with step-by-step integration guide
