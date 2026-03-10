"""Conversation state machine for the TheraFlow lead-qualification flow.

The :class:`ConversationEngine` keeps in-memory sessions keyed by the user's
WhatsApp phone number and drives each contact through the 14-step intake flow
defined in :mod:`theraflow.conversation.flow`.

Typical usage inside the webhook handler::

    engine = ConversationEngine(
        sheets_client=sheets_client,
        telegram_notifier=telegram_notifier,
    )

    messages = await engine.handle_message(
        phone="5511999999999",
        name="Maria",
        text=None,
        button_payload={"id": "opt_0", "title": "Sim"},
    )
    for msg in messages:
        if msg.is_interactive:
            await send_button_message(phone, msg.text, msg.buttons)
        else:
            await send_text_message(phone, msg.text)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from theraflow.conversation.flow import (
    HUMAN_HANDOFF_MESSAGE,
    HUMAN_HANDOFF_OPTION,
    INVALID_INPUT_MESSAGE,
    LGPD_DECLINE_OPTION,
    LGPD_DECLINED_MESSAGE,
    SKIP_KEYWORDS,
    STEP_CONFIGS,
    Step,
    StepConfig,
    next_step,
)
from theraflow.logging import get_logger
from theraflow.sheets.client import LeadData, calculate_score

if TYPE_CHECKING:
    from theraflow.notifications.telegram import TelegramNotifier
    from theraflow.sheets.client import SheetsClient

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Outgoing message type
# ---------------------------------------------------------------------------


@dataclass
class OutgoingMessage:
    """A single message to be sent back to the user.

    Attributes:
        text: Message body text.  Used as the body for both plain-text and
            interactive button messages.
        buttons: Button descriptors for interactive messages.  Each dict
            must contain ``"id"`` (≤256 chars) and ``"title"`` (≤20 chars)
            keys as expected by
            :func:`~theraflow.whatsapp.sender.send_button_message`.
            Empty list means a plain-text message.
    """

    text: str
    buttons: list[dict[str, str]] = field(default_factory=list)

    @property
    def is_interactive(self) -> bool:
        """True when this message should be sent as an interactive button message."""
        return bool(self.buttons)


# ---------------------------------------------------------------------------
# Session model
# ---------------------------------------------------------------------------


class UserSession(BaseModel):
    """Mutable conversation state for a single WhatsApp contact.

    Attributes:
        phone: E.164 phone number without the leading ``+``.
        whatsapp_name: Display name from the contact's WhatsApp profile.
            May be an empty string if the platform did not provide one.
        current_step: The step we are *waiting for the user to answer*.
            Advances with each valid response.
        collected_data: Answers gathered so far, keyed by
            :attr:`~theraflow.conversation.flow.StepConfig.data_key`.
        created_at: UTC timestamp of session creation.
    """

    phone: str
    whatsapp_name: str
    current_step: Step
    collected_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )


# ---------------------------------------------------------------------------
# Conversation engine
# ---------------------------------------------------------------------------


class ConversationEngine:
    """Stateful engine driving the 14-step lead-qualification flow.

    All sessions are held in ``_sessions`` (a plain Python dict) for
    simplicity.  A single instance should be shared across the application
    lifetime and is created during the FastAPI lifespan with injected
    :class:`~theraflow.sheets.client.SheetsClient` and
    :class:`~theraflow.notifications.telegram.TelegramNotifier` dependencies.

    Attributes:
        _sessions: Active sessions keyed by phone number (E.164, no ``+``).
        _sheets_client: Client for persisting lead records to Google Sheets.
            May be ``None`` if not configured (lead storage is skipped).
        _telegram_notifier: Notifier for sending Telegram lead alerts.
            May be ``None`` if not configured (notifications are skipped).
    """

    def __init__(
        self,
        sheets_client: SheetsClient | None = None,
        telegram_notifier: TelegramNotifier | None = None,
    ) -> None:
        self._sessions: dict[str, UserSession] = {}
        self._sheets_client = sheets_client
        self._telegram_notifier = telegram_notifier

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle_message(
        self,
        phone: str,
        name: str,
        text: str | None,
        button_payload: dict[str, Any] | None,
    ) -> list[OutgoingMessage]:
        """Process one inbound message and return the reply message(s).

        This is the single entry point called by the webhook handler for every
        inbound text or button-reply message.  It implements the following
        state-machine logic:

        1. **New contact** — create a session at GREETING and return the
           greeting prompt.
        2. **Existing session** — validate the user's answer for the current
           step, store it in ``collected_data``, and advance to the next step.
        3. **Special branches**:

           * GREETING + "Prefiro falar com uma pessoa" → human handoff.
           * CONSENT + "Não" → discard all collected data (LGPD compliance).

        4. **CLOSING** — trigger lead-storage and assistant-notification hooks,
           return the closing message, and clean up the session.
        5. **Post-terminal messages** — silently ignored (returns ``[]``).

        Args:
            phone: Sender's phone number in E.164 format without the leading
                ``+`` (e.g. ``"5511999999999"``).
            name: WhatsApp display name for the contact (may be empty string).
            text: Message body for plain-text messages; ``None`` for button
                replies.
            button_payload: Parsed ``button_reply`` dict containing ``"id"``
                and ``"title"`` keys; ``None`` for plain-text messages.

        Returns:
            Ordered list of :class:`OutgoingMessage` objects.  The caller
            should send them in order without modification.
        """
        button_id: str | None = (button_payload or {}).get("id")
        button_title: str | None = (button_payload or {}).get("title")

        session = self._sessions.get(phone)

        # ----------------------------------------------------------------
        # New contact — create session and return the greeting prompt
        # ----------------------------------------------------------------
        if session is None:
            session = UserSession(
                phone=phone,
                whatsapp_name=name,
                current_step=Step.GREETING,
                collected_data={},
            )
            self._sessions[phone] = session
            log.info(
                "conversation_session_created",
                phone=phone,
                name=name,
            )
            return self._build_prompt(Step.GREETING)

        # ----------------------------------------------------------------
        # Guard: ignore messages after the session has reached a terminal step
        # ----------------------------------------------------------------
        current: Step = session.current_step
        if current in (Step.CLOSING, Step.HUMAN_HANDOFF):
            log.debug(
                "conversation_message_after_terminal",
                phone=phone,
                step=current,
            )
            return []

        config: StepConfig = STEP_CONFIGS[current]

        # ----------------------------------------------------------------
        # Resolve the user's answer
        # ----------------------------------------------------------------
        answer: str | None = config.resolve_answer(text, button_id, button_title)

        # ----------------------------------------------------------------
        # Step-specific answer normalisation
        # ----------------------------------------------------------------

        # Step 12 — OPTIONAL_NOTE: treat None, empty input, or skip keywords
        # as an intentional skip (store empty string rather than nothing).
        if current == Step.OPTIONAL_NOTE:
            raw = (text or "").strip()
            if answer is None or raw.lower() in SKIP_KEYWORDS:
                answer = ""

        # ----------------------------------------------------------------
        # Invalid input — reprompt without advancing the step
        # ----------------------------------------------------------------
        if answer is None and not config.accepts_free_text:
            log.debug(
                "conversation_invalid_input",
                phone=phone,
                step=current,
                text=text,
                button_id=button_id,
            )
            return [OutgoingMessage(text=INVALID_INPUT_MESSAGE), *self._build_prompt(current)]

        # ----------------------------------------------------------------
        # Special branch: human handoff (GREETING → "Prefiro falar com uma pessoa")
        # ----------------------------------------------------------------
        if current == Step.GREETING and answer == HUMAN_HANDOFF_OPTION:
            log.info("conversation_human_handoff", phone=phone)
            self._cleanup_session(phone)
            return [OutgoingMessage(text=HUMAN_HANDOFF_MESSAGE)]

        # ----------------------------------------------------------------
        # Special branch: LGPD consent declined (CONSENT → "Não")
        # ----------------------------------------------------------------
        if current == Step.CONSENT and answer == LGPD_DECLINE_OPTION:
            log.info("conversation_lgpd_declined", phone=phone)
            self._cleanup_session(phone)
            return [OutgoingMessage(text=LGPD_DECLINED_MESSAGE)]

        # ----------------------------------------------------------------
        # Store the answer in collected_data
        # ----------------------------------------------------------------
        if config.data_key is not None and answer is not None:
            session.collected_data[config.data_key] = answer
            log.debug(
                "conversation_answer_stored",
                phone=phone,
                step=current,
                key=config.data_key,
                value=answer,
            )

        # ----------------------------------------------------------------
        # Advance to the next step
        # ----------------------------------------------------------------
        advance_to: Step | None = next_step(current)
        if advance_to is None:
            # Already at the final step — clean up defensively.
            log.warning("conversation_no_next_step", phone=phone, step=current)
            self._cleanup_session(phone)
            return []

        session.current_step = advance_to
        log.info(
            "conversation_step_advanced",
            phone=phone,
            from_step=current,
            to_step=advance_to,
        )

        # ----------------------------------------------------------------
        # Terminal step: CLOSING — persist lead, notify, clean up
        # ----------------------------------------------------------------
        if advance_to == Step.CLOSING:
            await self._on_conversation_complete(session)
            messages = self._build_prompt(Step.CLOSING)
            self._cleanup_session(phone)
            return messages

        return self._build_prompt(advance_to)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_prompt(self, step: Step) -> list[OutgoingMessage]:
        """Build the outgoing message(s) for a given step.

        For button steps returns a single interactive message.  For all other
        steps (numbered-list or free-text) returns a single plain-text message.

        Args:
            step: The step whose prompt should be rendered.

        Returns:
            List of :class:`OutgoingMessage` (usually length 1).  Returns an
            empty list if *step* is not in :data:`~theraflow.conversation.flow.STEP_CONFIGS`.
        """
        config = STEP_CONFIGS.get(step)
        if config is None:
            return []

        if config.use_buttons:
            return [OutgoingMessage(text=config.prompt, buttons=config.to_buttons())]

        # Numbered-list or free-text steps — send as plain text.
        return [OutgoingMessage(text=config.full_prompt())]

    def _cleanup_session(self, phone: str) -> None:
        """Remove the active session for *phone*, if any."""
        self._sessions.pop(phone, None)
        log.info("conversation_session_cleaned", phone=phone)

    async def _on_conversation_complete(self, session: UserSession) -> None:
        """Hook called when a contact completes the flow and gives LGPD consent.

        Assembles the full :class:`~theraflow.sheets.client.LeadData` record,
        logs it, persists it to Google Sheets, and sends a Telegram notification.

        Error handling:

        * If Sheets write fails — the exception is logged and suppressed so the
          closing message is still delivered to the user.
        * If Telegram notification fails — same: logged and suppressed.

        Args:
            session: The completed :class:`UserSession` with all collected data.
        """
        data = session.collected_data
        score, _priority = calculate_score(data)

        # Build the lead record; use empty-string defaults for any optional
        # fields that might be absent (e.g. note on a skipped step).
        _lead_fields: list[str] = [
            "who_for",
            "gender",
            "age_group",
            "city",
            "format",
            "first_therapy",
            "topic",
            "urgency",
            "preferred_time",
            "appointment_interest",
            "note",
            "consent",
        ]
        lead = LeadData(
            whatsapp_name=session.whatsapp_name,
            phone_number=session.phone,
            score=score,
            **{key: data.get(key, "") for key in _lead_fields},
        )

        log.info(
            "conversation_complete",
            phone=session.phone,
            lead_id=lead.lead_id,
            score=score,
        )

        # ----------------------------------------------------------------
        # Persist lead to Google Sheets
        # ----------------------------------------------------------------
        if self._sheets_client is not None:
            try:
                await self._sheets_client.write_lead(lead)
            except Exception:
                log.exception(
                    "conversation_sheets_write_failed",
                    phone=session.phone,
                    lead_id=lead.lead_id,
                )

        # ----------------------------------------------------------------
        # Send Telegram notification
        # ----------------------------------------------------------------
        if self._telegram_notifier is not None:
            try:
                await self._telegram_notifier.send_lead_notification(lead)
            except Exception:
                log.exception(
                    "conversation_telegram_notify_failed",
                    phone=session.phone,
                    lead_id=lead.lead_id,
                )
