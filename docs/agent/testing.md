## Inject

`conftest.py` sets all required env vars via `os.environ.setdefault` BEFORE any `theraflow` import — `settings` is a module-level singleton that reads env at import time. If you add a new required `settings` field, add a corresponding `setdefault` to `conftest.py` or tests will fail with a validation error before any test runs.

`LLM_ENABLED` is force-set to `"false"` with `os.environ["LLM_ENABLED"] = "false"` (overwrite, not setdefault — `conftest.py:22`). Tests always use hardcoded prompts. Do not rely on LLM behavior in tests.

The `engine` fixture creates a fresh `ConversationEngine` with mocked `SheetsClient` and `TelegramNotifier` (`conftest.py:62-67`). Both mocks are `AsyncMock` — assert calls with `mock_sheets.write_lead.assert_awaited_once_with(...)`.

The `test_app` fixture bypasses the production lifespan entirely — it builds a bare `FastAPI`, includes the WhatsApp router, and directly sets `app.state.engine` and `app.state.http_client` (`conftest.py:72-81`). No real credentials are used.

Signature verification in tests: the `client` fixture sends requests to the ASGI app directly. For webhook POST tests, compute the HMAC signature using the test secret `"test_app_secret"` and add the `X-Hub-Signature-256: sha256=<hex>` header, or the handler will return 403.

`detect_risk` (`safety/detector.py`) is pure and synchronous — test it directly without fixtures.

## Reference

### Test files and focus

- `test_conversation.py` — unit tests for engine state transitions, step advancement, and data collection
- `test_scenarios.py` — named user scenarios (happy path, terms decline, escalation)
- `test_e2e_simulation.py` — full webhook POST → response round-trips through the ASGI app
- `test_safety.py` — crisis detection: high/medium risk, exclusion phrases, edge cases
- `test_security_stress.py` — injection attempts, oversized payloads, malformed JSON

### Mock patterns

```python
# Assert a lead was written with specific fields
mock_sheets.write_lead.assert_awaited_once()
lead_arg = mock_sheets.write_lead.call_args[0][0]
assert lead_arg.topic == "Ansiedade"

# Assert Telegram notification sent
mock_telegram.send_lead_notification.assert_awaited_once()
```

### HMAC helper for webhook tests

```python
import hashlib, hmac, json
body = json.dumps(payload).encode()
sig = hmac.new(b"test_app_secret", body, hashlib.sha256).hexdigest()
headers = {"X-Hub-Signature-256": f"sha256={sig}"}
```
