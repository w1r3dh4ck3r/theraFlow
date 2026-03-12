# TheraFlow AI Upgrade — Amended Plan

## Decision Log

| Spec item | Decision | Reason |
|---|---|---|
| Node.js/TypeScript | **Keep Python/FastAPI** | Already working, no reason to rewrite |
| OpenAI Responses API | **Gemini 2.5 Flash** | 5x cheaper, free tier, good PT-BR |
| PostgreSQL + Redis | **Keep Sheets + in-memory** | Sufficient for current scale |
| Admin panel | **Defer** | Sheets IS the panel |
| CRM integrations | **Defer** | Sheets only |
| A/B prompt testing | **Defer** | Log prompt version for future use |
| Dual-model (cheap + strong) | **Single model + scripted fallback** | Gemini 2.5 Flash is cheap enough for all turns |
| DeepSeek | **Rejected** | 7.5s latency, disqualified |

## LLM Choice

**Primary: Gemini 2.5 Flash** — ~$0.007/conversation
- Excellent PT-BR, native JSON mode, built-in safety filters
- Free tier for dev/testing via AI Studio
- Sub-2s latency
- ~$65/month at 10k conversations

**Fallback (on LLM failure): scripted responses** — the current bot flow, zero cost

## Phases

### Phase 0 — Housekeeping
- Fix stale tests vs flow.py (7 steps vs 14)
- Fix session TTL (created_at → last_activity_at)
- Add LLM config to Settings

### Phase 1 — LLM Integration
- `llm/service.py` — Gemini 2.5 Flash, async, 10s timeout
- `llm/prompts.py` — versioned system prompt, PT-BR persona, output schema
- `llm/parser.py` — Pydantic model for structured JSON response
- Wire into engine: LLM generates natural reply text, rules still enforce required fields

### Phase 2 — Safety Layer
- `safety/detector.py` — deterministic PT-BR keywords first, LLM risk_level second
- `safety/responses.py` — CVV (188), SAMU (192), empathetic fixed messages
- Insert before LLM call in handle_message()

### Phase 3 — Handoff + Scoring
- Extend LeadData with intent, risk_level, lead_quality, confidence
- Multi-axis scoring (pain demonstrated, contact collected, scheduling intent, etc.)
- Priority Telegram alerts for hot leads and crisis flags

### Phase 4 — Tests + Polish
- Safety tests (crisis keywords, false positives on "estou ansiosa")
- Conversation scenario tests (hot, cold, spam, price-first, crisis)
- Verify existing tests still pass
