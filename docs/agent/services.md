## Inject

Sessions are pure in-memory dict (`engine.py:158`). A container restart drops all active conversations. There is no persistence layer for session state.

Safety detection runs BEFORE session lookup on every message (`engine.py:215`). A `high` risk result: sends Telegram alert, destroys the session (`_sessions.pop`), returns the crisis message. A `medium` risk result: sends alert but does NOT destroy the session — the conversation continues (`engine.py:246-248`). Never skip or reorder this check.

The `GREETING` step has a special branch: `_handle_greeting_response` tries to extract both `name` and `who_for` from the first message. If `who_for` is found, `WHO_FOR` is skipped entirely and the session jumps directly to `GENDER` (`engine.py:314-319`). `STEP_ORDER` in `flow.py` lists all 8 steps, but the actual step visited depends on this branch.

`TERMS` decline ends the flow immediately — session is cleaned up and `write_lead` is NOT called (`engine.py:399-406`). The lead is lost. If you need to capture partial data on decline, write to `Follow Up` via `_on_follow_up()` instead.

`_on_conversation_complete` (`engine.py:611`) is the only place that calls both `write_lead` and `send_lead_notification`. It is called only when `advance_to == Step.CLOSING`. Both calls are individually wrapped in try/except — a Sheets failure does not prevent the Telegram notification and vice versa.

`calculate_score` (`sheets/client.py:140`) uses `whatsapp_name` and `phone_number` for the +15 contact score, but at call time `whatsapp_name` and `phone_number` are not in `session.collected_data` — they come from `session.whatsapp_name` and `session.phone`. The engine passes them via `LeadData` constructor fields, not through the score function directly (`engine.py:642-659`). If you change the scoring call, confirm both values are passed explicitly.

`StepConfig.natural=True` means the step accepts raw text AND uses LLM classification for free-text answers (`flow.py:56-65`). Natural steps never render as buttons or lists — `use_buttons` and `use_list` both return `False` when `natural=True` (`flow.py:73-83`). Do not set `natural=True` and `use_buttons` on the same step.

The LLM is disabled in tests via `os.environ["LLM_ENABLED"] = "false"` set in `conftest.py:22` — hardcoded prompts are used. When LLM is enabled, `GREETING` and `GENDER` steps always use fixed prompts regardless (`engine.py:502`).

Session eviction runs lazily — only when a NEW session is created (`_evict_stale_sessions` at `engine.py:448`). TTL is 30 minutes of inactivity (`SESSION_TTL_SECONDS = 1800`). Sessions are never evicted mid-conversation by a background task.

## Reference

### Side effects at CLOSING (ordered)

1. `calculate_score(data)` — pure, no I/O
2. `derive_intent({...data, risk_level})` — pure
3. `LeadData` constructed
4. `await sheets_client.write_lead(lead)` — appends to Leads sheet (may silently fail)
5. `await telegram_notifier.send_lead_notification(lead)` — Telegram alert (may silently fail)
6. `_cleanup_session(phone)` — session removed

### LLM fallback chain

`_build_prompt` attempts LLM generation; on any failure (timeout, empty response, API error) it silently falls back to `config.prompt` (`engine.py:494-515`). The fallback is transparent to the caller. `classify_answer` returns `None` on failure, causing a reprompt — not a skip.

### Three Google Sheets tabs

`SheetsClient` manages three worksheet tabs: "Leads" (main), "Follow Up" (declined appointments), "Conversation Log" (all in/out messages). Tabs are auto-created if missing. All writes are sync gspread calls wrapped in `asyncio.run_in_executor`. Token expiry triggers one automatic re-auth via `_reauthorize()` before propagating.

### Handoff detection

`_is_handoff_request` checks for Portuguese keywords: "falar com alguém", "atendente", "humano", etc. (`engine.py:555-565`). Detection runs after safety check but before any step logic. On match: Telegram alert sent, session cleaned up, flow ends — no lead is written.
