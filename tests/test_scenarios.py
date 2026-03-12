"""Scenario-level tests for the 14-step TheraFlow conversation flow.

Exercises complete end-to-end journeys through :class:`ConversationEngine`
using ``engine.handle_message()`` directly, verifying both the response
quality at every step and the downstream side-effects (Sheets write,
Telegram notification, session cleanup) for distinct lead archetypes.
"""

from __future__ import annotations

from typing import Any

from unittest.mock import AsyncMock

from theraflow.conversation.engine import ConversationEngine, OutgoingMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PHONE = "5533333333333"
NAME = "Scenario User"


async def send(
    engine: ConversationEngine,
    phone: str,
    *,
    text: str | None = None,
    button_id: str | None = None,
    button_title: str | None = None,
) -> list[OutgoingMessage]:
    """Thin wrapper so tests don't repeat the full keyword call each time."""
    button_payload: dict[str, Any] | None = None
    if button_id is not None:
        button_payload = {"id": button_id, "title": button_title or ""}
    return await engine.handle_message(
        phone=phone,
        name=NAME,
        text=text,
        button_payload=button_payload,
    )


# ---------------------------------------------------------------------------
# Scenario 1 — Hot lead (all strong signals)
# ---------------------------------------------------------------------------


async def test_happy_path_hot_lead(
    engine: ConversationEngine,
    mock_sheets: AsyncMock,
    mock_telegram: AsyncMock,
) -> None:
    """Drive all 14 steps with strong signals; verify hot-lead outcome.

    Answers given:
    - topic   = "Ansiedade"         (clear, non-vague  → +20)
    - urgency = "O quanto antes"    (scheduling + soon → +40)
    - appointment_interest = "Sim"  (booking intent    → +15)
    - note    = non-empty text      (personal note     → +10)
    - name + phone always present   (contact info      → +15)
    Expected score: 100 → lead_quality == "hot"
    """
    all_responses: list[list[OutgoingMessage]] = []

    # Step 1 — Initial message creates session → returns GREETING prompt
    all_responses.append(await send(engine, PHONE, text="Oi"))

    # Step 2 — GREETING → Sim (button opt_0)
    all_responses.append(
        await send(engine, PHONE, button_id="opt_0", button_title="Sim")
    )

    # Step 3 — WHO_FOR → "Para mim" (list index 1)
    all_responses.append(await send(engine, PHONE, text="1"))

    # Step 4 — GENDER → Mulher (button opt_0)
    all_responses.append(
        await send(engine, PHONE, button_id="opt_0", button_title="Mulher")
    )

    # Step 5 — AGE_GROUP → 25–34 (list index 4)
    all_responses.append(await send(engine, PHONE, text="4"))

    # Step 6 — CITY → clear, specific city (free text)
    all_responses.append(await send(engine, PHONE, text="São Paulo"))

    # Step 7 — FORMAT → Online (button opt_0)
    all_responses.append(
        await send(engine, PHONE, button_id="opt_0", button_title="Online")
    )

    # Step 8 — FIRST_THERAPY → Sim (button opt_0)
    all_responses.append(
        await send(engine, PHONE, button_id="opt_0", button_title="Sim")
    )

    # Step 9 — TOPIC → Ansiedade (list index 1) — strong, non-vague topic
    all_responses.append(await send(engine, PHONE, text="1"))

    # Step 10 — URGENCY → O quanto antes (list index 1) — highest urgency
    all_responses.append(await send(engine, PHONE, text="1"))

    # Step 11 — PREFERRED_TIME → Manhã (list index 1)
    all_responses.append(await send(engine, PHONE, text="1"))

    # Step 12 — APPOINTMENT_INTENT → Sim (button opt_0) — strong booking intent
    all_responses.append(
        await send(engine, PHONE, button_id="opt_0", button_title="Sim")
    )

    # Step 13 — OPTIONAL_NOTE → personal note text (+10 to score)
    all_responses.append(
        await send(
            engine,
            PHONE,
            text="Estou sofrendo muito com ansiedade e quero começar o quanto antes",
        )
    )

    # Step 14 — CONSENT → Sim (button opt_0) → triggers CLOSING
    closing_msgs = await send(engine, PHONE, button_id="opt_0", button_title="Sim")
    all_responses.append(closing_msgs)

    # --- Assertions ---

    # Every step must return at least one non-empty message
    for step_num, msgs in enumerate(all_responses, start=1):
        assert msgs, f"Step {step_num} returned an empty response list"
        for msg in msgs:
            assert msg.text, f"Step {step_num} contains a message with empty text"

    # Final step returns the closing acknowledgement
    assert "Perfeito" in closing_msgs[0].text

    # Session is cleaned up after CLOSING
    assert PHONE not in engine._sessions

    # Lead persisted to Sheets exactly once with hot quality
    mock_sheets.write_lead.assert_called_once()
    lead = mock_sheets.write_lead.call_args[0][0]
    assert lead.score >= 60, f"Expected score >= 60, got {lead.score}"
    assert lead.lead_quality == "hot"

    # Telegram notified exactly once
    mock_telegram.send_lead_notification.assert_called_once()


