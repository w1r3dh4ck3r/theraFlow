# TheraFlow — Architecture

## Overview

Single-process Python service. No frontend, no database. WhatsApp Cloud API → FastAPI webhook → ConversationEngine (in-memory) → Google Sheets + Telegram.

## Directory Structure

```
src/theraflow/
├── main.py                   # FastAPI app, lifespan handler, /health endpoint
├── config.py                 # Pydantic Settings (reads .env)
├── logging.py                # structlog configuration
├── utils.py                  # Utility helpers (mask_phone, etc.)
│
├── conversation/
│   ├── flow.py               # Step enum, StepConfig, STEP_CONFIGS, STEP_ORDER
│   └── engine.py             # ConversationEngine, UserSession, OutgoingMessage
│
├── whatsapp/
│   ├── webhook.py            # GET /webhook/whatsapp (verify) + POST (receive)
│   └── sender.py             # send_text_message, send_button_message, send_list_message
│
├── llm/
│   └── service.py            # OpenRouter API calls: generate_response, classify_answer, extract_greeting_info
│
├── safety/
│   ├── detector.py           # detect_risk() — HMAC-free keyword matching for crisis signals
│   └── responses.py          # CRISIS_MESSAGE_HIGH, CRISIS_MESSAGE_LOW constants
│
├── notifications/
│   └── telegram.py           # TelegramNotifier: send_lead_notification, send_handoff_alert, send_safety_alert
│
└── sheets/
    └── client.py             # SheetsClient, LeadData, FollowUpData, calculate_score, derive_intent

tests/
├── conftest.py               # Env setup + shared fixtures (engine, mock_sheets, mock_telegram, client)
├── test_conversation.py      # Unit tests for engine + flow logic
├── test_scenarios.py         # Realistic persona scenarios (hot lead, cold lead, price-first, spam)
├── test_e2e_simulation.py    # End-to-end ASGI client simulations
├── test_safety.py            # Safety detector unit tests
└── test_security_stress.py   # Security stress suite (72 scenarios, JSONL + markdown reports)
```

## WhatsApp Webhook Flow

```
Meta Platform
    │  POST /webhook/whatsapp
    │  X-Hub-Signature-256: sha256=<hmac>
    ▼
webhook.py: receive_webhook()
    1. Read raw bytes
    2. Verify HMAC-SHA256 (app_secret, raw_body) → 403 on mismatch
    3. Parse JSON
    4. Extract messages[] and contacts[] from nested entry.changes.value
    5. For each message:
       - type="text"        → dispatch(text=body)
       - type="interactive" → dispatch(button_payload=button_reply or list_reply)
       - other types        → ignore (logged at DEBUG)
    ▼
engine.handle_message(phone, name, text, button_payload)
    1. Safety check (detect_risk) on every message — high risk clears session
    2. New phone → create UserSession at Step.GREETING, return greeting prompt
    3. Existing session → resolve answer for current step
       a. Natural steps: LLM classify_answer() → fuzzy fallback
       b. Button/list steps: exact id/title/text match
    4. Special GREETING branch: LLM extract_greeting_info() → may skip WHO_FOR
    5. Advance step via next_step(current)
    6. CLOSING step → _on_conversation_complete() → Sheets write + Telegram notify
    ▼
sender.py
    - OutgoingMessage.is_button  → send_button_message() (max 3 buttons)
    - OutgoingMessage.is_list    → send_list_message()
    - plain text                 → send_text_message()
    ▼
Meta Cloud API (graph.facebook.com/v21.0/{phone_number_id}/messages)
```

## Conversation State Machine

Steps in order (STEP_ORDER):

| Step | Input type | Data key | Notes |
|------|-----------|----------|-------|
| GREETING | free text | — | LLM extracts name + optional who_for |
| WHO_FOR | natural (LLM) | who_for | Skipped if GREETING extracted it |
| GENDER | buttons (3) | gender | Fixed buttons, no LLM |
| FIRST_THERAPY | natural (LLM) | first_therapy | |
| TOPIC | list (6 options) | topic | LLM classifies with clinical term hints |
| URGENCY | natural (LLM) | urgency | |
| TERMS | natural (LLM) | terms_agreement | "não" → graceful decline, session ends |
| CLOSING | — | — | Terminal: writes lead, sends Telegram |
| HUMAN_HANDOFF | — | — | Terminal: triggered by handoff keywords |

Session limits: MAX_SESSIONS=1000, SESSION_TTL_SECONDS=1800 (lazy eviction on new session creation).

## LLM Integration

- Provider: OpenRouter (OpenAI-compatible API)
- Default model: `google/gemini-2.0-flash-001`
- Used for: prompt generation (`generate_response`), free-text classification (`classify_answer`), greeting info extraction (`extract_greeting_info`)
- Disabled by default (`LLM_ENABLED=false`). Always disabled in tests.
- All LLM calls have timeout + fallback to hardcoded prompts/fuzzy matching.

## Safety Layer

`safety/detector.py` runs on every inbound message before any session logic:
- HIGH risk (self-harm keywords) → crisis message + session cleared + Telegram safety alert
- MEDIUM risk (distress signals) → crisis message sent, session NOT cleared
- Exclusion phrases prevent false positives (e.g. "morrer de rir")

## Lead Persistence

`sheets/client.py` uses `gspread` with a Google Service Account. Writes are offloaded to a thread pool (gspread is sync). Two tabs: "Leads" (completed flows) and "Follow Up" (declined scheduling). Conversation turns are also logged per-row in a Conversations tab.

## Docker Setup

Two services in `docker-compose.yml`:

```
bot:
  build: . (multi-stage: python:3.13-slim builder + runtime)
  env_file: .env
  volumes: ./secrets:/run/secrets:ro
  restart: unless-stopped
  port: 8000 (internal only)

tunnel:
  image: cloudflare/cloudflared:latest
  command: tunnel --config /etc/cloudflared/config.yml run
  tunnel-config.yml: theraflow.w1r3d.dev → http://bot:8000
  depends_on: bot
  restart: unless-stopped
```

No host port binding — traffic reaches port 8000 only via the Cloudflare tunnel. The tunnel credential is mounted from `/home/mark/.cloudflared/99ef3355-f097-4a41-a7e2-a1099aa591d1.json`.
