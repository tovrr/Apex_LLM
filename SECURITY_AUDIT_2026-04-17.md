# Apex-LLM Security Audit Report

**Audit Date:** 2026-04-17  
**Auditor:** Droid (AI Security Analyst)  
**Scope:** Full codebase security review  
**Risk Rating Scale:** Critical 🔴 | High 🟠 | Medium 🟡 | Low 🟢 | Info ℹ️

---

## Executive Summary

The Apex-LLM codebase demonstrates **strong security fundamentals** in several areas (API key hashing, parameterized SQL queries, environment variable management) but has **critical vulnerabilities** that require immediate attention before production deployment.

### Overall Security Posture: 🟠 HIGH RISK

**Critical Issues:** 2  
**High Issues:** 5  
**Medium Issues:** 8  
**Low/Info:** 6

---

## Critical Findings (Immediate Action Required)

### 🔴 CR-01: Hardcoded API Keys in Git History

**Severity:** CRITICAL  
**File:** `.rotated_keys_20260416.txt`, git history  
**Status:** ✅ Active in repository

**Issue:**
- Rotated API keys are stored in plaintext in `.rotated_keys_20260416.txt`
- File contains full API keys with format: `apx_1af6c96e445a5a9c9b9601b913b84490`
- Git history shows key rotation occurred but old keys may still be accessible
- Keys are committed to git despite `.gitignore` rules

**Impact:**
- Anyone with repository access can use these API keys
- Potential for unauthorized API usage and quota exhaustion
- If keys were ever pushed to public fork, they are compromised

**Remediation:**
1. **IMMEDIATE:** Revoke all keys listed in `.rotated_keys_20260416.txt`
2. Run `git filter-branch` or BFG Repo-Cleaner to remove file from git history
3. Add `.rotated_keys_*.txt` to `.gitignore` (already present - verify enforcement)
4. Implement key rotation via secure secret manager (not file-based)
5. Audit key usage logs for unauthorized access

**Priority:** P0 - Do within 24 hours

---

### 🔴 CR-02: Insufficient API Key Entropy and Validation

**Severity:** CRITICAL  
**File:** `serveur_api.py`, `key_store.py`  
**Status:** ⚠️ Active

**Issue:**
- API keys use simple SHA-256 hashing without salting
- No minimum entropy requirements for key generation
- Keys can be brute-forced if hash database is compromised
- Test keys like "test-key" are accepted in production code paths

**Evidence:**
```python
def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()  # No salt!
```

**Impact:**
- Rainbow table attacks possible against hashed keys
- Weak keys vulnerable to dictionary attacks
- Database breach would expose all API keys

**Remediation:**
1. Add per-key random salt before hashing
2. Implement minimum key entropy validation (e.g., 256-bit entropy)
3. Use HMAC-based key derivation (HKDF) instead of raw SHA-256
4. Add key strength validation on creation
5. Consider using established API key formats (e.g., Stripe-style with checksum)

**Priority:** P0 - Do within 48 hours

---

## High-Risk Findings

### 🟠 HI-01: Overly Permissive CORS Configuration

**Severity:** HIGH  
**File:** `serveur_api.py` line 282-288  
**Status:** ⚠️ Active

**Issue:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://quill-ai-xi.vercel.app", "http://localhost:3000"], 
    allow_credentials=True,
    allow_methods=["*"],  # ❌ All methods allowed
    allow_headers=["*"],  # ❌ All headers allowed
)
```

**Impact:**
- Wildcard methods/headers increase attack surface
- `allow_credentials=True` with broad permissions enables CSRF attacks
- Any endpoint can be called from allowed origins with any method

**Remediation:**
1. Explicitly list allowed methods: `["GET", "POST", "OPTIONS"]`
2. Explicitly list allowed headers: `["Content-Type", "X-API-Key", "X-Request-ID"]`
3. Add `expose_headers` for only necessary response headers
4. Consider removing `localhost` in production builds
5. Add CORS validation logging for security monitoring

**Priority:** P1 - Do within 1 week

---

### 🟠 HI-02: SQL Injection Risk in Dynamic Schema Migration

**Severity:** HIGH  
**File:** `key_store.py` line 113-118  
**Status:** ⚠️ Active

**Issue:**
```python
def _ensure_events_schema(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute(
        "PRAGMA table_info(usage_events)"  # ✅ Safe
    ).fetchall()}
    if "task_type" not in cols:
        conn.execute(
            "ALTER TABLE usage_events ADD COLUMN task_type TEXT NOT NULL DEFAULT 'default'"
            # ❌ Dynamic column name could be injection vector if made configurable
        )
