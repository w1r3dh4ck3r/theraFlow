# TheraFlow — Stack

## Runtime

| Component | Version / Detail |
|-----------|-----------------|
| Python | 3.13 (requires `>=3.13` in pyproject.toml) |
| FastAPI | `>=0.115.0` |
| Uvicorn | `>=0.32.0` (with standard extras: websockets, httptools) |
| httpx | `>=0.28.0` (async HTTP client for WhatsApp API + LLM calls) |
| pydantic-settings | `>=2.7.0` (env var / .env loading) |
| structlog | `>=24.4.0` (structured logging) |
| python-dotenv | `>=1.0.1` |
| gspread | `>=6.1.0` (Google Sheets access via service account) |

## Dev / Test

| Tool | Version |
|------|---------|
| uv | latest (package manager, replaces pip/venv) |
| pytest | `>=8.3.0` |
| pytest-asyncio | `>=0.24.0` (asyncio_mode = "auto") |
| ruff | `>=0.8.0` (linting, target python 3.13) |

## External Services

| Service | Purpose | Config |
|---------|---------|--------|
| Meta WhatsApp Cloud API | Inbound webhook + outbound messaging | `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET` |
| OpenRouter | LLM inference (optional) | `OPENROUTER_API_KEY`, `LLM_MODEL` (default: `google/gemini-2.0-flash-001`), `LLM_TIMEOUT_SECS` (default: 10), `LLM_ENABLED` (default: false) |
| Google Sheets | Lead persistence + conversation log | `GOOGLE_SHEETS_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON` (path to service account key file) |
| Telegram Bot API | Lead notifications + handoff/safety alerts | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Cloudflare Tunnel | Exposes bot to internet | Tunnel ID: `99ef3355-f097-4a41-a7e2-a1099aa591d1`, config at `tunnel-config.yml` |

## Infrastructure

- **Host**: srv1439433 (187.77.225.44)
- **Container runtime**: Docker Compose
- **Build**: Multi-stage Docker build (python:3.13-slim builder → runtime)
- **Package manager**: `uv` (used inside Dockerfile via `uv pip install`)
- **Venv**: `/app/.venv` inside the container
- **User**: Non-root (`theraflow` system user in container)
- **Port**: 8000 (internal only, no host binding)
- **Domain**: `theraflow.w1r3d.dev` via Cloudflare Tunnel (compose service: `tunnel`)
- **Secrets mount**: `./secrets/` → `/run/secrets/` (read-only bind mount)

## Environment Variables Reference

Required (app fails to start if missing):
```
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_APP_SECRET=
GOOGLE_SERVICE_ACCOUNT_JSON=/run/secrets/service-account.json
GOOGLE_SHEETS_ID=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Optional:
```
SCHEDULING_LINK=https://exemplo.com/agendar
OPENROUTER_API_KEY=
LLM_MODEL=google/gemini-2.0-flash-001
LLM_TIMEOUT_SECS=10
LLM_ENABLED=false
```

## Build System

- `pyproject.toml` with `hatchling` backend
- Package path: `src/theraflow` (src layout)
- `uv.lock` for reproducible installs
