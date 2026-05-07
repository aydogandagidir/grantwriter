# 05 — API Contracts

## 1. Konvansiyon

- **Base URL:** `https://api.bluedev.dev/api/v1`
- **Auth:** `Authorization: Bearer <supabase_jwt>` (header)
- **Content-Type:** `application/json` (default)
- **Date format:** ISO 8601 UTC (`2026-05-07T14:30:00Z`)
- **IDs:** UUIDv4 strings
- **Pagination:** cursor-based (`?cursor=<base64>&limit=20`)
- **Errors:** RFC 7807 Problem Details
- **Rate limit headers:** `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`
- **Versioning:** URL path (`/v1/`); breaking changes → `/v2/`

---

## 2. Error Response Schema

```json
{
  "type": "https://bluedev.dev/errors/validation-failed",
  "title": "Validation Failed",
  "status": 422,
  "detail": "The request body failed validation.",
  "instance": "/api/v1/proposals",
  "errors": [
    {
      "field": "title",
      "code": "required",
      "message": "Title is required"
    }
  ],
  "trace_id": "abc123def456"
}
```

**Standard error codes:**
| HTTP | Code | When |
|---|---|---|
| 400 | bad_request | malformed request |
| 401 | unauthorized | missing/invalid JWT |
| 403 | forbidden | RLS / role check fails |
| 404 | not_found | resource doesn't exist |
| 409 | conflict | duplicate resource |
| 422 | validation_failed | Pydantic validation error |
| 429 | rate_limited | rate limit hit |
| 500 | internal_error | unexpected |
| 503 | service_unavailable | upstream down |

---

## 3. Endpoints

### 3.1 Health & Meta

```http
GET /health
→ 200 {"status": "ok", "version": "1.0.0", "timestamp": "..."}

GET /api/v1/programmes
→ 200 [{ "id": "tubitak_1501", "name_tr": "...", "name_en": "...", ... }]

GET /api/v1/programmes/{id}/brief-schema
→ 200 { "fields": [...], "required": [...] }   // for dynamic form rendering
```

### 3.2 Calls

```http
GET /api/v1/calls
Query: programme_id, status, deadline_before, deadline_after, search, cursor, limit
→ 200 {
  "items": [
    {
      "id": "uuid",
      "programme": { "id": "horizon_eu_ria", "name_tr": "...", "name_en": "..." },
      "title": "Emerging digital technologies",
      "external_id": "HORIZON-CL4-2026-DIGITAL-EMERGING-01",
      "deadline": "2026-09-15",
      "budget_total_eur": 50000000,
      "budget_per_project_min_eur": 4000000,
      "budget_per_project_max_eur": 6000000,
      "trl_min": 4,
      "trl_max": 6,
      "topic_keywords": ["AI", "edge computing"],
      "status": "open",
      "language": "en"
    }
  ],
  "cursor": "next_cursor_string",
  "total": 47
}

GET /api/v1/calls/{id}
→ 200 {
  ...full call object including call_text, eligibility_summary
}

GET /api/v1/calls/{id}/eligibility-check
Query: tenant_id (implicit from JWT), TRL, country, entity_type
→ 200 {
  "eligible": true,
  "issues": [],
  "warnings": ["Your TRL (3) is below minimum (4)"]
}
```

### 3.3 Proposals (CRUD)

```http
POST /api/v1/proposals
Body: {
  "call_id": "uuid",          // optional
  "programme_id": "tubitak_1501",
  "language": "tr",
  "title": "Initial title (changeable)"
}
→ 201 { "id": "uuid", "status": "draft", ...}

GET /api/v1/proposals
Query: status, programme_id, search, cursor, limit
→ 200 { "items": [...], "cursor": "...", "total": ... }

GET /api/v1/proposals/{id}
→ 200 {
  "id": "uuid",
  "title": "Project ACME",
  "acronym": "ACME",
  "status": "draft_complete",
  "programme": {...},
  "call": {...},
  "brief": {...},
  "draft": {
    "excellence_md": "# Excellence\n...",
    "impact_md": "# Impact\n...",
    "implementation_md": "# Implementation\n..."
  },
  "budget": {...},
  "bibliography": [...],
  "compliance_report": {...},
  "distinctiveness_score": 0.7234,
  "word_count": 12453,
  "page_count": 38,
  "ai_disclosure_text": "...",
  "created_at": "...",
  "updated_at": "..."
}

PATCH /api/v1/proposals/{id}
Body: { "title": "...", "brief": {...}, "draft": {...} }
→ 200 { ...updated proposal }

DELETE /api/v1/proposals/{id}
→ 204 (soft delete: status='archived')
```

