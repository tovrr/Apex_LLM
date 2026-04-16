/**
 * quill-proxy/api/apex/chat/route.ts
 *
 * Next.js App Router — POST /api/apex/chat
 *
 * Receives a question from the Quill client, injects the Apex secret key
 * server-side, forwards the request to the Apex backend, and returns the
 * full JSON response.
 *
 * HOW TO USE IN QUILL
 * ───────────────────
 * 1. Copy this file to:  app/api/apex/chat/route.ts
 * 2. Copy lib/apex-client.ts to: lib/apex-client.ts
 * 3. Add to .env.local (never commit):
 *      APEX_BASE_URL=https://your-apex-host.example.com
 *      APEX_SECRET_KEY=apx_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
 * 4. (Optional) Replace the auth stub below with your real session check.
 *
 * ENVIRONMENT VARIABLES (.env.local, server-side only)
 * ──────────────────────────────────────────────────────
 *   APEX_BASE_URL    — e.g. https://proud-spiders-fly.loca.lt
 *   APEX_SECRET_KEY  — Apex API key from `manage_keys.py add`
 */

import { NextRequest, NextResponse } from "next/server";
import { getToken } from "next-auth/jwt";
import {
  APEX_BASE_URL,
  apexHeaders,
  safeMots,
  type ApexChatRequest,
  type ApexChatResponse,
} from "@/lib/apex-client";

// ── Auth guard ────────────────────────────────────────────────────────────────
// Uses next-auth JWT session. Requires NEXTAUTH_SECRET in .env.local.
// Falls back to APEX_ALLOW_ANON_PROXY=1 for local dev without auth.
async function getAuthenticatedUserId(req: NextRequest): Promise<string | null> {
  if (process.env.APEX_ALLOW_ANON_PROXY === "1") return "anon-proxy-user";
  try {
    const token = await getToken({ req, secret: process.env.NEXTAUTH_SECRET });
    if (!token) return null;
    return (token.sub ?? token.email ?? String(token.id)) as string;
  } catch {
    return null;
  }
}

// ── Route handler ─────────────────────────────────────────────────────────────

export async function POST(req: NextRequest): Promise<NextResponse> {
  // 1. Authenticate the Quill user.
  const userId = await getAuthenticatedUserId(req);
  if (!userId) {
    return NextResponse.json(
      {
        error: "Unauthorized",
        hint: "Wire your session auth in getAuthenticatedUserId() or set APEX_ALLOW_ANON_PROXY=1 for temporary testing.",
      },
      { status: 401 }
    );
  }

  // 2. Parse and validate client payload.
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  if (
    typeof body !== "object" ||
    body === null ||
    typeof (body as Record<string, unknown>).question !== "string"
  ) {
    return NextResponse.json(
      { error: "Missing required field: question (string)" },
      { status: 400 }
    );
  }

  const { question, mots_max } = body as Record<string, unknown>;
  const payload: ApexChatRequest = {
    question: String(question).slice(0, 4000), // enforce Apex's 4000-char limit
    mots_max: safeMots(mots_max),
  };

  // 3. Forward to Apex with server-side key injection.
  let apexRes: Response;
  try {
    apexRes = await fetch(`${APEX_BASE_URL}/chat`, {
      method: "POST",
      headers: apexHeaders(),
      body: JSON.stringify(payload),
      // Abort if Apex doesn't respond within 60 s (Next.js fetch supports this).
      signal: AbortSignal.timeout(60_000),
    });
  } catch (err) {
    console.error("[apex/chat] upstream fetch failed:", err);
    return NextResponse.json(
      {
        error: "Apex backend unreachable",
        hint: "Check APEX_BASE_URL, APEX_SECRET_KEY, and that Apex server is running.",
      },
      { status: 502 }
    );
  }

  // 4. Forward Apex's status code and body to the Quill client.
  const apexBody = await apexRes.json().catch(() => ({ error: "Apex returned non-JSON" }));

  // Attach the authenticated user ID for billing attribution in logs.
  if (apexRes.ok) {
    const typed = apexBody as ApexChatResponse;
    return NextResponse.json(
      { ...typed, _user: userId },
      { status: apexRes.status }
    );
  }

  return NextResponse.json(apexBody, { status: apexRes.status });
}
