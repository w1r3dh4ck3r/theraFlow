"""Tests for ConversationEngine — the 14-step lead-qualification state machine."""

from __future__ import annotations

from typing import Any

import pytest
from unittest.mock import AsyncMock

from theraflow.conversation.engine import ConversationEngine, OutgoingMessage, UserSession
from theraflow.conversation.flow import (
    HUMAN_HANDOFF_MESSAGE,
    INVALID_INPUT_MESSAGE,
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


async def run_full_flow(engine: ConversationEngine, phone: str = PHONE_A) -> list[OutgoingMessage]:
    """Drive the engine through the entire flow and return the closing messages."""
    await send(engine, phone, text="Oi")                                            # creates session → GREETING
    await send(engine, phone, text="Maria")                                         # GREETING → WHO_FOR (name only)
    await send(engine, phone, text="Para mim")                                      # WHO_FOR → GENDER
    await send(engine, phone, button_id="opt_0", button_title="Mulher")              # GENDER → FIRST_THERAPY
    await send(engine, phone, text="Não")                                           # FIRST_THERAPY → TOPIC
    await send(engine, phone, text="Ansiedade")                                     # TOPIC → URGENCY
    await send(engine, phone, text="O quanto antes")                               # URGENCY → TERMS
    return await send(engine, phone, text="Sim")                                   # TERMS → CLOSING


# ---------------------------------------------------------------------------
# New-user greeting
# ---------------------------------------------------------------------------


class TestNewUser:
    async def test_first_message_returns_greeting_prompt(self, engine: ConversationEngine) -> None:
        msgs = await send(engine, PHONE_A, text="Oi")
        assert len(msgs) == 1
        assert "bem-vinda" in msgs[0].text.lower()

    async def test_greeting_is_plain_text(self, engine: ConversationEngine) -> None:
        """GREETING is a plain text message — no buttons."""
        msgs = await send(engine, PHONE_A, text="Oi")
        assert not msgs[0].is_interactive

    async def test_session_starts_at_greeting(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        assert PHONE_A in engine._sessions
        assert engine._sessions[PHONE_A].current_step == Step.GREETING

    async def test_greeting_response_stores_name(self, engine: ConversationEngine) -> None:
        """Answering GREETING with a name stores it and advances to WHO_FOR."""
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, text="Maria")
        assert engine._sessions[PHONE_A].collected_data.get("name") == "Maria"
        assert engine._sessions[PHONE_A].current_step == Step.WHO_FOR


# ---------------------------------------------------------------------------
# Full 14-step happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    async def test_greeting_response_advances_to_who_for(self, engine: ConversationEngine) -> None:
        """Answering GREETING with name advances to WHO_FOR."""
        await send(engine, PHONE_A, text="Oi")
        msgs = await send(engine, PHONE_A, text="Maria")
        assert engine._sessions[PHONE_A].current_step == Step.WHO_FOR
        assert engine._sessions[PHONE_A].collected_data["name"] == "Maria"

    async def test_who_for_stored_on_text_answer(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, text="Maria")                                # GREETING → WHO_FOR
        await send(engine, PHONE_A, text="Para mim")
        assert engine._sessions[PHONE_A].collected_data["who_for"] == "Para mim"
        assert engine._sessions[PHONE_A].current_step == Step.GENDER

    async def test_who_for_stored_on_numbered_answer(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, text="Maria")                                # GREETING → WHO_FOR
        await send(engine, PHONE_A, text="1")                                    # answer "Para mim"
        assert engine._sessions[PHONE_A].collected_data["who_for"] == "Para mim"
        assert engine._sessions[PHONE_A].current_step == Step.GENDER

    async def test_gender_stored(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, text="Maria")
        await send(engine, PHONE_A, text="Para mim")
        await send(engine, PHONE_A, button_id="opt_0", button_title="Mulher")
        assert engine._sessions[PHONE_A].collected_data["gender"] == "Mulher"
        assert engine._sessions[PHONE_A].current_step == Step.FIRST_THERAPY

    async def test_full_happy_path_reaches_closing(
        self, engine: ConversationEngine, mock_sheets: AsyncMock, mock_telegram: AsyncMock
    ) -> None:
        """Drive all steps; verify closing message and downstream calls."""
        msgs = await run_full_flow(engine, PHONE_A)

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
        await run_full_flow(engine, PHONE_A)

        lead = mock_sheets.write_lead.call_args[0][0]
        assert lead.who_for == "Para mim"
        assert lead.gender == "Mulher"
        assert lead.urgency == "O quanto antes"
        assert lead.phone_number == PHONE_A


# ---------------------------------------------------------------------------
# Human handoff
# ---------------------------------------------------------------------------


class TestHumanHandoff:
    async def test_handoff_keyword_returns_handoff_message(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        msgs = await send(engine, PHONE_A, text="Quero falar com alguém")
        assert len(msgs) == 1
        assert msgs[0].text == HUMAN_HANDOFF_MESSAGE

    async def test_handoff_cleans_up_session(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, text="Prefiro falar com uma pessoa")
        assert PHONE_A not in engine._sessions

    async def test_handoff_does_not_write_lead(
        self, engine: ConversationEngine, mock_sheets: AsyncMock
    ) -> None:
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, text="quero falar com alguém")
        mock_sheets.write_lead.assert_not_called()

    async def test_handoff_works_at_any_step(self, engine: ConversationEngine) -> None:
        """Handoff keyword works at any step, not just the beginning."""
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, text="Maria")                                # GREETING → WHO_FOR
        await send(engine, PHONE_A, text="Para mim")                             # WHO_FOR → GENDER
        msgs = await send(engine, PHONE_A, text="quero um atendente")            # at GENDER
        assert msgs[0].text == HUMAN_HANDOFF_MESSAGE
        assert PHONE_A not in engine._sessions