### 3.4 Generation (Long-running)

```http
POST /api/v1/proposals/{id}/generate
Body: {
  "agents": ["all"],          // or ["excellence_writer", "impact_writer"]
  "use_byok": false           // override tenant default
}
→ 202 {
  "job_id": "uuid",
  "estimated_duration_seconds": 1800,
  "status_url": "/api/v1/jobs/{job_id}",
  "stream_url": "/api/v1/proposals/{id}/stream"
}

GET /api/v1/jobs/{job_id}
→ 200 {
  "id": "uuid",
  "status": "running",         // queued | running | completed | failed
  "progress": 0.45,
  "current_step": "excellence_writer",
  "started_at": "...",
  "completed_at": null,
  "error": null
}

GET /api/v1/proposals/{id}/stream    (SSE)
→ event: agent_started
  data: {"agent": "call_analyst", "timestamp": "..."}
→ event: agent_progress
  data: {"agent": "excellence_writer", "tokens_streamed": 2400}
→ event: agent_completed
  data: {"agent": "excellence_writer", "duration_ms": 28000, "preview": "..."}
→ event: citation_verified
  data: {"citation_id": "uuid", "status": "verified", "doi": "10.xxxx/..."}
→ event: completed
  data: {"proposal_id": "uuid", "url": "/proposals/..."}
→ event: error
  data: {"agent": "excellence_writer", "error": "...", "recoverable": true}
```

### 3.5 Citations

```http
POST /api/v1/proposals/{id}/citations
Body: {
  "raw_text": "Smith et al. (2023). AI in healthcare. Nature, 12(3), 45-67.",
  "doi": null,                 // optional, system will try to find
  "title": "AI in healthcare",
  "authors": ["John Smith", "Jane Doe"],
  "year": 2023
}
→ 201 {
  "id": "uuid",
  "status": "verifying",
  ...
}

GET /api/v1/proposals/{id}/citations
→ 200 [{ ...citation }]

POST /api/v1/citations/{id}/verify
→ 200 {
  "status": "verified",
  "source": "crossref",
  "match_score": 0.94,
  "metadata": {...},
  "doi": "10.xxxx/yyy"
}

DELETE /api/v1/citations/{id}
→ 204
```

### 3.6 Compliance & Quality

```http
POST /api/v1/proposals/{id}/validate
→ 200 {
  "valid": false,
  "blockers": [
    {"type": "fabricated_citation", "count": 2, "ids": [...]},
    {"type": "page_limit_exceeded", "section": "excellence", "current": 12, "limit": 10}
  ],
  "warnings": [
    {"type": "distinctiveness", "score": 0.91, "message": "..."}
  ],
  "compliance_report": {
    "ai_disclosure": "ok",
    "dnsh": "ok",
    "gender_dimension": "missing",
    "page_limits": {...},
    "citations": {"verified": 45, "partial": 2, "fabricated": 1}
  }
}

GET /api/v1/proposals/{id}/distinctiveness
→ 200 {
  "score": 0.72,
  "level": "distinctive",
  "message": "...",
  "similar_projects": [
    {"acronym": "GREENBOT", "similarity": 0.72, "cordis_url": "..."}
  ]
}

GET /api/v1/proposals/{id}/ai-disclosure
→ 200 {
  "text": "## AI Tool Disclosure...",
  "tools_used": [...],
  "sources_used": [...],
  "limitations": "..."
}
```

### 3.7 Export