# ---------------------------------------------------------------------------
# Scenario 2 — Cold lead (weak / vague signals)
# ---------------------------------------------------------------------------


async def test_cold_lead(
    engine: ConversationEngine,
    mock_sheets: AsyncMock,
    mock_telegram: AsyncMock,
) -> None:
    """Drive all 14 steps with minimal/vague answers; verify cold-lead outcome.

    Answers given:
    - city    = "SP"                      (2-char vague field, < threshold)
    - topic   = "Outro"                   (in _VAGUE_TOPICS → no +20)
    - urgency = "Ainda estou pensando"    (no scheduling/committed bonus)
    - appointment_interest = "Ainda estou pensando"  (not "Sim" → no +15)
    - note    = "" (via "pular")          (no note → no +10)
    - name + phone always present         (contact info → +15)
    Expected score: 15 → lead_quality == "cold"
    """
    # Step 1 — Initial message creates session → GREETING prompt
    await send(engine, PHONE, text="Oi")

    # Step 2 — GREETING → Sim (button opt_0)
    await send(engine, PHONE, button_id="opt_0", button_title="Sim")

    # Step 3 — WHO_FOR → "Para mim" (list index 1)
    await send(engine, PHONE, text="1")

    # Step 4 — GENDER → Mulher (button opt_0)
    await send(engine, PHONE, button_id="opt_0", button_title="Mulher")

    # Step 5 — AGE_GROUP → 25–34 (list index 4)
    await send(engine, PHONE, text="4")

    # Step 6 — CITY → short/vague answer (2 chars)
    await send(engine, PHONE, text="SP")

    # Step 7 — FORMAT → Online (button opt_0)
    await send(engine, PHONE, button_id="opt_0", button_title="Online")

    # Step 8 — FIRST_THERAPY → Sim (button opt_0)
    await send(engine, PHONE, button_id="opt_0", button_title="Sim")

    # Step 9 — TOPIC → Outro (list index 6) — vague topic, no +20
    await send(engine, PHONE, text="6")

    # Step 10 — URGENCY → Ainda estou pensando (list index 4) — lowest urgency
    await send(engine, PHONE, text="4")

    # Step 11 — PREFERRED_TIME → Manhã (list index 1)
    await send(engine, PHONE, text="1")

    # Step 12 — APPOINTMENT_INTENT → Ainda estou pensando (button opt_1) — no +15
    await send(
        engine, PHONE, button_id="opt_1", button_title="Ainda estou pensando"
    )

    # Step 13 — OPTIONAL_NOTE → "pular" keyword stores "" and advances
    await send(engine, PHONE, text="pular")

    # Step 14 — CONSENT → Sim (button opt_0) → triggers CLOSING
    await send(engine, PHONE, button_id="opt_0", button_title="Sim")

    # Lead persisted to Sheets exactly once with cold quality
    mock_sheets.write_lead.assert_called_once()
    lead = mock_sheets.write_lead.call_args[0][0]
    assert lead.score < 30, f"Expected score < 30, got {lead.score}"
    assert lead.lead_quality == "cold"