```

**Current Status:** Currently safe (hardcoded), but pattern is dangerous if made configurable.

**Remediation:**
1. Add explicit allowlist validation if column names become configurable
2. Document that this function must never accept external input
3. Add type annotations to prevent accidental refactoring
4. Consider using SQLAlchemy for ORM-based migrations

**Priority:** P1 - Do within 1 week (defensive hardening)

---

### 🟠 HI-03: Missing Rate Limiting on Critical Endpoints

**Severity:** HIGH  
**File:** `serveur_api.py`  
**Status:** ⚠️ Partial implementation

**Issue:**
- Rate limiting exists but only checks request count per window
- No IP-based rate limiting for unauthenticated endpoints
- `/health`, `/api/status`, `/api/tools` are unlimited
- No rate limiting on `/api/eval/run` (expensive operation)

**Evidence:**
```python
def _verifier_rate_limit(cle_api: str, ip_client: str) -> None:
    # Only called for authenticated requests
    # Unauthenticated endpoints have no protection
```

**Impact:**
- DoS attacks via unauthenticated endpoints
- Resource exhaustion on eval endpoints
- Reconnaissance attacks via unlimited health/status checks

**Remediation:**
1. Add IP-based rate limiting for all endpoints (authenticated or not)
2. Implement stricter limits on expensive operations (`/api/eval/run`)
3. Add rate limit headers (`X-RateLimit-Limit`, `X-RateLimit-Remaining`)
4. Consider using `slowapi` or `fastapi-limiter` for production-grade limiting
5. Add monitoring/alerting for rate limit violations

**Priority:** P1 - Do within 1 week

---

### 🟠 HI-04: Sensitive Data in Logs

**Severity:** HIGH  
**File:** `serveur_api.py` line 445  
**Status:** ⚠️ Active

**Issue:**
```python
logger.info("Legacy API key seeded: %s...", _LEGACY_RAW_KEY[:8])
# Logs first 8 characters of API key in plaintext
```

**Impact:**
- Partial key exposure aids reconnaissance
- Log aggregation systems may expose keys
- Violates principle of logging no secret material

**Remediation:**
1. Never log any portion of API keys, even prefixes
2. Log only key hash prefixes if debugging needed: `key_hash[:12]`
3. Add log sanitization middleware for sensitive patterns
4. Implement structured logging with redaction rules
5. Review all `logger.info()` calls for sensitive data

**Priority:** P1 - Do within 1 week

---

### 🟠 HI-05: Path Traversal Risk in File Serving

**Severity:** HIGH  
**File:** `serveur_api.py` line 98-99, 2500-2520  
**Status:** ⚠️ Active

**Issue:**
```python
UI_DIR = os.path.join(BASE_DIR, "ui")
app.mount("/ui/assets", StaticFiles(directory=UI_DIR), name="ui-assets")

# FileResponse endpoints
index_path = os.path.join(UI_DIR, "index.html")
if not os.path.isfile(index_path):
    raise HTTPException(status_code=404, detail="UI introuvable.")
return FileResponse(index_path)
```

**Impact:**
- StaticFiles mount is generally safe, but...
- No validation that requested files are within UI_DIR
- Potential for symlink attacks if UI directory is compromised
- No Content-Type validation for served files

**Remediation:**
1. Add `pathlib.Path().resolve()` to prevent `..` traversal
2. Validate that resolved path starts with UI_DIR
3. Add explicit Content-Type headers for all responses
4. Consider using `aiofiles` for async file serving with better security
5. Add CSP headers to prevent XSS via uploaded assets

**Priority:** P1 - Do within 2 weeks

---

### 🟠 HI-06: No Input Validation on User Prompts

**Severity:** HIGH  
**File:** `serveur_api.py` multiple locations  
**Status:** ⚠️ Active

**Issue:**
- User prompts are passed directly to model without sanitization
- No validation for prompt injection attacks
- No length limits on `question` field before processing
- `context_chunks` in `/chat/v2` are not validated for size or content

**Evidence:**
```python
def _preparer_inputs(question: str, ...) -> tuple[str, Any]:
    prompt = f"<|user|>\n{question}\n<|assistant|>\n"  # Direct interpolation
    inputs = tokenizer(prompt, return_tensors="pt")
