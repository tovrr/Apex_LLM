/**
 * quill-proxy/api/apex/stream/route.ts
 *
 * Next.js App Router — POST /api/apex/stream
 *
 * Receives a question from the Quill client, injects the Apex secret key
 * server-side, and pass-through streams the SSE response from Apex.
 *
 * The client reads Server-Sent Events in the standard format:
 *   data: {"type":"status","value":"loading","run_id":"..."}
 *   data: {"type":"delta","value":"token ","run_id":"..."}
 *   data: {"type":"done","run_id":"..."}
 *
 * HOW TO USE IN QUILL
 * ───────────────────
 * 1. Copy this file to:  app/api/apex/stream/route.ts
 * 2. Install the Edge runtime line only if running on Vercel Edge;
 *    remove it for Node.js runtime (default).
 * 3. On the client side, consume exactly as the Control Deck's app.js does —
 *    replace the direct Apex URL with /api/apex/stream.
 *
 * ENVIRONMENT VARIABLES (.env.local, server-side only)
 * ──────────────────────────────────────────────────────
 *   APEX_BASE_URL    — e.g. https://proud-spiders-fly.loca.lt
 *   APEX_SECRET_KEY  — Apex API key from `manage_keys.py add`
 */

import { NextRequest, NextResponse } from "next/server";
import { getToken } from "next-auth/jwt";
import { APEX_BASE_URL, apexHeaders, safeMots } from "@/lib/apex-client";

// ── Auth guard (same pattern as chat/route.ts) ────────────────────────────────
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

export async function POST(req: NextRequest): Promise<NextResponse | Response> {
  // 1. Authenticate.
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

  // 2. Parse payload.
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
  const payload = {
    question: String(question).slice(0, 4000),
    mots_max: safeMots(mots_max),
  };

  // 3. Open streaming request to Apex.
  let apexRes: Response;
  try {
    apexRes = await fetch(`${APEX_BASE_URL}/chat/stream`, {
      method: "POST",
      headers: apexHeaders(),
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(90_000),
    });
  } catch (err) {
    console.error("[apex/stream] upstream fetch failed:", err);
    return NextResponse.json(
      {
        error: "Apex backend unreachable",
        hint: "Check APEX_BASE_URL, APEX_SECRET_KEY, and that Apex server is running.",
      },
      { status: 502 }
    );
  }

  if (!apexRes.ok || !apexRes.body) {
    const errBody = await apexRes.json().catch(() => ({ error: "Apex error" }));
    return NextResponse.json(errBody, { status: apexRes.status });
  }

  // 4. Pass the SSE stream straight through to the Quill client.
  //    We inject an extra "x-user-id" event at the start for server-side logging.
  const upstreamBody = apexRes.body;

  const proxiedStream = new ReadableStream({
    async start(controller) {
      // Inject user attribution as a comment line (invisible to SSE parsers).
      controller.enqueue(
        new TextEncoder().encode(`: user=${userId}\n\n`)
      );

      const reader = upstreamBody.getReader();
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          controller.enqueue(value);
        }
      } catch (err) {
        console.error("[apex/stream] stream read error:", err);
      } finally {
        controller.close();
        reader.releaseLock();
      }
    },
  });

  return new Response(proxiedStream, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      // Allow the Quill browser client to read the response.
      "Access-Control-Allow-Origin": "*",
    },
  });
}
