# Freemium Model for Apex

## Tier Structure

### Free Tier
- **Cost:** $0
- **Requests/day:** 10
- **Tokens/day:** 5,000 (~25 short conversations)
- **Model access:** `fast` tier only (lightweight)
- **Streaming:** No

**Ideal for:** Learning, casual use, testing.

### Pro Tier
- **Cost:** $5/month (suggested)
- **Requests/day:** 1,000
- **Tokens/day:** 500,000 (~2,500 conversations)
- **Model access:** All tiers (fast/default/reasoning)
- **Streaming:** Yes
- **Priority:** Standard queue

**Ideal for:** Active developers, teams, production integrations.

### Internal Tier
- **Cost:** Free (internal use only)
- **Requests/day:** Unlimited
- **Tokens/day:** Unlimited
- **Model access:** All tiers
- **Streaming:** Yes
- **Priority:** Highest

**Ideal for:** Development, testing, internal tools.

---

## How It Works

### 1. **Daily Quotas (UTC midnight)**
- Resets every 24 hours at 00:00 UTC
- Tracked in SQLite `usage_daily` table
- One row per (key_hash, date)

### 2. **Quota Check Flow**
```
User makes request
    ↓
API key verified (is_active? valid?)
    ↓
Freemium tier checked (free/pro/internal?)
    ↓
Daily quota checked:
  - requests_used >= requests_limit?
  - tokens_used + tokens_request > tokens_limit?
    ↓
If quota exceeded → HTTP 429 "Quota exceeded"
If quota OK → Process request
    ↓
Record usage (requests_used++, tokens_used+= tokens)
```

### 3. **Token Estimation**
Tokens counted as: `len(question.split()) + mots_max`

Example:
- Question: "Debug my API" (3 words)
- Max output: 200 tokens
- **Total: 203 tokens counted**

---

## Implementation

### API Changes
- `/chat` endpoint now returns **HTTP 429** if quota exceeded
- Error message: `"Quota dépassé pour le plan pro: exceeded_token_quota_5100/5000. Upgrade ou attendez demain pour un renouvellement."`

### Database Schema
```sql
CREATE TABLE usage_daily (
    key_hash TEXT,
    date TEXT (YYYY-MM-DD),
    requests_used INTEGER,
    tokens_used INTEGER,
    PRIMARY KEY (key_hash, date)
);
```

### Creating Keys with Plans

```bash
# Free tier key (10 requests, 5k tokens/day)
python manage_keys.py add --label "my-user-free" --plan free

# Pro tier key (1000 requests, 500k tokens/day)
python manage_keys.py add --label "my-org-pro" --plan pro

# Internal key (unlimited)
python manage_keys.py add --label "internal-dev" --plan internal
```

---

## Quota Reset

- **When:** Every UTC midnight (00:00 UTC)
- **Automatic:** Yes, no action needed
- **Timezone:** UTC (not user's local timezone)

---

## Monetization Path

### Phase 1 (Now)
- Free tier: 10 requests/day, 5k tokens
- Pro tier: 1,000 requests/day, 500k tokens
- **Cost:** Free + Premium pricing (to be decided)

### Phase 2 (When 50+ users)
- Add usage-based billing
- Integrate Stripe Billing Meters (already schema-ready in key_store.py)
- Per-token pricing (e.g., $0.0001 per 1k tokens)

### Phase 3 (Mature)
- Team plans
- Custom quotas
- SLA guarantees

---

## Examples

### Example 1: Free tier user hits limit
```
User makes request #11 of the day
API returns:
  HTTP 429
  "Quota dépassé pour le plan free: exceeded_request_quota_10/10. 
   Upgrade ou attendez demain pour un renouvellement."
```

### Example 2: Pro tier user hits token limit
```
User has used 450k tokens today.
Makes new request: 300 tokens estimated.
Total would be: 450,300 tokens (exceeds 500k limit)

API returns:
  HTTP 429
  "Quota dépassé pour le plan pro: exceeded_token_quota_450300/500000. 
   Upgrade ou attendez demain pour un renouvellement."
```

### Example 3: Free tier user within quota
```
User has made 5 requests, 2,000 tokens today.
Makes new request: 500 tokens estimated.

Quota check:
  - requests: 5 < 10 ✅
  - tokens: 2,000 + 500 = 2,500 < 5,000 ✅

Request proceeds normally.
Usage incremented: requests=6, tokens=2,500.
```

---

## Testing

```bash
# Create a free tier key
python manage_keys.py add --label test-free --plan free

# Export for testing
export APEX_API_KEY=apx_xxxx...

# Make 11 requests with curl
for i in {1..11}; do
  curl -X POST http://localhost:8000/chat \
    -H "X-API-Key: $APEX_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"question\": \"Hello $i\", \"mots_max\": 10}"
done

# Request 11 should return HTTP 429
```

---

## Disabling Freemium (for testing)

If you want to bypass quotas temporarily (for testing):
```python
# In serveur_api.py, comment out the freemium check:
# quota_check = _check_freemium_quota(...)
# if not quota_check["allowed"]: ...
```

---

## Limits Summary

| Metric | Free | Pro | Internal |
|--------|------|-----|----------|
| Requests/day | 10 | 1,000 | ∞ |
| Tokens/day | 5,000 | 500,000 | ∞ |
| Tiers available | fast | all | all |
| Streaming | No | Yes | Yes |
| Cost | Free | $5/mo | Free |

---

## Upgrade Flow (Quill UI)

1. User hits quota → see modal: "Upgrade to Pro"
2. Click "Upgrade" → Stripe checkout
3. Complete payment → Pro key issued
4. Quota resets tomorrow
5. Continue using Apex with new limits

---

## Future: Stripe Integration

When you wire Stripe Billing Meters:
```python
# Each recorded usage event generates a Stripe meter event
stripe.billing_meter_event.create(
    event_name="apex_tokens_used",
    payload={
        "value": tokens_used,
        "stripe_customer_id": customer_id,
        "idempotency_key": event_id
    }
)
```

This is already schema-ready in `key_store.py` (usage_events table).
