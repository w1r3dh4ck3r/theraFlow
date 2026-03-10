"""Tests for ConversationEngine — the 14-step lead-qualification state machine."""

from __future__ import annotations

from typing import Any

import pytest
from unittest.mock import AsyncMock

from theraflow.conversation.engine import ConversationEngine, OutgoingMessage, UserSession
from theraflow.conversation.flow import (
    HUMAN_HANDOFF_MESSAGE,
    INVALID_INPUT_MESSAGE,
    LGPD_DECLINED_MESSAGE,
    Step,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PHONE_A = "5511111111111"
PHONE_B = "5522222222222"
NAME = "Test User"


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


async def run_to_consent(engine: ConversationEngine, phone: str = PHONE_A) -> None:
    """Drive the engine from a fresh start up to (and including) the CONSENT step prompt."""
    await send(engine, phone, text="Oi")                                            # creates session → GREETING
    await send(engine, phone, button_id="opt_0", button_title="Sim")               # GREETING → WHO_FOR
    await send(engine, phone, text="1")                                             # WHO_FOR → GENDER
    await send(engine, phone, button_id="opt_0", button_title="Mulher")            # GENDER → AGE_GROUP
    await send(engine, phone, text="4")                                             # AGE_GROUP → CITY
    await send(engine, phone, text="São Paulo")                                     # CITY → FORMAT
    await send(engine, phone, button_id="opt_0", button_title="Online")            # FORMAT → FIRST_THERAPY
    await send(engine, phone, button_id="opt_1", button_title="Não")               # FIRST_THERAPY → TOPIC
    await send(engine, phone, text="1")                                             # TOPIC → URGENCY
    await send(engine, phone, text="1")                                             # URGENCY → PREFERRED_TIME
    await send(engine, phone, text="1")                                             # PREFERRED_TIME → APPOINTMENT_INTENT
    await send(engine, phone, button_id="opt_0", button_title="Sim")               # APPOINTMENT_INTENT → OPTIONAL_NOTE
    await send(engine, phone, text="Estou ansioso")                                 # OPTIONAL_NOTE → CONSENT


# ---------------------------------------------------------------------------
# New-user greeting
# ---------------------------------------------------------------------------


class TestNewUser:
    async def test_first_message_returns_greeting_prompt(self, engine: ConversationEngine) -> None:
        msgs = await send(engine, PHONE_A, text="Oi")
        assert len(msgs) == 1
        assert "assistente virtual" in msgs[0].text

    async def test_greeting_is_interactive(self, engine: ConversationEngine) -> None:
        """GREETING has exactly 2 options so it renders as reply buttons."""
        msgs = await send(engine, PHONE_A, text="Oi")
        assert msgs[0].is_interactive
        assert len(msgs[0].buttons) == 2

    async def test_session_created_at_greeting_step(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        assert PHONE_A in engine._sessions
        assert engine._sessions[PHONE_A].current_step == Step.GREETING


# ---------------------------------------------------------------------------
# Full 14-step happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    async def test_greeting_to_who_for(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        msgs = await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")
        assert engine._sessions[PHONE_A].current_step == Step.WHO_FOR
        assert "Para quem" in msgs[0].text

    async def test_who_for_stored_on_numbered_answer(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")      # GREETING → WHO_FOR
        await send(engine, PHONE_A, text="1")                                    # answer "Para mim"
        assert engine._sessions[PHONE_A].collected_data["who_for"] == "Para mim"
        assert engine._sessions[PHONE_A].current_step == Step.GENDER

    async def test_gender_stored_on_button_answer(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")
        await send(engine, PHONE_A, text="1")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Mulher")
        assert engine._sessions[PHONE_A].collected_data["gender"] == "Mulher"
        assert engine._sessions[PHONE_A].current_step == Step.AGE_GROUP

    async def test_age_group_stored(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")
        await send(engine, PHONE_A, text="1")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Mulher")
        await send(engine, PHONE_A, text="4")                                    # option 4 = "25–34"
        assert engine._sessions[PHONE_A].collected_data["age_group"] == "25\u201334"
        assert engine._sessions[PHONE_A].current_step == Step.CITY

    async def test_city_stored_as_free_text(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")
        await send(engine, PHONE_A, text="1")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Mulher")
        await send(engine, PHONE_A, text="4")
        await send(engine, PHONE_A, text="São Paulo")
        assert engine._sessions[PHONE_A].collected_data["city"] == "São Paulo"
        assert engine._sessions[PHONE_A].current_step == Step.FORMAT

    async def test_full_happy_path_reaches_closing(
        self, engine: ConversationEngine, mock_sheets: AsyncMock, mock_telegram: AsyncMock
    ) -> None:
        """Drive all 14 steps; verify closing message and downstream calls."""
        # Message 1: trigger session creation
        await send(engine, PHONE_A, text="Oi")

        # Step 1 — GREETING → "Sim"
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")
        # Step 2 — WHO_FOR → "1" (Para mim)
        await send(engine, PHONE_A, text="1")
        # Step 3 — GENDER → opt_0 (Mulher)
        await send(engine, PHONE_A, button_id="opt_0", button_title="Mulher")
        # Step 4 — AGE_GROUP → "4" (25–34)
        await send(engine, PHONE_A, text="4")
        # Step 5 — CITY → free text
        await send(engine, PHONE_A, text="São Paulo")
        # Step 6 — FORMAT → opt_0 (Online)
        await send(engine, PHONE_A, button_id="opt_0", button_title="Online")
        # Step 7 — FIRST_THERAPY → opt_1 (Não)
        await send(engine, PHONE_A, button_id="opt_1", button_title="Não")
        # Step 8 — TOPIC → "1" (Ansiedade)
        await send(engine, PHONE_A, text="1")
        # Step 9 — URGENCY → "1" (O quanto antes)
        await send(engine, PHONE_A, text="1")
        # Step 10 — PREFERRED_TIME → "1" (Manhã)
        await send(engine, PHONE_A, text="1")
        # Step 11 — APPOINTMENT_INTENT → opt_0 (Sim)
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")
        # Step 12 — OPTIONAL_NOTE → free text
        await send(engine, PHONE_A, text="Estou ansioso")
        # Step 13 — CONSENT → opt_0 (Sim) → triggers CLOSING
        msgs = await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")

        assert len(msgs) == 1
        assert "Perfeito" in msgs[0].text
        # Session cleaned up after closing
        assert PHONE_A not in engine._sessions
        # Downstream persistence hooks called exactly once
        mock_sheets.write_lead.assert_called_once()
        mock_telegram.send_lead_notification.assert_called_once()

    async def test_collected_data_contents_after_full_flow(
        self, engine: ConversationEngine, mock_sheets: AsyncMock
    ) -> None:
        """The LeadData passed to write_lead contains the answers we gave."""
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")
        await send(engine, PHONE_A, text="1")                                    # who_for = Para mim
        await send(engine, PHONE_A, button_id="opt_0", button_title="Mulher")   # gender = Mulher
        await send(engine, PHONE_A, text="4")                                    # age_group = 25–34
        await send(engine, PHONE_A, text="Curitiba")                             # city
        await send(engine, PHONE_A, button_id="opt_0", button_title="Online")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")       # first_therapy = Sim
        await send(engine, PHONE_A, text="1")                                    # topic = Ansiedade
        await send(engine, PHONE_A, text="1")                                    # urgency = O quanto antes
        await send(engine, PHONE_A, text="1")                                    # preferred_time = Manhã
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")       # appointment_interest = Sim
        await send(engine, PHONE_A, text="Minha nota")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")       # consent

        lead = mock_sheets.write_lead.call_args[0][0]
        assert lead.who_for == "Para mim"
        assert lead.gender == "Mulher"
        assert lead.city == "Curitiba"
        assert lead.appointment_interest == "Sim"
        assert lead.note == "Minha nota"
        assert lead.phone_number == PHONE_A

    async def test_optional_note_skip_keyword(self, engine: ConversationEngine, mock_sheets: AsyncMock) -> None:
        """The word 'pular' at OPTIONAL_NOTE stores an empty string and advances."""
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")
        await send(engine, PHONE_A, text="1")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Mulher")
        await send(engine, PHONE_A, text="4")
        await send(engine, PHONE_A, text="SP")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Online")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")
        await send(engine, PHONE_A, text="1")
        await send(engine, PHONE_A, text="1")
        await send(engine, PHONE_A, text="1")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")
        await send(engine, PHONE_A, text="pular")                                # skip keyword
        assert engine._sessions[PHONE_A].current_step == Step.CONSENT
        assert engine._sessions[PHONE_A].collected_data.get("note") == ""


# ---------------------------------------------------------------------------
# Human handoff
# ---------------------------------------------------------------------------


class TestHumanHandoff:
    async def test_handoff_option_returns_handoff_message(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        msgs = await send(engine, PHONE_A, button_id="opt_1", button_title="Falar com alguém")
        assert len(msgs) == 1
        assert msgs[0].text == HUMAN_HANDOFF_MESSAGE

    async def test_handoff_cleans_up_session(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, button_id="opt_1", button_title="Falar com alguém")
        assert PHONE_A not in engine._sessions

    async def test_handoff_does_not_write_lead(
        self, engine: ConversationEngine, mock_sheets: AsyncMock
    ) -> None:
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, button_id="opt_1", button_title="Falar com alguém")
        mock_sheets.write_lead.assert_not_called()


# ---------------------------------------------------------------------------
# LGPD consent denial
# ---------------------------------------------------------------------------


class TestConsentDenial:
    async def test_consent_denial_returns_lgpd_message(self, engine: ConversationEngine) -> None:
        await run_to_consent(engine, PHONE_A)
        assert engine._sessions[PHONE_A].current_step == Step.CONSENT
        msgs = await send(engine, PHONE_A, button_id="opt_1", button_title="Não")
        assert len(msgs) == 1
        assert msgs[0].text == LGPD_DECLINED_MESSAGE

    async def test_consent_denial_cleans_up_session(self, engine: ConversationEngine) -> None:
        await run_to_consent(engine, PHONE_A)
        await send(engine, PHONE_A, button_id="opt_1", button_title="Não")
        assert PHONE_A not in engine._sessions

    async def test_consent_denial_does_not_persist_data(
        self, engine: ConversationEngine, mock_sheets: AsyncMock
    ) -> None:
        await run_to_consent(engine, PHONE_A)
        await send(engine, PHONE_A, button_id="opt_1", button_title="Não")
        mock_sheets.write_lead.assert_not_called()


# ---------------------------------------------------------------------------
# Invalid input handling
# ---------------------------------------------------------------------------


class TestInvalidInput:
    async def test_wrong_button_id_returns_invalid_message(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        msgs = await send(engine, PHONE_A, button_id="opt_99", button_title="bogus")
        assert any(INVALID_INPUT_MESSAGE in m.text for m in msgs)

    async def test_wrong_button_id_does_not_advance_step(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, button_id="opt_99", button_title="bogus")
        assert engine._sessions[PHONE_A].current_step == Step.GREETING

    async def test_invalid_reprompt_includes_original_prompt(self, engine: ConversationEngine) -> None:
        """After invalid input the engine sends the error + the step prompt again."""
        await send(engine, PHONE_A, text="Oi")
        msgs = await send(engine, PHONE_A, button_id="opt_99", button_title="bogus")
        # First message is the error, second is the re-prompt
        assert len(msgs) == 2
        assert msgs[0].text == INVALID_INPUT_MESSAGE

    async def test_empty_text_on_list_step_reprompts(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")       # at WHO_FOR (list step)
        msgs = await send(engine, PHONE_A, text="")
        assert any(INVALID_INPUT_MESSAGE in m.text for m in msgs)
        assert engine._sessions[PHONE_A].current_step == Step.WHO_FOR

    async def test_out_of_range_number_on_list_step(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")       # at WHO_FOR (4 options)
        msgs = await send(engine, PHONE_A, text="99")
        assert any(INVALID_INPUT_MESSAGE in m.text for m in msgs)
        assert engine._sessions[PHONE_A].current_step == Step.WHO_FOR

    async def test_non_numeric_text_on_list_step(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")
        msgs = await send(engine, PHONE_A, text="nope")
        assert any(INVALID_INPUT_MESSAGE in m.text for m in msgs)
        assert engine._sessions[PHONE_A].current_step == Step.WHO_FOR


# ---------------------------------------------------------------------------
# Session isolation
# ---------------------------------------------------------------------------


class TestSessionIsolation:
    async def test_two_phones_independent_sessions(self, engine: ConversationEngine) -> None:
        """Sessions for different phone numbers never cross-contaminate."""
        # Advance PHONE_A one step past greeting
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")
        assert engine._sessions[PHONE_A].current_step == Step.WHO_FOR

        # Start PHONE_B fresh
        await send(engine, PHONE_B, text="Olá")
        assert engine._sessions[PHONE_B].current_step == Step.GREETING
        # PHONE_A should be unaffected
        assert engine._sessions[PHONE_A].current_step == Step.WHO_FOR

    async def test_two_phones_collect_independent_data(self, engine: ConversationEngine) -> None:
        """Data stored for one phone must not appear in the other's session."""
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Sim")  # advance PHONE_A
        await send(engine, PHONE_A, text="1")                                # who_for = Para mim

        await send(engine, PHONE_B, text="Oi")
        await send(engine, PHONE_B, button_id="opt_0", button_title="Sim")
        await send(engine, PHONE_B, text="2")                                # who_for = Para meu filho(a)

        assert engine._sessions[PHONE_A].collected_data["who_for"] == "Para mim"
        assert engine._sessions[PHONE_B].collected_data["who_for"] == "Para meu filho(a)"

    async def test_message_after_terminal_guard(self, engine: ConversationEngine) -> None:
        """A session artificially stuck at CLOSING returns an empty list."""
        engine._sessions[PHONE_A] = UserSession(
            phone=PHONE_A,
            whatsapp_name="Test",
            current_step=Step.CLOSING,
        )
        msgs = await send(engine, PHONE_A, text="anything")
        assert msgs == []

    async def test_message_after_human_handoff_guard(self, engine: ConversationEngine) -> None:
        """A session artificially stuck at HUMAN_HANDOFF returns an empty list."""
        engine._sessions[PHONE_A] = UserSession(
            phone=PHONE_A,
            whatsapp_name="Test",
            current_step=Step.HUMAN_HANDOFF,
        )
        msgs = await send(engine, PHONE_A, text="anything")
        assert msgs == []
