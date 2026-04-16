# Freemium Integration: Apex + quill-proxy

## Overview

Apex now enforces **tier-based rate limiting** at the API key level. To integrate this into Quill, you need to:

1. **In Apex (apex-llm repo):** ✅ Already done
   - Freemium quotas per API key (free/pro/internal tiers)
   - HTTP 429 response when quota exceeded

2. **In Quill (quill-proxy repo):** You need to implement
   - Select tier-specific Apex key based on user subscription
   - Handle HTTP 429 gracefully (show "Upgrade to Pro" message)
   - (Optional) Track per-user usage for analytics

---

## Architecture

### Current Setup
```
Quill Client → quill-proxy (single APEX_SECRET_KEY) → Apex
                                ↓
                    All users (free + pro) share
                    one API key's quotas
```

### New Setup (Tier-Aware)
```
Quill Client → quill-proxy
                   ↓
           Check user tier from JWT
                   ↓
           Free user? → Use APEX_KEY_FREE_TIER
           Pro user?  → Use APEX_KEY_PRO_TIER
                   ↓
                Apex (enforces quotas per key)
                   ↓
           Quota OK? → Process request
           Quota exceeded? → HTTP 429
```

---

## Step 1: Copy Tier-Specific Keys to Quill

In **Apex-llm**, three keys have been created:

| Tier | Key | Requests/day | Tokens/day |
|------|-----|--------------|------------|
| Free | `apx_4f6421b270f8b17cef41555938b58264` | 10 | 5,000 |
| Pro | `apx_493f177847c00cd899741e5a2dbc40cb` | 1,000 | 500,000 |
| Internal | `apx_33c15a1be8a8a8b94eaf6e9032ccb843` | ∞ | ∞ |

Add these to your **quill-proxy/.env.local**:

```bash
APEX_BASE_URL=https://your-apex-railway-app.railway.app
APEX_KEY_FREE_TIER=apx_4f6421b270f8b17cef41555938b58264
APEX_KEY_PRO_TIER=apx_493f177847c00cd899741e5a2dbc40cb
APEX_KEY_INTERNAL=apx_33c15a1be8a8a8b94eaf6e9032ccb843
```

---

## Step 2: Modify quill-proxy Routes

### Update `/api/apex/chat/route.ts`

Add a function to get the user's tier from next-auth:

```typescript
async function getUserTier(req: NextRequest): Promise<'free' | 'pro' | 'internal'> {
  // Get the next-auth JWT token
  const token = await getToken({ req, secret: process.env.NEXTAUTH_SECRET });
  
  // Extract tier from token claims (depends on your auth schema)
  // Common patterns:
  //   token.tier, token.subscriptionTier, token.plan
  // Fallback to 'free' for unauthenticated
  
  const tier = (token?.tier ?? 'free') as string;
  if (tier === 'pro' || tier === 'premium') return 'pro';
  if (tier === 'internal') return 'internal';
  return 'free';
}

function getApexKey(tier: 'free' | 'pro' | 'internal'): string {
  const keys: Record<string, string> = {
    free: process.env.APEX_KEY_FREE_TIER!,
    pro: process.env.APEX_KEY_PRO_TIER!,
    internal: process.env.APEX_KEY_INTERNAL!,
  };
  return keys[tier];
}
```

Then in your `POST` handler, before calling Apex:

```typescript
export async function POST(req: NextRequest): Promise<NextResponse> {
  const userId = await getAuthenticatedUserId(req);
  if (!userId) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  // ← Add this:
  const userTier = await getUserTier(req);
  const apexKey = getApexKey(userTier);

  // Parse payload...
  let body = await req.json();
  const payload: ApexChatRequest = { ... };

  // Fetch from Apex with tier-specific key:
  let apexRes: Response;
  try {
    apexRes = await fetch(`${APEX_BASE_URL}/chat`, {
      method: "POST",
      headers: {
        "X-API-Key": apexKey,  // ← Use tier-specific key
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(60_000),
    });
  } catch (err) {
    console.error("[apex/chat] upstream fetch failed:", err);
    return NextResponse.json(
      { error: "Apex backend unreachable" },
      { status: 502 }
    );
  }

  // ← Add this: Handle HTTP 429 quota exceeded
  if (apexRes.status === 429) {
    const body = await apexRes.json().catch(() => ({}));
    return NextResponse.json(
      {
        error: "Daily limit reached",
        message: 
          userTier === 'free'
            ? "You've used your 10 free requests today. Upgrade to Pro for unlimited access."
            : "You've reached your monthly token limit. Contact support for higher limits.",
        hint: body.reason, // Apex's detailed quota reason
      },
      { status: 429 }
    );
  }

  // Otherwise forward Apex's response as normal...
  const apexBody = await apexRes.json().catch(() => ({ error: "Apex returned non-JSON" }));
  if (apexRes.ok) {
    return NextResponse.json(
      { ...apexBody, _user: userId },
      { status: apexRes.status }
    );
  }
  return NextResponse.json(apexBody, { status: apexRes.status });
}
```

### Update `/api/apex/stream/route.ts`

Apply the same logic:

```typescript
const userTier = await getUserTier(req);
const apexKey = getApexKey(userTier);

const apexRes = await fetch(`${APEX_BASE_URL}/chat/stream`, {
  method: "POST",
  headers: {
    "X-API-Key": apexKey,  // ← Use tier-specific key
    "Content-Type": "application/json",
  },
  body: JSON.stringify(payload),
  signal: AbortSignal.timeout(60_000),
});

// Handle 429:
if (apexRes.status === 429) {
  // Either return 429 to client or fall back to non-streaming response
  return new Response(
    JSON.stringify({
      error: "Daily limit reached",
      message: "You've used your daily request limit. Upgrade to Pro.",
    }),
    { status: 429, headers: { "Content-Type": "application/json" } }
  );
}

// Stream response as normal...
```