# ---------------------------------------------------------------------------
# Invalid input handling
# ---------------------------------------------------------------------------


class TestInvalidInput:
    async def test_empty_text_on_natural_step_reprompts(self, engine: ConversationEngine) -> None:
        await send(engine, PHONE_A, text="Oi")                                   # GREETING
        await send(engine, PHONE_A, text="Maria")                                # → WHO_FOR
        msgs = await send(engine, PHONE_A, text="")
        assert any(INVALID_INPUT_MESSAGE in m.text for m in msgs)
        assert engine._sessions[PHONE_A].current_step == Step.WHO_FOR

    async def test_natural_step_accepts_free_text(self, engine: ConversationEngine) -> None:
        """Natural steps accept free text and store it (LLM classifies in prod)."""
        await send(engine, PHONE_A, text="Oi")                                   # GREETING
        await send(engine, PHONE_A, text="Maria")                                # → WHO_FOR
        await send(engine, PHONE_A, text="para minha filha")
        assert engine._sessions[PHONE_A].current_step == Step.GENDER

    async def test_natural_step_accepts_exact_option(self, engine: ConversationEngine) -> None:
        """Natural steps resolve exact text matches to canonical options."""
        await send(engine, PHONE_A, text="Oi")                                   # GREETING
        await send(engine, PHONE_A, text="Maria")                                # → WHO_FOR
        await send(engine, PHONE_A, text="Para mim")
        assert engine._sessions[PHONE_A].collected_data["who_for"] == "Para mim"
        assert engine._sessions[PHONE_A].current_step == Step.GENDER

    async def test_natural_step_accepts_numbered_input(self, engine: ConversationEngine) -> None:
        """Natural steps still resolve 1-based numeric index."""
        await send(engine, PHONE_A, text="Oi")                                   # GREETING
        await send(engine, PHONE_A, text="Maria")                                # → WHO_FOR
        await send(engine, PHONE_A, text="1")
        assert engine._sessions[PHONE_A].collected_data["who_for"] == "Para mim"


# ---------------------------------------------------------------------------
# Session isolation
# ---------------------------------------------------------------------------


class TestSessionIsolation:
    async def test_two_phones_independent_sessions(self, engine: ConversationEngine) -> None:
        """Sessions for different phone numbers never cross-contaminate."""
        # Advance PHONE_A past GREETING
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, text="Maria")                            # GREETING → WHO_FOR
        assert engine._sessions[PHONE_A].current_step == Step.WHO_FOR

        # Start PHONE_B fresh
        await send(engine, PHONE_B, text="Olá")
        assert engine._sessions[PHONE_B].current_step == Step.GREETING
        # PHONE_A should be unaffected
        assert engine._sessions[PHONE_A].current_step == Step.WHO_FOR

    async def test_two_phones_collect_independent_data(self, engine: ConversationEngine) -> None:
        """Data stored for one phone must not appear in the other's session."""
        await send(engine, PHONE_A, text="Oi")
        await send(engine, PHONE_A, text="Maria")
        await send(engine, PHONE_A, text="Para mim")                         # who_for = Para mim

        await send(engine, PHONE_B, text="Oi")
        await send(engine, PHONE_B, text="João")
        await send(engine, PHONE_B, text="Para meu filho(a)")                # who_for = Para meu filho(a)

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