```

**Impact:**
- Prompt injection attacks possible
- Resource exhaustion via very long prompts
- System prompt leakage via injection
- Tool call manipulation via crafted prompts

**Remediation:**
1. Add maximum prompt length validation (e.g., 8000 chars)
2. Implement prompt injection detection (block `<|assistant|>` in user input)
3. Sanitize `context_chunks` content and size
4. Add input validation middleware with OWASP recommendations
5. Log and alert on detected injection attempts

**Priority:** P1 - Do within 2 weeks

---

### 🟠 HI-07: Insecure Direct Object Reference (IDOR) in Usage API

**Severity:** HIGH  
**File:** `serveur_api.py`, `key_store.py`  
**Status:** ⚠️ Active

**Issue:**
- Usage endpoints return data based solely on API key
- No additional authorization checks for sensitive operations
- `/api/usage` exposes full usage history to any valid key holder
- No audit trail for who accessed usage data

**Remediation:**
1. Add rate limiting on usage endpoints
2. Implement usage data access logging
3. Consider adding secondary authorization for bulk data access
4. Add pagination to limit data exposure per request
5. Implement data retention policies for usage events

**Priority:** P2 - Do within 1 month

---

## Medium-Risk Findings

### 🟡 MD-01: No Security Headers

**Severity:** MEDIUM  
**File:** `serveur_api.py`  
**Status:** ❌ Missing

**Issue:**
- No Content-Security-Policy (CSP)
- No X-Content-Type-Options
- No X-Frame-Options
- No Strict-Transport-Security
- No Referrer-Policy

**Remediation:**
Add middleware:
```python
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000"
    response.headers["Content-Security-Policy"] = "default-src 'self'"
    return response