```http
POST /api/v1/proposals/{id}/export
Body: {
  "format": "docx",            // docx | pdf | xlsx (budget only)
  "template": "horizon_eu_ria" // optional, defaults to programme template
}
→ 202 {
  "export_id": "uuid",
  "status_url": "/api/v1/exports/{export_id}"
}

GET /api/v1/exports/{export_id}
→ 200 {
  "status": "completed",
  "download_url": "https://storage.../signed_url",
  "expires_at": "..."
}
```

### 3.8 RAG / Retrieval

```http
POST /api/v1/rag/search
Body: {
  "query": "AI for circular economy",
  "programme_id": "horizon_eu_ria",
  "section": "excellence",
  "k": 5
}
→ 200 [
  {
    "chunk_id": "uuid",
    "content": "...",
    "source_proposal": "GREENBOT (HORIZON-CL4-2024)",
    "section": "excellence",
    "similarity": 0.87
  }
]
```

### 3.9 Tenant & Users

```http
GET /api/v1/me
→ 200 { "user": {...}, "tenant": {...}, "plan": {...}, "usage": {...} }

PATCH /api/v1/me
Body: { "display_name": "...", "preferred_language": "tr" }
→ 200 {...}

GET /api/v1/tenant
→ 200 {...}

PATCH /api/v1/tenant   (admin only)
Body: { "name": "...", "billing_email": "..." }
→ 200 {...}

GET /api/v1/tenant/members
→ 200 [{...}]

POST /api/v1/tenant/invitations
Body: { "email": "...", "role": "member" }
→ 201 {...}

DELETE /api/v1/tenant/invitations/{id}
→ 204

PATCH /api/v1/tenant/members/{user_id}
Body: { "role": "admin" }
→ 200
```

### 3.10 LLM Config (BYOK)

```http
GET /api/v1/tenant/llm-config
→ 200 {
  "preferred_provider": "claude",
  "monthly_budget_usd": 200,
  "use_managed_keys": false,
  "anthropic_key_set": true,    // bool, not the actual key
  "openai_key_set": false
}

PUT /api/v1/tenant/llm-config
Body: {
  "anthropic_api_key": "sk-ant-...",  // encrypted server-side
  "preferred_provider": "claude"
}
→ 200 {...}

POST /api/v1/tenant/llm-config/test
Body: { "provider": "claude" }
→ 200 { "valid": true, "model": "claude-opus-4-7" }
```

### 3.11 Usage & Billing

```http
GET /api/v1/tenant/usage
Query: from, to
→ 200 {
  "period": {...},
  "total_proposals": 12,
  "total_llm_cost_usd": 45.23,
  "by_proposal": [...],
  "by_day": [...]
}

POST /api/v1/billing/checkout
Body: { "plan": "pro", "billing_period": "yearly" }
→ 200 { "checkout_url": "https://stripe.com/..." }

POST /api/v1/billing/portal
→ 200 { "portal_url": "https://stripe.com/..." }

POST /webhooks/stripe          (no auth, Stripe-Signature verified)
POST /webhooks/iyzico
```

### 3.12 Public (No Auth)

```http
GET /api/v1/calls/public
Query: programme_id, search
→ 200 [...subset of calls, public listing for marketing site]
```

---

## 4. SSE Stream Detail

### 4.1 Headers

```
Cache-Control: no-cache
Content-Type: text/event-stream
Connection: keep-alive
X-Accel-Buffering: no
```

### 4.2 Reconnection

```typescript
// Client-side resumption
const evt = new EventSource(`/api/v1/proposals/${id}/stream?last_event_id=${lastId}`);
evt.onmessage = (e) => { ... };
evt.onerror = () => { /* auto-reconnects */ };
```

### 4.3 Backend (FastAPI)

```python
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

@router.get("/proposals/{proposal_id}/stream")
async def stream_proposal(
    proposal_id: UUID,
    last_event_id: str | None = None,
    user: User = Depends(get_current_user),
):
    async def event_generator():
        # Subscribe to Redis Pub/Sub channel for this proposal
        async for event in redis_subscribe(f"proposal:{proposal_id}", since=last_event_id):
            yield {"event": event.type, "data": event.data, "id": event.id}

    return EventSourceResponse(event_generator())
```

