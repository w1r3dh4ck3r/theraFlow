## Inject

The webhook POST handler (`whatsapp/webhook.py:186`) must always return `{"status": "ok"}` with HTTP 200 — even on errors. Meta retries any non-200 response, causing duplicate message processing. Per-message exceptions are caught with `continue` (`webhook.py:278`), so one bad message never kills the batch.

Signature verification (`_verify_signature`, `webhook.py:88`) reads the **raw body bytes** before JSON parsing. Always call `await request.body()` first, then `await request.json()` separately — do not parse JSON and re-encode it, as byte-level differences break the HMAC (`webhook.py:211-212`).

`ConversationEngine` and `httpx.AsyncClient` are retrieved from `request.app.state` — they are application-lifetime singletons set during FastAPI lifespan. Never instantiate them inside a handler.

Only `text` and `interactive` message types are processed. `interactive` covers both `button_reply` and `list_reply` — both are extracted from `interactive.button_reply` or `interactive.list_reply` (`webhook.py:252`). All other types (image, audio, document) are silently logged and ignored.

The `_dispatch` adapter (`webhook.py:289`) is the only place that calls `engine.handle_message`. It takes exactly one of `text` or `button_payload` — never both. For button taps, `text=None`; for free-text, `button_payload=None`.

Contact display names come from `contacts[].profile.name` in the webhook payload, not from the message itself. `_extract_contact_names` builds a `wa_id → name` dict; missing names default to empty string (`webhook.py:150-173`).

## Reference

### Meta retry behavior

Meta will retry webhook delivery up to 3 times with exponential backoff if it receives a non-200 or a timeout. Because processing is synchronous within the handler, slow LLM calls can cause Meta to retry before the first response is sent. If LLM latency exceeds ~5s, consider queuing the dispatch.

### HMAC key source

`settings.whatsapp_app_secret` (`config.py`) is the app secret from the Meta developer dashboard — NOT the access token and NOT the verify token. All three are different values.

### GET verify endpoint

The `GET /webhook/whatsapp` handler (`webhook.py:38`) is only called once during initial Meta webhook setup. It echoes `hub.challenge` as plain text. After setup it will not be called again.
