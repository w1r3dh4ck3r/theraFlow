# TheraFlow — Dev Workflow

## Prerequisites

- `uv` installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Docker + Docker Compose installed
- A `.env` file in the project root (copy from `.env.example`)
- Google service account JSON at `./secrets/service-account.json`

## Local Development (without Docker)

```bash
# Install dependencies
uv sync

# Run the dev server (hot reload, listens on port 8000)
uv run uvicorn theraflow.main:app --reload --host 0.0.0.0 --port 8000

# Health check
curl http://localhost:8000/health
```

The app reads `.env` from the current working directory via pydantic-settings. Make sure `.env` is present and all required vars are set before starting.

Note: Without the Cloudflare tunnel, Meta's webhook cannot reach localhost. For local webhook testing, set up a tunnel manually (e.g. `cloudflared tunnel --url http://localhost:8000`) and update the webhook URL in the Meta App Dashboard.

## Running Tests

```bash
# All tests (LLM always disabled in test mode via conftest.py)
uv run pytest

# Specific file
uv run pytest tests/test_scenarios.py -v

# With output on failure
uv run pytest --tb=long

# Security stress suite (produces reports in tests/reports/)
uv run pytest tests/test_security_stress.py -v
```

Test files:
- `test_conversation.py` — unit tests: engine logic, step transitions, session management
- `test_scenarios.py` — persona scenarios: hot lead, cold/hesitant, price-first, spam
- `test_e2e_simulation.py` — ASGI client simulations of complete flows
- `test_safety.py` — safety detector: high/medium risk, exclusion phrases
- `test_security_stress.py` — 72 scenarios covering injection, prompt attacks, edge cases; writes JSONL + markdown to `tests/reports/`

## Linting

```bash
uv run ruff check src tests
uv run ruff format src tests
```

## Docker: Build & Run

```bash
# Build and start (bot + tunnel)
docker compose up -d --build

# Rebuild only the bot (after code changes)
docker compose up -d --build bot

# View bot logs
docker compose logs -f bot

# View tunnel logs
docker compose logs -f tunnel

# Stop everything
docker compose down
```

The compose file does NOT bind any host port. Traffic only enters via the Cloudflare tunnel at `theraflow.w1r3d.dev`.

## Deploy Process

TheraFlow runs on the dedicated Linux server (srv1439433). There is no CI/CD pipeline — deploys are manual.

```bash
# On the server, from the project directory
cd ~/AI/projects/theraFlow

# Pull latest code
git pull

# Rebuild and restart the bot container
# IMPORTANT: use --force-recreate to reload .env changes (restart alone won't)
docker compose up -d --build --force-recreate bot
```

Use `--force-recreate` any time `.env` has changed — `docker compose restart` does not reload environment variables.

## Updating the Conversation Flow

1. Edit `src/theraflow/conversation/flow.py`:
   - `STEP_CONFIGS` — prompts, options, button titles
   - `STEP_ORDER` — linear step sequence
2. Edit `src/theraflow/conversation/engine.py` if branching logic changes
3. Run tests: `uv run pytest`
4. Rebuild Docker: `docker compose up -d --build bot`

## Checking Leads

Leads are written to the Google Spreadsheet configured via `GOOGLE_SHEETS_ID`. Two tabs:
- **Leads** — completed qualification flows
- **Follow Up** — contacts who declined scheduling
- **Conversations** — per-turn log of all messages

## Webhook Configuration (Meta Dashboard)

When the tunnel URL changes or on first setup:

1. Go to Meta for Developers → Your App → WhatsApp → Configuration
2. Set Webhook URL to: `https://theraflow.w1r3d.dev/webhook/whatsapp`
3. Set Verify Token to match `WHATSAPP_VERIFY_TOKEN` in `.env`
4. Subscribe to the `messages` field

## Enabling LLM Mode

By default, `LLM_ENABLED=false` and the bot uses hardcoded Portuguese prompts with fuzzy matching. To enable LLM-generated natural responses:

```env
LLM_ENABLED=true
OPENROUTER_API_KEY=sk-or-...
LLM_MODEL=google/gemini-2.0-flash-001
LLM_TIMEOUT_SECS=10
```

Rebuild the container after changing `.env`:
```bash
docker compose up -d --force-recreate bot
```

## Troubleshooting

| Symptom | Likely cause |
|---------|-------------|
| All webhooks return 403 | `WHATSAPP_APP_SECRET` mismatch |
| App fails to start | Missing required env var — check logs |
| Leads not appearing in Sheets | Service account JSON missing or wrong path; check `GOOGLE_SERVICE_ACCOUNT_JSON` |
| Tunnel not routing | Check `docker compose logs tunnel`; verify Cloudflare tunnel credentials file exists at `/home/mark/.cloudflared/99ef3355-f097-4a41-a7e2-a1099aa591d1.json` |
| Sessions lost after restart | Expected — sessions are in-memory only |
| LLM not generating natural responses | `LLM_ENABLED` is false, or `OPENROUTER_API_KEY` is empty |
