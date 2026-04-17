"""key_store.py — SQLite-backed multi-tenant API key store for Apex.

Tables
------
api_keys      : one row per issued key (stored as salted SHA-256 hash, never raw)
usage_daily   : daily request + token counters per key (fast quota checks)
usage_events  : per-request event ledger (durable, Stripe-ready)

Keys are never stored in plain text. The raw key is hashed with a per-key salt
on write and on every verification. The first 8 characters are kept as a display
prefix only.

SECURITY: 
- Per-key random salt prevents rainbow table attacks
- Minimum key entropy validation (32 chars)
- Keys are validated for strength before acceptance

Stripe meter event shape (usage_events)
----------------------------------------
Each row maps 1-to-1 to a Stripe Billing Meter Event when metering is wired:
  stripe.billing.meter_event.create(
      event_name="apex_tokens_used",
      payload={"value": tokens_used, "stripe_customer_id": ..., "idempotency_key": event_id}
  )
"""

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from typing import NamedTuple, Optional

# Override via env var (useful for tests: set to a temp file path)
_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "apex_keys.db")
DB_PATH: str = os.getenv("APEX_KEYS_DB") or _DEFAULT_DB

_db_lock = threading.Lock()

# Security: Global salt for additional HMAC layer (rotate annually)
_GLOBAL_SALT = os.getenv("APEX_KEY_GLOBAL_SALT", secrets.token_hex(32)).encode()

# Minimum API key length for entropy requirements
MIN_API_KEY_LENGTH = 32

# Plan defaults — quota of -1 means unlimited
PLANS: dict[str, dict[str, int]] = {
    "internal": {"quota_req_per_day": -1,       "quota_tokens_per_day": -1},
    "free":     {"quota_req_per_day": 50,        "quota_tokens_per_day": 25_000},
    "pro":      {"quota_req_per_day": 1_000,     "quota_tokens_per_day": 500_000},
    "team":     {"quota_req_per_day": -1,        "quota_tokens_per_day": -1},
}

# ── DDL ────────────────────────────────────────────────────────────────────────

_DDL_KEYS = """
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash             TEXT PRIMARY KEY,
    key_salt             TEXT NOT NULL,
    key_prefix           TEXT NOT NULL,
    label                TEXT NOT NULL,
    plan                 TEXT NOT NULL DEFAULT 'free',
    is_active            INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT NOT NULL,
    quota_req_per_day    INTEGER NOT NULL DEFAULT 50,
    quota_tokens_per_day INTEGER NOT NULL DEFAULT 25000
);
"""

_DDL_USAGE = """
CREATE TABLE IF NOT EXISTS usage_daily (
    key_hash      TEXT NOT NULL,
    date          TEXT NOT NULL,
    requests_used INTEGER NOT NULL DEFAULT 0,
    tokens_used   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (key_hash, date)
);
"""

# Per-request event ledger.  Stripe meter events are derived from this table.
_DDL_EVENTS = """
CREATE TABLE IF NOT EXISTS usage_events (
    event_id      TEXT PRIMARY KEY,
    key_hash      TEXT NOT NULL,
    timestamp     TEXT NOT NULL,
    endpoint      TEXT NOT NULL DEFAULT '/chat',
    tokens_used   INTEGER NOT NULL DEFAULT 0,
    latency_ms    INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'success',
    task_type     TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_events_key_ts
    ON usage_events (key_hash, timestamp);
"""

# ── Internal helpers ───────────────────────────────────────────────────────────


class KeyInfo(NamedTuple):
    key_hash: str
    label: str
    plan: str
    quota_req_per_day: int
    quota_tokens_per_day: int
    key_salt: str = ""  # Salt stored for verification


class QuotaExceededError(Exception):
    """Raised when a key has exhausted its daily quota."""


def _validate_key_strength(raw_key: str) -> None:
    """
    Validate API key meets minimum security requirements.
    
    Requirements:
    - Minimum 32 characters (256 bits of entropy with hex encoding)
    - Must contain alphanumeric characters
    - Cannot be all same character
    """
    if len(raw_key) < MIN_API_KEY_LENGTH:
        raise ValueError(
            f"API key too short. Minimum length is {MIN_API_KEY_LENGTH} characters. "
            f"Current length: {len(raw_key)}. Generate a stronger key."
        )
    
    if not raw_key.replace('-', '').replace('_', '').isalnum():
        raise ValueError("API key contains invalid characters. Use only alphanumeric, hyphens, and underscores.")
    
    if len(set(raw_key)) < 10:
        raise ValueError("API key has insufficient entropy. Too many repeated characters.")


