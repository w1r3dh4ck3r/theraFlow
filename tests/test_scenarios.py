"""Conversation scenario tests for TheraFlow.

End-to-end scenarios that exercise realistic user personas through the
conversation engine: hot leads, cold/hesitant users, spam, price-first
callers, and crisis situations.

All tests run with LLM_ENABLED=false (set in conftest.py) so the engine
uses deterministic scripted prompts and fuzzy matching.
"""

from __future__ import annotations

from typing import Any

import pytest
from unittest.mock import AsyncMock

from theraflow.conversation.engine import ConversationEngine, OutgoingMessage
from theraflow.conversation.flow import HUMAN_HANDOFF_MESSAGE, Step
from theraflow.safety.responses import CRISIS_MESSAGE_HIGH, CRISIS_MESSAGE_MEDIUM
from theraflow.sheets.client import calculate_score


PHONE = "5511999990100"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(mock_sheets: AsyncMock, mock_telegram: AsyncMock) -> ConversationEngine:
    return ConversationEngine(sheets_client=mock_sheets, telegram_notifier=mock_telegram)


async def send(
    engine: ConversationEngine,
    text: str = "",
    button_id: str = "",
    button_title: str = "",
    phone: str = PHONE,
) -> list[OutgoingMessage]:
    button_payload: dict[str, Any] | None = None
    if button_id:
        button_payload = {"id": button_id, "title": button_title or ""}
    return await engine.handle_message(
        phone=phone,
        name="Teste",
        text=text,
        button_payload=button_payload,
    )


# ---------------------------------------------------------------------------
# Scenario: Hot lead — motivated, clear answers, wants to start ASAP
# ---------------------------------------------------------------------------


class TestHotLeadScenario:
    async def test_hot_lead_completes_flow(
        self, engine: ConversationEngine, mock_sheets: AsyncMock, mock_telegram: AsyncMock
    ) -> None:
        """Motivated user answers everything clearly and wants to start immediately."""
        await send(engine, text="Olá, preciso de ajuda")
        await send(engine, text="Ana")                                      # name → WHO_FOR
        await send(engine, text="Para mim")                                 # WHO_FOR → GENDER
        await send(engine, button_id="opt_0", button_title="Mulher")        # GENDER → FIRST_THERAPY
        await send(engine, text="Não")                                      # FIRST_THERAPY → TOPIC
        await send(engine, text="Ansiedade")                                # TOPIC → URGENCY
        msgs = await send(engine, text="O quanto antes")                    # URGENCY → CLOSING

        assert "Perfeito" in msgs[0].text
        assert PHONE not in engine._sessions

        # Lead was saved
        mock_sheets.write_lead.assert_called_once()
        lead = mock_sheets.write_lead.call_args[0][0]
        assert lead.who_for == "Para mim"
        assert lead.topic == "Ansiedade"
        assert lead.urgency == "O quanto antes"

        # Score should be hot
        score, quality = calculate_score({
            "topic": "Ansiedade",
            "whatsapp_name": "Ana",
            "phone_number": PHONE,
            "urgency": "O quanto antes",
        })
        assert quality == "hot"

        # Telegram notification was sent
        mock_telegram.send_lead_notification.assert_called_once()


# ---------------------------------------------------------------------------
# Scenario: Cold lead — hesitant, still thinking, minimal engagement
# ---------------------------------------------------------------------------


class TestColdLeadScenario:
    async def test_cold_lead_still_completes(
        self, engine: ConversationEngine, mock_sheets: AsyncMock
    ) -> None:
        """Hesitant user completes flow but with low-commitment answers."""
        await send(engine, text="oi")
        await send(engine, text="Carla")                                    # name → WHO_FOR
        await send(engine, text="Outra pessoa")                             # WHO_FOR → GENDER
        await send(engine, button_id="opt_2", button_title="Prefiro não responder")
        await send(engine, text="Sim")                                      # FIRST_THERAPY → TOPIC
        await send(engine, text="Outro")                                    # TOPIC → URGENCY
        msgs = await send(engine, text="Ainda estou pensando")              # URGENCY → CLOSING

        assert "Perfeito" in msgs[0].text

        lead = mock_sheets.write_lead.call_args[0][0]
        assert lead.urgency == "Ainda estou pensando"

        # Cold score — "Outro" is vague, "Ainda estou pensando" gets no urgency bonus
        score, quality = calculate_score({
            "topic": "Outro",
            "urgency": "Ainda estou pensando",
            "whatsapp_name": "Carla",
            "phone_number": PHONE,
        })
        assert quality == "cold"


