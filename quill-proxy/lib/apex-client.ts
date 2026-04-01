/**
 * quill-proxy/lib/apex-client.ts
 *
 * Shared config and types for all Apex proxy routes.
 *
 * SECURITY RULES
 * ──────────────
 * 1. APEX_SECRET_KEY is read server-side only — never exported to the browser.
 * 2. All proxy routes must verify the user's Quill session before forwarding.
 * 3. The Apex API key is injected here, not passed in from the client.
 */

// ── Config ────────────────────────────────────────────────────────────────────

/** Base URL of the Apex FastAPI backend (no trailing slash). */
export const APEX_BASE_URL: string = (() => {
  const url = process.env.APEX_BASE_URL;
  if (!url) throw new Error("APEX_BASE_URL env var is not set.");
  return url.replace(/\/$/, "");
})();

/**
 * The Apex API key issued by `manage_keys.py add --label quill-prod --plan pro`.
 * Only ever read on the server — NEVER referenced in client components.
 */
export const APEX_SECRET_KEY: string = (() => {
  const key = process.env.APEX_SECRET_KEY;
  if (!key) throw new Error("APEX_SECRET_KEY env var is not set.");
  return key.trim();
})();

/** Default max tokens if the caller does not specify. */
export const DEFAULT_MAX_TOKENS = 200;

// ── Request / Response types ──────────────────────────────────────────────────

export interface ApexChatRequest {
  question: string;
  /** 1–500 tokens. Defaults to DEFAULT_MAX_TOKENS. */
  mots_max?: number;
}

export interface ApexChatResponse {
  run_id: string;
  status: "succes";
  question: string;
  reponse_apex: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Build the standard Apex request headers.
 * Called server-side only — injects the secret key.
 */
export function apexHeaders(): HeadersInit {
  return {
    "Content-Type": "application/json",
    "X-API-Key": APEX_SECRET_KEY,
  };
}

/**
 * Validate and clamp mots_max to the allowed range.
 * Returns a safe integer.
 */
export function safeMots(value: unknown): number {
  const n = Number(value);
  if (!Number.isFinite(n)) return DEFAULT_MAX_TOKENS;
  return Math.max(1, Math.min(500, Math.round(n)));
}