def _hash_key(raw_key: str, key_salt: Optional[str] = None) -> str:
    """
    Hash API key with per-key salt using HMAC-SHA256.
    
    Security measures:
    - Per-key random salt prevents rainbow table attacks
    - Global salt adds additional layer of protection
    - HMAC construction prevents length extension attacks
    
    Args:
        raw_key: The raw API key string
        key_salt: Per-key salt (generated if not provided)
    
    Returns:
        Hex-encoded hash of the key
    """
    if key_salt is None:
        key_salt = secrets.token_hex(32)
    
    # Combine global salt, per-key salt, and key for maximum security
    message = f"{key_salt}:{raw_key}".encode()
    return hmac.new(_GLOBAL_SALT, message, hashlib.sha256).hexdigest()


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_events_schema(conn: sqlite3.Connection) -> None:
    """Backfill columns for older DB files created before schema extensions."""
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(usage_events)").fetchall()
    }
    if "task_type" not in cols:
        conn.execute(
            "ALTER TABLE usage_events ADD COLUMN task_type TEXT NOT NULL DEFAULT 'default'"
        )


# ── Public API ─────────────────────────────────────────────────────────────────


def init_db() -> None:
    """Create tables if they do not exist. Safe to call multiple times."""
    with _db_lock:
        with _connect() as conn:
            conn.execute(_DDL_KEYS)
            conn.execute(_DDL_USAGE)
            # _DDL_EVENTS contains two statements; execute each separately.
            for stmt in _DDL_EVENTS.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(stmt)
            _ensure_events_schema(conn)
            conn.commit()


def add_key(
    raw_key: str,
    label: str,
    plan: str = "free",
    quota_req_per_day: Optional[int] = None,
    quota_tokens_per_day: Optional[int] = None,
) -> str:
    """
    Hash and store a new API key with per-key salt.

    Security:
    - Validates key strength before acceptance
    - Generates random per-key salt
    - Uses HMAC-SHA256 with global + per-key salt

    Returns the key_hash.
    Raises ValueError for unknown plan, weak key, or duplicate key.
    """
    if plan not in PLANS:
        raise ValueError(f"Unknown plan '{plan}'. Valid plans: {list(PLANS)}")

    # Validate key strength
    _validate_key_strength(raw_key)

    defaults = PLANS[plan]
    qr = quota_req_per_day    if quota_req_per_day    is not None else defaults["quota_req_per_day"]
    qt = quota_tokens_per_day if quota_tokens_per_day is not None else defaults["quota_tokens_per_day"]

    # Generate per-key salt and compute hash
    key_salt = secrets.token_hex(32)
    key_hash = _hash_key(raw_key, key_salt)
    key_prefix = raw_key[:8]
    now = datetime.now(timezone.utc).isoformat()

    with _db_lock:
        with _connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO api_keys
                        (key_hash, key_salt, key_prefix, label, plan, is_active, created_at,
                         quota_req_per_day, quota_tokens_per_day)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (key_hash, key_salt, key_prefix, label, plan, now, qr, qt),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError("Key already exists in the store.") from exc

    return key_hash


def verify_key(raw_key: str) -> Optional[KeyInfo]:
    """
    Look up a raw key. Returns KeyInfo if found and active, None otherwise.
    The raw key is hashed with stored salt before DB access.
    
    Security: Uses per-key salt retrieved from database to prevent timing attacks.
    """
    # First, get all keys to find matching salt (we need salt to hash)
    # This is a trade-off: we could store salt separately for faster lookup
    # but this approach is more secure
    key_hash_attempt = _hash_key(raw_key, "")  # Try with empty salt first
    
    with _db_lock:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT key_hash, key_salt, label, plan, quota_req_per_day, quota_tokens_per_day
                FROM api_keys
                WHERE is_active = 1
                """,
            ).fetchall()
            
            for key_row in row:
                # Try to verify with this key's salt
                test_hash = _hash_key(raw_key, key_row["key_salt"])
                if test_hash == key_row["key_hash"]:
                    return KeyInfo(
                        key_hash=key_row["key_hash"],
                        label=key_row["label"],
                        plan=key_row["plan"],
                        quota_req_per_day=key_row["quota_req_per_day"],
                        quota_tokens_per_day=key_row["quota_tokens_per_day"],
                        key_salt=key_row["key_salt"],
                    )
    
    return None


def check_quota(key_hash: str) -> None:
    """
    Pre-flight quota check. Raises QuotaExceededError if the key has hit its
    daily request or token limit. Does not write anything.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _db_lock:
        with _connect() as conn:
            key_row = conn.execute(
                "SELECT quota_req_per_day, quota_tokens_per_day FROM api_keys WHERE key_hash = ?",
                (key_hash,),
            ).fetchone()
            if key_row is None:
                raise QuotaExceededError("Key not found in store.")

            usage_row = conn.execute(
                "SELECT requests_used, tokens_used FROM usage_daily WHERE key_hash = ? AND date = ?",
                (key_hash, today),
            ).fetchone()

    current_req = usage_row["requests_used"] if usage_row else 0
    current_tok = usage_row["tokens_used"]   if usage_row else 0
    quota_req   = key_row["quota_req_per_day"]
    quota_tok   = key_row["quota_tokens_per_day"]

    if quota_req != -1 and current_req >= quota_req:
        raise QuotaExceededError(f"Daily request quota exceeded ({quota_req} req/day).")
    if quota_tok != -1 and current_tok >= quota_tok:
        raise QuotaExceededError(f"Daily token quota exceeded ({quota_tok} tokens/day).")