# ---------------------------------------------------------------------------
# Scenario: Spam / nonsense — gibberish input at natural steps
# ---------------------------------------------------------------------------


class TestSpamScenario:
    async def test_gibberish_at_greeting_stores_as_name(self, engine: ConversationEngine) -> None:
        """Gibberish text at GREETING is treated as a name (LLM disabled)."""
        await send(engine, text="hey")
        await send(engine, text="asdfghjkl")
        # With LLM disabled, raw text is stored as name
        assert engine._sessions[PHONE].collected_data.get("name") == "asdfghjkl"
        assert engine._sessions[PHONE].current_step == Step.WHO_FOR

    async def test_empty_messages_reprompt(self, engine: ConversationEngine) -> None:
        """Empty messages trigger reprompt, don't advance."""
        await send(engine, text="oi")
        await send(engine, text="João")                                     # → WHO_FOR
        msgs = await send(engine, text="")
        assert engine._sessions[PHONE].current_step == Step.WHO_FOR

        # Send again empty
        msgs = await send(engine, text="")
        assert engine._sessions[PHONE].current_step == Step.WHO_FOR

    async def test_wrong_button_at_gender_reprompts(self, engine: ConversationEngine) -> None:
        """Invalid button ID at GENDER (button step) reprompts."""
        await send(engine, text="oi")
        await send(engine, text="João")
        await send(engine, text="Para mim")                                 # → GENDER
        msgs = await send(engine, button_id="opt_99", button_title="Invalido")
        assert engine._sessions[PHONE].current_step == Step.GENDER


# ---------------------------------------------------------------------------
# Scenario: Price-first — asks about price, then continues
# ---------------------------------------------------------------------------


class TestPriceFirstScenario:
    async def test_price_question_treated_as_name(self, engine: ConversationEngine) -> None:
        """User whose first real answer mentions price — treated as name (no LLM)."""
        await send(engine, text="Quanto custa a sessão?")
        # First message creates session, GREETING returned
        assert PHONE in engine._sessions
        assert engine._sessions[PHONE].current_step == Step.GREETING

    async def test_price_user_continues_normally(
        self, engine: ConversationEngine, mock_sheets: AsyncMock
    ) -> None:
        """After initial price question, user can complete the flow normally."""
        await send(engine, text="Quanto custa?")
        await send(engine, text="Paula")                                    # name → WHO_FOR
        await send(engine, text="Para mim")                                 # WHO_FOR → GENDER
        await send(engine, button_id="opt_1", button_title="Homem")         # GENDER → FIRST_THERAPY
        await send(engine, text="Sim")                                      # FIRST_THERAPY → TOPIC
        await send(engine, text="Relacionamentos")                          # TOPIC → URGENCY
        msgs = await send(engine, text="Nesta semana")                      # URGENCY → CLOSING

        assert "Perfeito" in msgs[0].text
        lead = mock_sheets.write_lead.call_args[0][0]
        assert lead.topic == "Relacionamentos"
        assert lead.urgency == "Nesta semana"


# ---------------------------------------------------------------------------
# Scenario: Crisis — high risk at different points in conversation
# ---------------------------------------------------------------------------


