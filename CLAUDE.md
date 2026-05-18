# TheraFlow — AI Instructions

## Project Summary

WhatsApp lead-qualification bot for therapy practices (currently deployed for Karoline Jangola). A Python FastAPI service that conducts a 7-step intake conversation over WhatsApp Cloud API, scores leads, persists them to Google Sheets, and sends Telegram notifications.

- **Live URL**: https://theraflow.w1r3d.dev
- **Status**: Production, actively maintained
- **Pivoting toward**: general-purpose WhatsApp bot platform (configurable per tenant)

## Architecture in One Paragraph

Single FastAPI process. WhatsApp webhook receives messages, verifies HMAC-SHA256 signature, dispatches to `ConversationEngine`. The engine holds in-memory sessions (max 1000, 30-min TTL) keyed by phone number. Each session walks a `Step` enum through `STEP_CONFIGS` in `conversation/flow.py`. LLM (OpenRouter / Gemini Flash) optionally generates natural-language prompts and classifies free-text answers; falls back to hardcoded prompts + fuzzy matching when LLM is off. On completion: lead written to Google Sheets + Telegram notification sent.

## Critical Gotchas

- **No frontend**: This project is Python-only (FastAPI + uvicorn). There is no React/Vite/TypeScript frontend — ignore any context suggesting otherwise.
- **Sessions are in-memory**: Restarting the container drops all active conversations. No database.
- **Google Sheets is optional**: If `GOOGLE_SERVICE_ACCOUNT_JSON` path is wrong or file is missing, the app starts anyway — leads just won't persist. Check logs for `SheetsClient` errors.
- **LLM is off by default**: `LLM_ENABLED=false`. Tests always set it false explicitly (`conftest.py` line: `os.environ["LLM_ENABLED"] = "false"`). Enable with `LLM_ENABLED=true` + `OPENROUTER_API_KEY=...` in `.env`.
- **Webhook signature**: Meta signs with HMAC-SHA256 using `WHATSAPP_APP_SECRET`. Missing or wrong secret → all webhooks 403.
- **Service account JSON path**: In Docker, the file is bind-mounted at `./secrets/` → `/run/secrets/`. Set `GOOGLE_SERVICE_ACCOUNT_JSON=/run/secrets/service-account.json` in `.env`.
- **Port**: Container exposes 8000, not 8001. Cloudflare tunnel proxies `theraflow.w1r3d.dev` → `http://bot:8000`.
- **Step ordering is linear**: `STEP_ORDER` in `flow.py` is the canonical sequence. The GREETING step has a special branch — if the user supplies `who_for` in their greeting message, WHO_FOR is skipped.
- **TERMS decline ends the flow**: If the user answers "não" at the TERMS step, a graceful decline message is returned and the session is cleaned up (lead is NOT written to Sheets).
- **Handoff detection**: Any message containing keywords like "falar com alguém", "atendente", "humano" triggers a Telegram handoff alert and terminates the session.

## When Modifying the Conversation Flow

- Edit `src/theraflow/conversation/flow.py` for step prompts, options, and ordering.
- Edit `src/theraflow/conversation/engine.py` for branching logic, LLM calls, and session handling.
- `StepConfig.natural=True` means the step accepts free text and uses LLM classification — do not add buttons to natural steps.
- `StepConfig.use_buttons` returns True only when `len(options) <= 3` (WhatsApp limit).
- More than 3 options → list message format automatically.

## Test Suite

- Tests live in `tests/`. Run with `uv run pytest` (LLM always disabled in tests).
- `conftest.py` sets all required env vars before any import.
- Mock fixtures: `mock_sheets` (AsyncMock), `mock_telegram` (AsyncMock), `engine`, `test_app`, `client`.
- 5 test files: `test_conversation.py`, `test_scenarios.py`, `test_e2e_simulation.py`, `test_safety.py`, `test_security_stress.py`.

## Code Style

- Python 3.13, typed, Ruff for linting (`uv run ruff check src tests`).
- `structlog` for all logging — always use `log.info("event_name", key=value)` not f-strings.
- Async throughout. Sync gspread calls are wrapped in `asyncio.to_thread` in `sheets/client.py`.