---

## Step 3: Optional - Track Per-User Usage

If you want **per-user fairness** (not just per-tier pools), you can add a lightweight usage tracker in Quill:

```typescript
// In a shared utilities file:
interface UserUsage {
  userId: string;
  date: string; // YYYY-MM-DD
  requestsUsed: number;
  tokensUsed: number;
}

export async function checkUserQuota(
  userId: string,
  tier: 'free' | 'pro',
  tokensThisRequest: number
): Promise<{ allowed: boolean; reason?: string }> {
  // Query Quill's own DB (Postgres, Firestore, etc.)
  // against per-user limits, not per-tier limits
  
  // Example limits:
  const limits = {
    free: { requests: 10, tokens: 5000 },
    pro: { requests: 1000, tokens: 500000 },
  };
  
  const { requests: reqLimit, tokens: tokenLimit } = limits[tier];
  
  // Check if user exceeded limits today
  const usage = await getUsageForToday(userId);
  
  if (usage.requestsUsed >= reqLimit) {
    return { 
      allowed: false, 
      reason: `Exceeded request limit: ${usage.requestsUsed}/${reqLimit}` 
    };
  }
  
  if (usage.tokensUsed + tokensThisRequest > tokenLimit) {
    return {
      allowed: false,
      reason: `Exceeded token limit: ${usage.tokensUsed + tokensThisRequest}/${tokenLimit}`
    };
  }
  
  return { allowed: true };
}
```

**But this is optional.** Apex's per-key quotas already provide a safety net.

---

## Step 4: Testing

### Test 1: Free user quota

```bash
# Create a free test user, make 11 requests
# Request 11 should return HTTP 429

# In Quill client:
for i in {1..11}; do
  curl -X POST http://localhost:3000/api/apex/chat \
    -H "Authorization: Bearer $FREE_USER_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"question\": \"Test $i\"}"
done

# Response 11:
# {
#   "error": "Daily limit reached",
#   "message": "You've used your 10 free requests today. Upgrade to Pro for unlimited access.",
#   "hint": "exceeded_request_quota_10/10"
# }
```

### Test 2: Pro user no quota

```bash
# Create a pro test user, make 100 requests
# Should all succeed (1000 req/day limit)
```

### Test 3: Monitor usage

```bash
# In Apex-llm repo:
sqlite3 key_store.db "SELECT key_label, date, requests_used, tokens_used FROM usage_daily ORDER BY date DESC LIMIT 20;"

# Example output:
# quill-free-tier|2026-04-16|10|3500
# quill-pro-tier|2026-04-16|450|250000
# quill-internal|2026-04-16|0|0
```

---

## Quota Reset Schedule

- **When:** Every day at **00:00 UTC** (not user local time)
- **Automatic:** Yes, Apex handles it
- **Timezone:** UTC only (no per-timezone reset)

---

## Error Handling

### HTTP 429: Quota Exceeded

Apex returns this with a reason. Example:

```json
{
  "error": "Quota dépassé pour le plan free",
  "reason": "exceeded_request_quota_10/10",
  "usage": {
    "requests_used": 10,
    "requests_limit": 10,
    "tokens_used": 4800,
    "tokens_limit": 5000
  }
}
```

**In quill-proxy**, catch 429 and show user-friendly message:

```typescript
if (apexRes.status === 429) {
  const { usage } = await apexRes.json();
  const message = usage.tokens_used >= usage.tokens_limit
    ? "You've used your daily token budget. Come back tomorrow or upgrade to Pro."
    : "You've used your daily request limit. Come back tomorrow or upgrade to Pro.";
  
  return NextResponse.json(
    { error: "Quota exceeded", message },
    { status: 429 }
  );
}
```

---

## Next Steps

1. **In quill-proxy:** Implement `getUserTier()` and `getApexKey()` functions
2. **In quill-proxy:** Update both `/api/apex/chat` and `/api/apex/stream` routes
3. **In quill-proxy/.env.local:** Add the three tier-specific keys
4. **Test:** Verify free users hit limit at 10 requests, pro users at 1,000 requests
5. **Monitor:** Check `usage_daily` table in Apex's `key_store.db` to see tier usage

---

## FAQ

**Q: Does this limit open source users who self-host Apex?**  
A: No. Self-hosted users create their own API keys with the `internal` plan (unlimited quota). The freemium limits only apply to SaaS deployments like Quill, where you distribute keys to users. If you self-host, you have full control.

**Q: Can a user switch tiers mid-day?**  
A: Yes. Their next request will use the new tier's key. Previous requests count toward the old tier's quota.

**Q: What if both Apex and quill-proxy have quota trackers?**  
A: Apex's quotas are the hard limit (safety net). quill-proxy quotas are optional (fairness). Apex wins if there's a conflict.

**Q: How do I rotate keys?**  
A: Run `python manage_keys.py list` and `deactivate` to retire old keys. Create new ones with `add`. Update quill-proxy .env.

**Q: Can I give a user a custom quota?**  
A: For now, no. Tiers are fixed (free/pro/internal). For future: add a `custom` tier or reach out to extend this.

---

## References

- **Apex documentation:** See [FREEMIUM_MODEL.md](./FREEMIUM_MODEL.md)
- **API keys:** `manage_keys.py` in Apex-llm repo
- **Quotas table:** `key_store.db` → `usage_daily` table
- **Production deployment:** Railway (set APEX_BASE_URL to Railway app URL)