class TestCrisisScenario:
    async def test_crisis_at_first_message(
        self, engine: ConversationEngine, mock_telegram: AsyncMock
    ) -> None:
        """HIGH-risk keyword in the very first message triggers safety response."""
        msgs = await send(engine, text="quero me matar")
        assert msgs[0].text == CRISIS_MESSAGE_HIGH
        assert PHONE not in engine._sessions
        mock_telegram.send_safety_alert.assert_called_once()

    async def test_crisis_mid_conversation(
        self, engine: ConversationEngine, mock_telegram: AsyncMock
    ) -> None:
        """HIGH-risk keyword mid-conversation ends session with crisis message."""
        await send(engine, text="oi")
        await send(engine, text="Maria")                                    # → WHO_FOR
        msgs = await send(engine, text="não quero mais viver")
        assert msgs[0].text == CRISIS_MESSAGE_HIGH
        assert PHONE not in engine._sessions

    async def test_medium_risk_continues_session(self, engine: ConversationEngine) -> None:
        """MEDIUM-risk sends warning but does NOT end the session."""
        await send(engine, text="oi")
        await send(engine, text="Maria")                                    # → WHO_FOR
        msgs = await send(engine, text="não aguento mais")
        assert msgs[0].text == CRISIS_MESSAGE_MEDIUM
        # Session should still exist
        assert PHONE in engine._sessions

    async def test_crisis_after_medium_can_continue(self, engine: ConversationEngine) -> None:
        """After medium-risk warning, user can continue the flow."""
        await send(engine, text="oi")
        await send(engine, text="Maria")                                    # → WHO_FOR
        await send(engine, text="estou em desespero")                       # MEDIUM
        # User continues at WHO_FOR (session preserved)
        assert engine._sessions[PHONE].current_step == Step.WHO_FOR
        await send(engine, text="Para mim")                                 # → GENDER
        assert engine._sessions[PHONE].current_step == Step.GENDER

    async def test_crisis_telegram_alert_contains_risk_level(
        self, engine: ConversationEngine, mock_telegram: AsyncMock
    ) -> None:
        """Telegram safety alert includes the risk level and matched terms."""
        await send(engine, text="estou pensando em suicídio")
        call_kwargs = mock_telegram.send_safety_alert.call_args[1]
        assert call_kwargs["risk_level"] == "high"
        assert "suicídio" in call_kwargs["matched_terms"]

    async def test_ansiosa_is_not_crisis(self, engine: ConversationEngine) -> None:
        """'estou ansiosa' is normal therapy language, not a crisis."""
        await send(engine, text="oi")
        await send(engine, text="Maria")                                    # → WHO_FOR
        msgs = await send(engine, text="estou muito ansiosa")
        # Should NOT be a crisis response — should be treated as normal input
        assert msgs[0].text != CRISIS_MESSAGE_HIGH
        assert msgs[0].text != CRISIS_MESSAGE_MEDIUM


# ---------------------------------------------------------------------------
# Scenario: Handoff at various steps
# ---------------------------------------------------------------------------


class TestHandoffScenarios:
    async def test_handoff_at_topic_step(
        self, engine: ConversationEngine, mock_telegram: AsyncMock
    ) -> None:
        """User requests human at TOPIC step."""
        await send(engine, text="oi")
        await send(engine, text="Ana")
        await send(engine, text="Para mim")
        await send(engine, button_id="opt_0", button_title="Mulher")
        await send(engine, text="Sim")                                      # → TOPIC
        msgs = await send(engine, text="quero falar com uma pessoa")
        assert msgs[0].text == HUMAN_HANDOFF_MESSAGE
        assert PHONE not in engine._sessions
        mock_telegram.send_handoff_alert.assert_called_once()

    async def test_handoff_keyword_variations(self, engine: ConversationEngine) -> None:
        """Various handoff keyword phrasings all trigger handoff."""
        keywords = [
            "quero falar com alguém",
            "preciso de atendimento humano",
            "quero um atendente",
            "prefiro falar com uma pessoa real",
        ]
        for i, kw in enumerate(keywords):
            phone = f"551199999{i:04d}"
            await send(engine, text="oi", phone=phone)
            msgs = await send(engine, text=kw, phone=phone)
            assert msgs[0].text == HUMAN_HANDOFF_MESSAGE, f"Failed for: {kw!r}"


# ---------------------------------------------------------------------------
# Scenario: Re-contact after completed flow
# ---------------------------------------------------------------------------


class TestRecontact:
    async def test_message_after_closing_starts_new_session(
        self, engine: ConversationEngine, mock_sheets: AsyncMock
    ) -> None:
        """After completing the flow, a new message starts a fresh session."""
        # Complete full flow
        await send(engine, text="oi")
        await send(engine, text="Maria")
        await send(engine, text="Para mim")
        await send(engine, button_id="opt_0", button_title="Mulher")
        await send(engine, text="Não")
        await send(engine, text="Ansiedade")
        await send(engine, text="O quanto antes")
        assert PHONE not in engine._sessions

        # New message starts fresh
        msgs = await send(engine, text="Oi de novo")
        assert PHONE in engine._sessions
        assert engine._sessions[PHONE].current_step == Step.GREETING