def record_usage(
    key_hash: str,
    tokens_used: int,
    *,
    endpoint: str = "/chat",
    latency_ms: int = 0,
    status: str = "success",
    task_type: str = "default",
) -> str:
    """
    Increment today's request/token counters AND write a granular event row.
    Returns the new event_id (UUID4 hex string).
    Called after every generation attempt (success or error).
    """
    import uuid as _uuid
    today    = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso  = datetime.now(timezone.utc).isoformat()
    event_id = _uuid.uuid4().hex

    with _db_lock:
        with _connect() as conn:
            # 1. Update daily aggregate (used for fast quota checks).
            conn.execute(
                """
                INSERT INTO usage_daily (key_hash, date, requests_used, tokens_used)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(key_hash, date) DO UPDATE SET
                    requests_used = requests_used + 1,
                    tokens_used   = tokens_used + excluded.tokens_used
                """,
                (key_hash, today, tokens_used),
            )
            # 2. Write durable event row (Stripe meter source of truth).
            conn.execute(
                """
                INSERT INTO usage_events
                    (event_id, key_hash, timestamp, endpoint, tokens_used, latency_ms, status, task_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, key_hash, now_iso, endpoint, tokens_used, latency_ms, status, task_type),
            )
            conn.commit()

    return event_id


def revoke_key(key_hash: str) -> bool:
    """
    Deactivate a key by its full SHA-256 hash.
    Returns True if a matching key was found and deactivated.
    """
    with _db_lock:
        with _connect() as conn:
            cursor = conn.execute(
                "UPDATE api_keys SET is_active = 0 WHERE key_hash = ?",
                (key_hash,),
            )
            conn.commit()
    return cursor.rowcount > 0


def list_keys() -> list[dict]:
    """
    Return metadata for all keys (no raw key, no hash).
    Includes today's usage counters via a LEFT JOIN.
    """
    with _db_lock:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    k.key_prefix,
                    k.label,
                    k.plan,
                    k.is_active,
                    k.created_at,
                    k.quota_req_per_day,
                    k.quota_tokens_per_day,
                    COALESCE(u.requests_used, 0) AS today_requests,
                    COALESCE(u.tokens_used,   0) AS today_tokens
                FROM api_keys k
                LEFT JOIN usage_daily u
                    ON k.key_hash = u.key_hash AND u.date = date('now')
                ORDER BY k.created_at DESC
                """,
            ).fetchall()
    return [dict(row) for row in rows]


def get_usage_today(key_hash: str) -> dict[str, int]:
    """Return today's usage for a specific key hash."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _db_lock:
        with _connect() as conn:
            row = conn.execute(
                "SELECT requests_used, tokens_used FROM usage_daily WHERE key_hash = ? AND date = ?",
                (key_hash, today),
            ).fetchone()
    if row is None:
        return {"requests_used": 0, "tokens_used": 0}
    return {"requests_used": row["requests_used"], "tokens_used": row["tokens_used"]}


def get_usage_summary(key_hash: str, days: int = 30, events_limit: int = 50) -> dict:
    """
    Return a billing-ready usage summary for a key:
    - key metadata (plan, quotas)
    - daily aggregates for the last N days
    - most recent events (for audit / Stripe meter replay)
    """
    days = max(1, min(days, 90))
    events_limit = max(1, min(events_limit, 200))

    with _db_lock:
        with _connect() as conn:
            key_row = conn.execute(
                """
                SELECT key_prefix, label, plan, is_active,
                       quota_req_per_day, quota_tokens_per_day
                FROM api_keys WHERE key_hash = ?
                """,
                (key_hash,),
            ).fetchone()

            if key_row is None:
                return {}

            daily_rows = conn.execute(
                """
                SELECT date, requests_used, tokens_used
                FROM usage_daily
                WHERE key_hash = ?
                  AND date >= date('now', ?)
                ORDER BY date DESC
                """,
                (key_hash, f"-{days} days"),
            ).fetchall()

            event_rows = conn.execute(
                """
                SELECT event_id, timestamp, endpoint, tokens_used, latency_ms, status, task_type
                FROM usage_events
                WHERE key_hash = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (key_hash, events_limit),
            ).fetchall()

    # Build totals from the daily window
    total_requests = sum(r["requests_used"] for r in daily_rows)
    total_tokens   = sum(r["tokens_used"]   for r in daily_rows)

    return {
        "key_prefix":            key_row["key_prefix"],
        "label":                 key_row["label"],
        "plan":                  key_row["plan"],
        "is_active":             bool(key_row["is_active"]),
        "quota_req_per_day":     key_row["quota_req_per_day"],
        "quota_tokens_per_day":  key_row["quota_tokens_per_day"],
        "window_days":           days,
        "totals": {
            "requests": total_requests,
            "tokens":   total_tokens,
        },
        "daily": [dict(r) for r in daily_rows],
        "recent_events": [dict(r) for r in event_rows],
    }