```

**Priority:** P2 - Do within 2 weeks

---

### 🟡 MD-02: Dependency Vulnerabilities

**Severity:** MEDIUM  
**File:** `requirements.txt`  
**Status:** ⚠️ Needs verification

**Issue:**
- No automated dependency scanning
- Versions may have known CVEs
- No lock file for reproducible builds

**Current Dependencies:**
```
fastapi==0.135.2
torch==2.11.0
transformers==5.4.0
...
```

**Remediation:**
1. Run `pip-audit` or `safety check` to identify CVEs
2. Add `requirements.lock` or use `pip-tools`
3. Set up Dependabot or Renovate for automated updates
4. Add GitHub Action for dependency scanning
5. Pin all transitive dependencies in production

**Priority:** P2 - Do within 2 weeks

---

### 🟡 MD-03: No Request/Response Validation

**Severity:** MEDIUM  
**File:** `serveur_api.py`  
**Status:** ⚠️ Partial

**Issue:**
- Pydantic models exist but don't validate all fields
- No response validation/sanitization
- Tool calls and context chunks not fully validated
- No schema versioning

**Remediation:**
1. Add `Field()` constraints to all Pydantic models (min/max length, regex)
2. Implement response validation middleware
3. Add schema versioning to API endpoints
4. Validate `tool_choice` and `tools` fields strictly
5. Add request size limits

**Priority:** P2 - Do within 1 month

---

### 🟡 MD-04: Weak Error Handling

**Severity:** MEDIUM  
**File:** `serveur_api.py` multiple locations  
**Status:** ⚠️ Active

**Issue:**
- Some error messages expose internal details
- Stack traces may leak in development mode
- No standardized error response format
- Exception logging inconsistent

**Evidence:**
```python
raise HTTPException(status_code=500, detail="Erreur interne de génération.")
# Generic but other errors may be more verbose
```

**Remediation:**
1. Implement global exception handler
2. Never expose stack traces in production
3. Use structured error responses with error codes
4. Log full errors server-side, return sanitized messages
5. Add error monitoring (Sentry, etc.)

**Priority:** P2 - Do within 1 month

---

### 🟡 MD-05: Database Connection Security

**Severity:** MEDIUM  
**File:** `key_store.py`  
**Status:** ⚠️ Active

**Issue:**
- SQLite database stored in `data/` directory
- No encryption at rest
- File permissions not validated
- No backup/restore security

**Remediation:**
1. Set restrictive file permissions on DB file (600)
2. Consider SQLCipher for encryption at rest
3. Implement secure backup procedures
4. Add database integrity checks
5. Consider moving to PostgreSQL for production

**Priority:** P2 - Do within 1 month

---

### 🟡 MD-06: No CSRF Protection

**Severity:** MEDIUM  
**File:** `serveur_api.py`  
**Status:** ❌ Missing

**Issue:**
- API uses cookie-based auth potentially (with `allow_credentials=True`)
- No CSRF token validation
- CORS with credentials increases CSRF risk

**Remediation:**
1. Add CSRF token middleware for state-changing operations
2. Use SameSite=Strict for any cookies
3. Implement double-submit cookie pattern if needed
4. Consider removing cookie auth entirely (API keys only)

**Priority:** P2 - Do within 1 month

---

### 🟡 MD-07: Insufficient Authentication Logging

**Severity:** MEDIUM  
**File:** `serveur_api.py`, `key_store.py`  
**Status:** ⚠️ Active

**Issue:**
- Failed auth attempts not logged with IP
- No alerting on brute force patterns
- No key usage anomaly detection

**Remediation:**
1. Log all failed authentication attempts with IP
2. Add alerting on N failures from same IP
3. Implement key usage anomaly detection
4. Add geographic usage tracking
5. Create security dashboard for monitoring

**Priority:** P2 - Do within 1 month

---

### 🟡 MD-08: Model Loading Security

**Severity:** MEDIUM  
**File:** `serveur_api.py`  
**Status:** ⚠️ Active

**Issue:**
- Model paths from environment variables without validation
- LoRA directories checked with `os.path.isdir()` but not validated
- No integrity checks on model files
- Potential for model poisoning attacks

**Remediation:**
1. Validate model paths are within allowed directories
2. Add model file integrity verification (checksums)
3. Implement model signing verification
4. Restrict which HuggingFace repos can be loaded
5. Add model loading audit logs

**Priority:** P2 - Do within 1 month

---

## Low-Risk Findings

### 🟢 LO-01: Debug Code in Production

**Issue:** `print("[apex] Le serveur s'allume...")` on line 333  
**Remediation:** Remove or convert to logger.debug()  
**Priority:** P3

### 🟢 LO-02: French Comments Mixed with English

**Issue:** Inconsistent language may cause confusion in security-critical comments  
**Remediation:** Standardize on English for all security-related comments  
**Priority:** P3

### 🟢 LO-03: No API Versioning

**Issue:** All endpoints are unversioned (`/chat` vs `/v1/chat`)  
**Remediation:** Add version prefix to all API endpoints  
**Priority:** P3

### 🟢 LO-04: Test Code Uses Production Patterns

**Issue:** Test keys like "test-key" follow same pattern as production  
**Remediation:** Use clearly distinct test key format  
**Priority:** P3

### 🟢 LO-05: No Health Check Authentication

**Issue:** `/health` endpoint is public  
**Remediation:** Consider requiring auth or adding secret token  
**Priority:** P3

### ℹ️ INFO-01: Good Security Practices Found

**Positive findings:**
- ✅ API keys hashed with SHA-256 (not stored in plaintext)
- ✅ Parameterized SQL queries (no string concatenation)
- ✅ Environment variables for secrets (not hardcoded)
- ✅ `.env` in `.gitignore`
- ✅ Thread locks for concurrent DB access
- ✅ Test isolation with temp databases

---

## Remediation Priority Matrix

| Priority | Timeline | Issues | Effort |
|----------|----------|--------|--------|
| **P0** | 24-48 hours | CR-01, CR-02 | High |
| **P1** | 1-2 weeks | HI-01 through HI-06 | High |
| **P2** | 1 month | MD-01 through MD-08 | Medium |
| **P3** | 2 months | LO-01 through LO-05 | Low |

---

## Recommended Security Tools

1. **Static Analysis:**
   - `bandit` - Python security linter
   - `semgrep` - Pattern-based security scanning
   - `safety` - Dependency vulnerability checker

2. **Dynamic Analysis:**
   - `zap` - OWASP ZAP for API penetration testing
   - `burpsuite` - Manual security testing

3. **Monitoring:**
   - `sentry` - Error tracking with security context
   - `prometheus` + `grafana` - Security metrics dashboard

4. **Secrets Management:**
   - Move from `.env` files to:
     - AWS Secrets Manager
     - HashiCorp Vault
     - Doppler

---

## Security Checklist for Next Release

- [ ] All CRITICAL issues resolved
- [ ] All HIGH issues resolved or mitigated
- [ ] Security headers implemented
- [ ] Dependency scan clean
- [ ] Penetration test completed
- [ ] Security monitoring in place
- [ ] Incident response plan documented
- [ ] Security review in CI/CD pipeline

---

## Conclusion

The Apex-LLM codebase has a **solid security foundation** but requires **immediate attention** to critical issues before production deployment. The development team demonstrates security awareness (key hashing, parameterized queries) but needs to implement defense-in-depth strategies.

**Next Steps:**
1. Address P0 issues immediately (key rotation, key entropy)
2. Schedule P1 issues for next sprint
3. Integrate security scanning into CI/CD
4. Plan quarterly security audits

---

**Report Generated:** 2026-04-17  
**Next Audit Recommended:** 2026-07-17 (quarterly)
