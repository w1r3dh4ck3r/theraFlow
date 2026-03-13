"""Scenario-level tests for the 14-step TheraFlow conversation flow.

Exercises complete end-to-end journeys through :class:`ConversationEngine`
using ``engine.handle_message()`` directly, verifying both the response
quality at every step and the downstream side-effects (Sheets write,
Telegram notification, session cleanup) for distinct lead archetypes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import ANY, AsyncMock

from theraflow.conversation.engine import (
    SESSION_TTL_SECONDS,
    ConversationEngine,
    OutgoingMessage,
    UserSession,
)
from theraflow.conversation.flow import (
    INVALID_INPUT_MESSAGE,
    LGPD_DECLINED_MESSAGE,
    Step,
)
from theraflow.safety.responses import CRISIS_MESSAGE_HIGH, CRISIS_MESSAGE_MEDIUM

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


# ---------------------------------------------------------------------------
# Scenario 3 — Terms / LGPD decline (user refuses data storage at CONSENT)
# ---------------------------------------------------------------------------


async def test_terms_decline(
    engine: ConversationEngine,
    mock_sheets: AsyncMock,
    mock_telegram: AsyncMock,
) -> None:
    """Drive flow to CONSENT, then decline — verify LGPD message and no persistence.

    The user progresses through all steps up to and including OPTIONAL_NOTE,
    so the CONSENT prompt is shown.  They then answer 'Não' (opt_1).

    Expected behaviour:
    - Response contains the LGPD-decline message text.
    - Session is removed from engine._sessions (cleaned up).
    - ``write_lead`` is **not** called (data discarded on refusal).
    - ``write_follow_up`` is **not** called (no follow-up for explicit refusal).
    """
    # Steps 1-13: drive to CONSENT prompt (same strong-signal answers as hot lead)

    # Step 1 — Initial message → GREETING prompt
    await send(engine, PHONE, text="Oi")

    # Step 2 — GREETING → Sim
    await send(engine, PHONE, button_id="opt_0", button_title="Sim")

    # Step 3 — WHO_FOR → "Para mim"
    await send(engine, PHONE, text="1")

    # Step 4 — GENDER → Mulher
    await send(engine, PHONE, button_id="opt_0", button_title="Mulher")

    # Step 5 — AGE_GROUP → 25–34
    await send(engine, PHONE, text="4")

    # Step 6 — CITY → specific city
    await send(engine, PHONE, text="São Paulo")

    # Step 7 — FORMAT → Online
    await send(engine, PHONE, button_id="opt_0", button_title="Online")

    # Step 8 — FIRST_THERAPY → Sim
    await send(engine, PHONE, button_id="opt_0", button_title="Sim")

    # Step 9 — TOPIC → Ansiedade
    await send(engine, PHONE, text="1")

    # Step 10 — URGENCY → O quanto antes
    await send(engine, PHONE, text="1")

    # Step 11 — PREFERRED_TIME → Manhã
    await send(engine, PHONE, text="1")

    # Step 12 — APPOINTMENT_INTENT → Sim
    await send(engine, PHONE, button_id="opt_0", button_title="Sim")

    # Step 13 — OPTIONAL_NOTE → personal note; next prompt will be CONSENT
    await send(
        engine,
        PHONE,
        text="Estou sofrendo muito com ansiedade e quero começar o quanto antes",
    )

    # Step 14 — CONSENT → Não (opt_1) — decline data storage
    decline_msgs = await send(engine, PHONE, button_id="opt_1", button_title="Não")

    # --- Assertions ---

    # Response must carry the LGPD-decline message
    assert decline_msgs, "Expected a non-empty response on LGPD decline"
    assert LGPD_DECLINED_MESSAGE in decline_msgs[0].text, (
        f"LGPD decline message not found in: {decline_msgs[0].text!r}"
    )

    # Session must be cleaned up immediately after refusal
    assert PHONE not in engine._sessions, (
        "Session should be removed after LGPD decline"
    )

    # No data must be persisted — user explicitly refused storage
    mock_sheets.write_lead.assert_not_called()
    mock_sheets.write_follow_up.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 4 — Scheduling decline (user accepts terms but not appointment)
# ---------------------------------------------------------------------------


async def test_scheduling_decline(
    engine: ConversationEngine,
    mock_sheets: AsyncMock,
    mock_telegram: AsyncMock,
) -> None:
    """User accepts LGPD terms but answers 'Ainda estou pensando' at APPOINTMENT_INTENT.

    The flow should complete normally through CLOSING.  The lead is still
    written to the main sheet because the user gave LGPD consent; however,
    the appointment_interest field reflects the hesitant answer and the score
    is lower than the all-strong-signals hot-lead scenario.

    Note: ``_on_follow_up`` is defined in the engine but is not yet wired into
    ``handle_message``, so ``write_follow_up`` will **not** be called.
    """
    # Step 1 — Initial message → GREETING prompt
    await send(engine, PHONE, text="Oi")

    # Step 2 — GREETING → Sim
    await send(engine, PHONE, button_id="opt_0", button_title="Sim")

    # Step 3 — WHO_FOR → "Para mim"
    await send(engine, PHONE, text="1")

    # Step 4 — GENDER → Mulher
    await send(engine, PHONE, button_id="opt_0", button_title="Mulher")

    # Step 5 — AGE_GROUP → 25–34
    await send(engine, PHONE, text="4")

    # Step 6 — CITY → specific city
    await send(engine, PHONE, text="São Paulo")

    # Step 7 — FORMAT → Online
    await send(engine, PHONE, button_id="opt_0", button_title="Online")

    # Step 8 — FIRST_THERAPY → Sim
    await send(engine, PHONE, button_id="opt_0", button_title="Sim")

    # Step 9 — TOPIC → Ansiedade (+20)
    await send(engine, PHONE, text="1")

    # Step 10 — URGENCY → O quanto antes (+40)
    await send(engine, PHONE, text="1")

    # Step 11 — PREFERRED_TIME → Manhã
    await send(engine, PHONE, text="1")

    # Step 12 — APPOINTMENT_INTENT → "Ainda estou pensando" (opt_1) — no +15
    await send(
        engine, PHONE, button_id="opt_1", button_title="Ainda estou pensando"
    )

    # Step 13 — OPTIONAL_NOTE → personal note (+10)
    await send(
        engine,
        PHONE,
        text="Estou sofrendo muito com ansiedade e quero começar o quanto antes",
    )

    # Step 14 — CONSENT → Sim (opt_0) → triggers CLOSING
    closing_msgs = await send(engine, PHONE, button_id="opt_0", button_title="Sim")

    # --- Assertions ---

    # Closing message delivered
    assert closing_msgs, "Expected a non-empty response at CLOSING"
    assert "Perfeito" in closing_msgs[0].text

    # Session cleaned up after CLOSING
    assert PHONE not in engine._sessions

    # Lead must be written exactly once — user gave consent
    mock_sheets.write_lead.assert_called_once()
    lead = mock_sheets.write_lead.call_args[0][0]

    # Appointment interest reflects the hesitant answer
    assert lead.appointment_interest == "Ainda estou pensando", (
        f"Expected 'Ainda estou pensando', got {lead.appointment_interest!r}"
    )

    # Score must be lower than the hot-lead scenario (100) because the +15
    # booking bonus is absent when appointment_interest != "Sim".
    hot_lead_score = 100
    assert lead.score < hot_lead_score, (
        f"Expected score < {hot_lead_score} (hot-lead baseline), got {lead.score}"
    )

    # _on_follow_up is not yet called by handle_message — no follow-up write
    mock_sheets.write_follow_up.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 5 — Crisis: HIGH risk (immediate danger signal)
# ---------------------------------------------------------------------------


async def test_crisis_high_risk(
    engine: ConversationEngine,
    mock_sheets: AsyncMock,
    mock_telegram: AsyncMock,
) -> None:
    """Send a HIGH-risk message after greeting; verify crisis response and cleanup.

    A new session is opened via the initial "Oi", then the user sends a
    HIGH-risk phrase.  The engine must:
    - Return :data:`~theraflow.safety.responses.CRISIS_MESSAGE_HIGH` which
      embeds the CVV number *188* and the SAMU number *192*.
    - Pop the session from ``engine._sessions`` immediately.
    - Call ``mock_telegram.send_safety_alert`` exactly once with
      ``risk_level='high'``.
    - NOT persist any lead data to Sheets.
    """
    # Step 1 — Create session
    await send(engine, PHONE, text="Oi")
    assert PHONE in engine._sessions, "Session should exist after initial message"

    # Step 2 — HIGH-risk phrase triggers crisis response
    crisis_msgs = await send(engine, PHONE, text="quero me matar")

    # Response must be non-empty and contain both emergency numbers
    assert crisis_msgs, "Expected a non-empty response on HIGH-risk message"
    combined_text = " ".join(msg.text for msg in crisis_msgs)
    assert "188" in combined_text, f"CVV number 188 not found in: {combined_text!r}"
    assert "192" in combined_text, f"SAMU number 192 not found in: {combined_text!r}"

    # Entire crisis message should match the canonical constant
    assert crisis_msgs[0].text == CRISIS_MESSAGE_HIGH

    # Session must be cleaned up — no further messages should be delivered
    assert PHONE not in engine._sessions, (
        "Session should be removed from engine._sessions after HIGH-risk detection"
    )

    # Safety alert must be forwarded to Telegram with correct risk level
    mock_telegram.send_safety_alert.assert_called_once_with(
        phone=PHONE,
        risk_level="high",
        matched_terms=ANY,
    )

    # No lead data should be written — conversation was cut short by the crisis
    mock_sheets.write_lead.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 6 — Crisis: MEDIUM risk (distress signal, flow continues)
# ---------------------------------------------------------------------------


async def test_crisis_medium_risk(
    engine: ConversationEngine,
    mock_sheets: AsyncMock,
    mock_telegram: AsyncMock,
) -> None:
    """Send a MEDIUM-risk message after greeting; verify warning and session retention.

    Unlike HIGH risk, a MEDIUM-risk detection must NOT clean up the session —
    the user should be able to continue the flow after receiving the warning.

    Expected behaviour:
    - Response contains :data:`~theraflow.safety.responses.CRISIS_MESSAGE_MEDIUM`
      which embeds CVV *188* and an empathetic continuation invitation.
    - Session is **not** removed from ``engine._sessions``.
    - ``mock_telegram.send_safety_alert`` called once with ``risk_level='medium'``.
    - A valid follow-up message from the user is accepted and the engine
      advances the conversation normally.
    """
    # Step 1 — Create session
    await send(engine, PHONE, text="Oi")
    assert PHONE in engine._sessions, "Session should exist after initial message"

    # Step 2 — MEDIUM-risk phrase triggers medium crisis response
    crisis_msgs = await send(engine, PHONE, text="não aguento mais")

    # Response must be non-empty and carry the CVV number
    assert crisis_msgs, "Expected a non-empty response on MEDIUM-risk message"
    combined_text = " ".join(msg.text for msg in crisis_msgs)
    assert "188" in combined_text, f"CVV number 188 not found in: {combined_text!r}"

    # Entire crisis message should match the canonical constant
    assert crisis_msgs[0].text == CRISIS_MESSAGE_MEDIUM

    # Session must be RETAINED — the user can continue after the warning
    assert PHONE in engine._sessions, (
        "Session should be kept in engine._sessions after MEDIUM-risk detection"
    )

    # Safety alert must be forwarded to Telegram with correct risk level
    mock_telegram.send_safety_alert.assert_called_once_with(
        phone=PHONE,
        risk_level="medium",
        matched_terms=ANY,
    )

    # User continues the flow — session is still at Step.GREETING (the
    # medium-risk handler returns early without advancing the step).
    follow_up_msgs = await send(engine, PHONE, button_id="opt_0", button_title="Sim")
    assert follow_up_msgs, "Expected a response after user continues post-warning"

    # Session must still be alive — conversation is in progress
    assert PHONE in engine._sessions, (
        "Session should persist while the user continues past the medium-risk warning"
    )


# ---------------------------------------------------------------------------
# Scenario 7 — Spam / gibberish at a button step
# ---------------------------------------------------------------------------


async def test_spam_gibberish(
    engine: ConversationEngine,
    mock_sheets: AsyncMock,
    mock_telegram: AsyncMock,
) -> None:
    """Send random characters at a button step (GENDER); verify reprompt behaviour.

    GENDER uses WhatsApp interactive buttons (3 options), so free-form text is
    invalid.  The engine must:
    - Return a 2-message reprompt: the ``INVALID_INPUT_MESSAGE`` error followed
      by the original GENDER prompt (with its buttons).
    - Leave ``current_step`` at ``Step.GENDER`` — the step must not advance.
    - Keep the session alive in ``engine._sessions``.
    - NOT call any Sheets or Telegram side-effects.
    """
    # Drive to GENDER (three exchanges: initial → GREETING answer → WHO_FOR answer)
    await send(engine, PHONE, text="Oi")                               # → GREETING prompt
    await send(engine, PHONE, button_id="opt_0", button_title="Sim")  # → WHO_FOR prompt
    await send(engine, PHONE, text="1")                                # → GENDER prompt

    assert engine._sessions[PHONE].current_step == Step.GENDER

    # Send random characters — no button_payload and text doesn't match any option
    reprompt_msgs = await send(engine, PHONE, text="asdfghjkl")

    # Must return at least 2 messages: the error notice + the re-issued step prompt
    assert len(reprompt_msgs) >= 2, (
        f"Expected reprompt of ≥ 2 messages, got {len(reprompt_msgs)}: {reprompt_msgs!r}"
    )
    assert reprompt_msgs[0].text == INVALID_INPUT_MESSAGE, (
        f"First reprompt message should be INVALID_INPUT_MESSAGE, got: {reprompt_msgs[0].text!r}"
    )

    # Step must NOT have advanced — session is still waiting for a valid GENDER answer
    assert engine._sessions[PHONE].current_step == Step.GENDER, (
        "Step should remain at GENDER after invalid input"
    )

    # Session must still exist — invalid input does not close the session
    assert PHONE in engine._sessions

    # No side-effects from a rejected message
    mock_sheets.write_lead.assert_not_called()
    mock_telegram.send_lead_notification.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario 8 — Session TTL eviction (no sleep, no freezegun)
# ---------------------------------------------------------------------------


async def test_session_ttl_eviction(
    engine: ConversationEngine,
    mock_sheets: AsyncMock,
    mock_telegram: AsyncMock,
) -> None:
    """Age a session beyond SESSION_TTL_SECONDS and verify lazy eviction.

    Eviction is triggered lazily when a *new* contact sends their first message
    (``_evict_stale_sessions`` is called before each new session is created).
    ``last_activity_at`` is mutated directly — no ``time.sleep`` or
    ``freezegun`` are used.

    Expected behaviour:
    1. After mutation, the stale session is evicted when a different phone
       causes ``_evict_stale_sessions`` to run.
    2. The original phone subsequently receives a fresh greeting (a brand-new
       session at ``Step.GREETING``), confirming the old session is gone.
    """
    OTHER_PHONE = "5599999999999"

    # Create a session for PHONE
    await send(engine, PHONE, text="Oi")
    assert PHONE in engine._sessions, "Session should exist after initial message"

    # Age the session well past the TTL by mutating last_activity_at directly
    stale_time = datetime.now(timezone.utc) - timedelta(seconds=SESSION_TTL_SECONDS + 1)
    engine._sessions[PHONE].last_activity_at = stale_time

    # A different phone's first message triggers _evict_stale_sessions()
    await send(engine, OTHER_PHONE, text="Oi")

    # The stale session must now be evicted
    assert PHONE not in engine._sessions, (
        "Original phone's session should be evicted after TTL expires"
    )

    # Sending from the original phone again must start a completely fresh session
    fresh_msgs = await send(engine, PHONE, text="Oi")
    assert fresh_msgs, "Expected a greeting response for the re-connecting phone"
    assert PHONE in engine._sessions, (
        "A new session should be created when the original phone reconnects"
    )
    assert engine._sessions[PHONE].current_step == Step.GREETING, (
        "Fresh session must start at Step.GREETING"
    )


# ---------------------------------------------------------------------------
# Scenario 9 — Post-terminal message (session at CLOSING is silently ignored)
# ---------------------------------------------------------------------------


async def test_post_terminal_message(
    engine: ConversationEngine,
    mock_sheets: AsyncMock,
    mock_telegram: AsyncMock,
) -> None:
    """Verify that messages sent to a CLOSING session are silently ignored.

    The engine cleans up the session as soon as it reaches CLOSING during the
    normal happy path.  This test exercises the post-terminal guard in
    ``handle_message`` by manually inserting a ghost session at
    ``Step.CLOSING`` into ``engine._sessions`` after the real flow has already
    cleaned up, then confirms the guard returns ``[]`` without triggering any
    further side-effects.

    Two-phase assertions:
    1. After the full happy path, session is gone and one lead + notification
       were persisted.
    2. After injecting a ghost CLOSING session and sending a message, the
       engine returns ``[]`` and call counts remain unchanged.
    """
    # --- Phase 1: complete happy path ---
    await send(engine, PHONE, text="Oi")
    await send(engine, PHONE, button_id="opt_0", button_title="Sim")   # GREETING
    await send(engine, PHONE, text="1")                                 # WHO_FOR
    await send(engine, PHONE, button_id="opt_0", button_title="Mulher")  # GENDER
    await send(engine, PHONE, text="4")                                 # AGE_GROUP
    await send(engine, PHONE, text="São Paulo")                         # CITY
    await send(engine, PHONE, button_id="opt_0", button_title="Online") # FORMAT
    await send(engine, PHONE, button_id="opt_0", button_title="Sim")   # FIRST_THERAPY
    await send(engine, PHONE, text="1")                                 # TOPIC
    await send(engine, PHONE, text="1")                                 # URGENCY
    await send(engine, PHONE, text="1")                                 # PREFERRED_TIME
    await send(engine, PHONE, button_id="opt_0", button_title="Sim")   # APPOINTMENT_INTENT
    await send(engine, PHONE, text="pular")                             # OPTIONAL_NOTE
    closing_msgs = await send(engine, PHONE, button_id="opt_0", button_title="Sim")  # CONSENT → CLOSING

    assert closing_msgs, "Expected closing messages from the terminal step"
    assert "Perfeito" in closing_msgs[0].text

    # Session is already cleaned up after CLOSING
    assert PHONE not in engine._sessions, (
        "Session should be removed from engine._sessions after CLOSING"
    )

    # Side-effects happened exactly once during the real flow
    mock_sheets.write_lead.assert_called_once()
    mock_telegram.send_lead_notification.assert_called_once()

    # --- Phase 2: inject ghost session and test post-terminal guard ---
    ghost = UserSession(
        phone=PHONE,
        whatsapp_name=NAME,
        current_step=Step.CLOSING,
        collected_data={},
    )
    engine._sessions[PHONE] = ghost

    # Any message to a CLOSING session must be silently ignored
    post_msgs = await send(engine, PHONE, text="Oi")
    assert post_msgs == [], (
        f"Expected [] for post-terminal message, got: {post_msgs!r}"
    )

    # Guard must not trigger any additional Sheets writes or Telegram alerts
    assert mock_sheets.write_lead.call_count == 1, (
        "write_lead should not be called again after post-terminal message"
    )
    assert mock_telegram.send_lead_notification.call_count == 1, (
        "send_lead_notification should not be called again after post-terminal message"
    )


# ---------------------------------------------------------------------------
# Scenario 10 — Button payload vs plain text at the same step
# ---------------------------------------------------------------------------


async def test_button_vs_text_input(
    engine: ConversationEngine,
    mock_sheets: AsyncMock,
    mock_telegram: AsyncMock,
) -> None:
    """Button payload and equivalent plain-text inputs resolve to the same answer.

    GENDER is a *button* step (3 options ≤ 3, rendered as WhatsApp interactive
    buttons).  Both input modes are driven in separate sessions:

    - Path A (``PHONE``): button payload ``{'id': 'opt_0', 'title': 'Mulher'}``
    - Path B (``PHONE_B``): plain text ``'Mulher'``

    Both must store ``'Mulher'`` in ``collected_data['gender']`` and advance
    to ``Step.AGE_GROUP``.

    AGE_GROUP is a *list* step (7 options, rendered as a WhatsApp list
    message).  Numeric input ``text='1'`` is verified to resolve to the first
    option (``'Menor de 12'``) via 1-based index resolution.
    """
    PHONE_B = "5511111111111"  # Second session for the text-input path

    # ------------------------------------------------------------------
    # Path A — GENDER via button payload (opt_0 → "Mulher")
    # ------------------------------------------------------------------
    await send(engine, PHONE, text="Oi")                               # → GREETING
    await send(engine, PHONE, button_id="opt_0", button_title="Sim")  # → WHO_FOR
    await send(engine, PHONE, text="1")                                # → GENDER
    assert engine._sessions[PHONE].current_step == Step.GENDER

    gender_prompt_a = await send(engine, PHONE, button_id="opt_0", button_title="Mulher")

    assert gender_prompt_a, "Expected a prompt after answering GENDER via button"
    assert engine._sessions[PHONE].collected_data.get("gender") == "Mulher", (
        f"Expected gender='Mulher' via button, got: {engine._sessions[PHONE].collected_data!r}"
    )
    assert engine._sessions[PHONE].current_step == Step.AGE_GROUP

    # ------------------------------------------------------------------
    # Path B — GENDER via plain text ("Mulher")
    # ------------------------------------------------------------------
    await send(engine, PHONE_B, text="Oi")                               # → GREETING
    await send(engine, PHONE_B, button_id="opt_0", button_title="Sim")  # → WHO_FOR
    await send(engine, PHONE_B, text="1")                                # → GENDER
    assert engine._sessions[PHONE_B].current_step == Step.GENDER

    gender_prompt_b = await send(engine, PHONE_B, text="Mulher")

    assert gender_prompt_b, "Expected a prompt after answering GENDER via text"
    assert engine._sessions[PHONE_B].collected_data.get("gender") == "Mulher", (
        f"Expected gender='Mulher' via text, got: {engine._sessions[PHONE_B].collected_data!r}"
    )
    assert engine._sessions[PHONE_B].current_step == Step.AGE_GROUP

    # Both paths must resolve to the same canonical stored value
    assert (
        engine._sessions[PHONE].collected_data["gender"]
        == engine._sessions[PHONE_B].collected_data["gender"]
    ), "Button and text inputs for GENDER must resolve to the same stored value"

    # ------------------------------------------------------------------
    # List step — AGE_GROUP via numeric index (text='1' → "Menor de 12")
    # ------------------------------------------------------------------
    # Continue Path A which is already waiting at AGE_GROUP
    age_prompt = await send(engine, PHONE, text="1")  # index 1 → options[0] = "Menor de 12"

    assert age_prompt, "Expected a prompt after answering AGE_GROUP via numeric index"
    assert engine._sessions[PHONE].collected_data.get("age_group") == "Menor de 12", (
        f"Numeric '1' should resolve to 'Menor de 12', "
        f"got: {engine._sessions[PHONE].collected_data.get('age_group')!r}"
    )
    assert engine._sessions[PHONE].current_step == Step.CITY
