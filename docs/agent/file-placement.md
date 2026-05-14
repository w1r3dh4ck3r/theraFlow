## Inject

TheraFlow is a Python FastAPI WhatsApp bot. New files belong in the correct subdirectory of `src/theraflow/`.

```
src/theraflow/
  whatsapp/     # FastAPI route handlers and WhatsApp API integration.
                # Existing: webhook.py (inbound), sender.py (outbound send helpers)
                # New WhatsApp routes go here — handlers must stay thin (dispatch only)
  conversation/ # Core conversation engine and flow state machine.
                # Existing: engine.py (ConversationEngine), flow.py (state transitions)
                # New conversation logic goes here — this is the primary business layer
  llm/          # LLM client and prompt logic.
                # Existing: service.py
                # New LLM integrations or prompt builders go here
  safety/       # Crisis detection and safe-messaging responses.
                # Existing: detector.py, responses.py
                # New safety checks go here — keep safety logic out of conversation/
  sheets/       # Google Sheets integration for session data.
                # Existing: client.py
  notifications/ # Outbound alerts (Telegram, etc.)
                # Existing: telegram.py
```

Cross-cutting modules at `src/theraflow/` root (NOT for new features):
- `main.py` — FastAPI app factory and startup
- `config.py` — settings and env vars (Pydantic BaseSettings)
- `logging.py` — logger factory (`get_logger(name)`)
- `utils.py` — pure utility functions with no domain state

Rules:
- New conversation behaviors go in `src/theraflow/conversation/`
- New WhatsApp message types go in `src/theraflow/whatsapp/sender.py`
- `conversation/` must never import from `whatsapp/` (dependency flows inward)
- Use `get_logger(__name__)` from `theraflow.logging` — never use print()

## Reference

<!-- Full architecture detail — not injected, human-read only. -->