---

## 5. Pydantic Schemas (Excerpt)

```python
# apps/api/src/api/schemas/proposal.py

from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from uuid import UUID
from typing import Literal

class ProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: UUID | None = None
    programme_id: str = Field(..., pattern=r"^[a-z_]+$")
    language: Literal["tr", "en"]
    title: str = Field(..., min_length=3, max_length=200)

class ProposalDraft(BaseModel):
    excellence_md: str = ""
    impact_md: str = ""
    implementation_md: str = ""
    summary: str = ""

class ProposalResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    title: str
    acronym: str | None
    status: Literal[
        "draft", "brief_complete", "generating", "draft_complete",
        "in_review", "validated", "exported", "submitted",
        "funded", "rejected", "archived"
    ]
    programme: ProgrammeRef
    call: CallRef | None
    brief: dict
    draft: ProposalDraft
    budget: dict
    bibliography: list[CitationRef]
    distinctiveness_score: float | None
    word_count: int
    page_count: int
    created_at: datetime
    updated_at: datetime
```

---

## 6. Auth Flow

```
1. User signs in via Supabase Auth (web)
   → Supabase returns JWT cookie

2. Frontend includes JWT in API requests
   → Authorization: Bearer <jwt>

3. FastAPI validates JWT
   → Decodes, extracts user_id (auth.uid())
   → Loads user from public.users
   → Sets request context

4. Route handlers use Depends(get_current_user)
   → User object available, RLS enforced by DB
```

```python
# apps/api/src/core/auth.py
async def get_current_user(
    authorization: str = Header(...),
    db: Session = Depends(get_db),
) -> User:
    token = authorization.replace("Bearer ", "")
    try:
        payload = jwt.decode(token, settings.SUPABASE_JWT_SECRET,
                            algorithms=["HS256"], audience="authenticated")
        user_id = UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(401, "Invalid token")

    user = await db.fetch_one("SELECT * FROM public.users WHERE id = $1", user_id)
    if not user:
        raise HTTPException(401, "User not found")
    return User.from_row(user)
```

---

## 7. Rate Limiting

```python
# Implemented via Redis sliding window
@router.post("/proposals/{id}/generate")
@rate_limit(key="user:{user_id}", max_requests=10, window_seconds=60)
async def generate_proposal(...):
    ...

# Plan-based limits enforced at service layer
# Starter: 3 proposals/month → checked in proposal_service.create()
```

---

## 8. CORS

```python
# Production
allow_origins = [
    "https://bluedev.dev",
    "https://app.bluedev.dev",
    "https://staging.bluedev.dev",
]

# Development
allow_origins = ["http://localhost:3000"]

allow_credentials = True
allow_methods = ["GET", "POST", "PATCH", "PUT", "DELETE"]
allow_headers = ["Authorization", "Content-Type"]
```

---

## 9. OpenAPI

- FastAPI auto-generates `/docs` (Swagger) ve `/redoc`
- Production'da auth gerektirir (admin only)
- TypeScript client otomatik üretilir: `openapi-typescript-codegen`
- CI'da contract test (frontend `apps/web` typecheck'i fail eder eğer API contract bozulursa)

---

## 10. Webhook Conventions

### Outgoing webhooks (future, Faz 2)
```http
POST <tenant_webhook_url>
X-Bluedev-Signature: hmac_sha256(payload, secret)
Body: {
  "event": "proposal.draft_complete",
  "tenant_id": "...",
  "proposal_id": "...",
  "timestamp": "..."
}
```

### Incoming webhooks
- Stripe: `/webhooks/stripe` (signature: `Stripe-Signature`)
- Iyzico: `/webhooks/iyzico` (signature: `X-IYZ-SIGNATURE-V3`)

---

**Sonraki dosya:** `06-agent-architecture.md` — 7 agent'ın detay tasarımı.