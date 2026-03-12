"""Telegram notification sender for theraFlow lead alerts.

Sends a structured Portuguese-language message to a Telegram chat via the
Bot API whenever a new lead completes the WhatsApp qualification flow.

Typical usage::

    from theraflow.notifications.telegram import TelegramNotifier
    from theraflow.config import settings

    notifier = TelegramNotifier(http_client=client)
    await notifier.send_lead_notification(lead)
"""

from __future__ import annotations

import httpx

from theraflow.config import settings
from theraflow.logging import get_logger
from theraflow.sheets.client import LeadData, calculate_score
from theraflow.utils import mask_phone

log = get_logger(__name__)

_TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramNotifier:
    """Sends lead notifications to a Telegram chat via the Bot API.

    Configuration is read from the application :data:`~theraflow.config.settings`
    singleton (``telegram_bot_token`` and ``telegram_chat_id``).

    The notifier intentionally swallows all errors so that a Telegram outage
    or misconfiguration never crashes the lead-processing flow.  All
    outcomes are recorded with structlog.

    Args:
        http_client: A shared :class:`httpx.AsyncClient` instance managed by
            the application lifespan.  Reusing a single client improves
            connection-pool efficiency.
    """

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http_client: httpx.AsyncClient = http_client
        self._bot_token: str = settings.telegram_bot_token
        self._chat_id: str = settings.telegram_chat_id
        self._send_url: str = (
            f"{_TELEGRAM_API_BASE}/bot{self._bot_token}/sendMessage"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_safety_alert(
        self,
        phone: str,
        risk_level: str,
        matched_terms: list[str],
    ) -> None:
        """Send a crisis-safety alert to the configured Telegram chat.

        Formats a Portuguese-language alert with a priority tag, the masked
        phone number, and the matched risk terms.  Any network or API error is
        caught, logged, and silently suppressed so the safety response is still
        delivered to the user even when Telegram is unavailable.

        Args:
            phone: Sender's phone number (will be masked before sending).
            risk_level: ``'high'`` or ``'medium'`` — controls the priority tag.
            matched_terms: Human-readable list of terms that triggered the alert.
        """
        if risk_level == "high":
            priority_tag = "🚨 URGENTE"
        else:
            priority_tag = "⚠️ ATENÇÃO"

        terms_str = ", ".join(matched_terms) if matched_terms else "—"
        text = (
            f"<b>{priority_tag} — Alerta de segurança theraFlow</b>\n\n"
            f"<b>Telefone:</b> {mask_phone(phone)}\n"
            f"<b>Nível de risco:</b> {risk_level}\n"
            f"<b>Termos detectados:</b> {terms_str}"
        )

        log.info(
            "telegram_safety_alert_attempt",
            phone=mask_phone(phone),
            risk_level=risk_level,
            matched_terms=matched_terms,
        )

        try:
            await self._post_message(text)
        except httpx.HTTPStatusError as exc:
            log.error(
                "telegram_safety_alert_http_error",
                phone=mask_phone(phone),
                status_code=exc.response.status_code,
                response_text=exc.response.text,
            )
        except httpx.RequestError as exc:
            log.error(
                "telegram_safety_alert_request_error",
                phone=mask_phone(phone),
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "telegram_safety_alert_unexpected_error",
                phone=mask_phone(phone),
                error=str(exc),
            )
        else:
            log.info(
                "telegram_safety_alert_ok",
                phone=mask_phone(phone),
                risk_level=risk_level,
            )

    async def send_handoff_alert(self, phone: str, name: str) -> None:
        """Send an alert when a user requests to speak to a human.

        Args:
            phone: Sender's phone number.
            name: WhatsApp display name.
        """
        from urllib.parse import quote

        greeting = quote(
            f"Olá, {name}! Recebi seu contato e estou disponível para conversar."
        )
        wa_link = f"https://wa.me/{phone}?text={greeting}"
        text = (
            "📞 <b>SOLICITAÇÃO DE ATENDIMENTO HUMANO</b>\n\n"
            f"<b>Nome:</b> {name}\n"
            f"<b>Telefone:</b> <a href=\"{wa_link}\">{phone}</a>\n\n"
            "O contato solicitou falar diretamente com uma pessoa."
        )

        log.info("telegram_handoff_alert_attempt", phone=mask_phone(phone))

        try:
            await self._post_message(text)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "telegram_handoff_alert_error",
                phone=mask_phone(phone),
                error=str(exc),
            )
        else:
            log.info("telegram_handoff_alert_ok", phone=mask_phone(phone))

    async def send_lead_notification(self, lead: LeadData) -> None:
        """Send a lead summary notification to the configured Telegram chat.

        Formats the lead data as a Portuguese-language HTML message and POSTs
        it to the Telegram Bot API.  Any network or API error is caught,
        logged, and silently suppressed so the caller's flow is unaffected.

        Args:
            lead: A fully-populated :class:`~theraflow.sheets.client.LeadData`
                instance representing the new lead.
        """
        _, lead_quality = calculate_score(lead.model_dump())
        text = self._format_message(lead, lead_quality)

        log.info(
            "telegram_notify_attempt",
            lead_id=lead.lead_id,
            phone=mask_phone(lead.phone_number),
            score=lead.score,
            lead_quality=lead_quality,
            risk_level=lead.risk_level,
        )

        try:
            await self._post_message(text)
        except httpx.HTTPStatusError as exc:
            log.error(
                "telegram_notify_http_error",
                lead_id=lead.lead_id,
                status_code=exc.response.status_code,
                response_text=exc.response.text,
            )
        except httpx.RequestError as exc:
            log.error(
                "telegram_notify_request_error",
                lead_id=lead.lead_id,
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "telegram_notify_unexpected_error",
                lead_id=lead.lead_id,
                error=str(exc),
            )
        else:
            log.info(
                "telegram_notify_ok",
                lead_id=lead.lead_id,
                phone=mask_phone(lead.phone_number),
            )

    async def send_crisis_alert(
        self,
        phone: str,
        risk_level: str,
        matched_terms: list[str],
    ) -> None:
        """Send an immediate crisis alert to the configured Telegram chat.

        Intended to be called *during* the conversation flow (not at
        completion) so the therapist is notified as early as possible when
        risk-related language is detected.  The method is fire-and-forget:
        all errors are caught and logged without re-raising so the calling
        flow is never interrupted.

        Args:
            phone: E.164 phone number of the user (without leading ``+``).
            risk_level: Severity label detected by the risk classifier
                (e.g. ``"high"``, ``"medium"``).
            matched_terms: List of risk-related terms that triggered the alert.
        """
        wa_link = f"https://wa.me/{phone}"
        terms_str = ", ".join(matched_terms) if matched_terms else "—"
        text = (
            "🚨 <b>ALERTA DE CRISE</b>\n\n"
            f"<b>Telefone:</b> <a href=\"{wa_link}\">{phone}</a>\n"
            f"<b>Nível de risco:</b> {risk_level}\n"
            f"<b>Termos detectados:</b> {terms_str}"
        )

        log.warning(
            "telegram_crisis_alert_attempt",
            phone=mask_phone(phone),
            risk_level=risk_level,
            matched_terms=matched_terms,
        )

        try:
            await self._post_message(text)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "telegram_crisis_alert_error",
                phone=mask_phone(phone),
                error=str(exc),
            )
        else:
            log.warning(
                "telegram_crisis_alert_sent",
                phone=mask_phone(phone),
                risk_level=risk_level,
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_message(lead: LeadData, lead_quality: str) -> str:
        """Build the HTML-formatted notification message in Portuguese."""
        from urllib.parse import quote

        urgency_map = {
            "hot": "URGENTE",
            "warm": "Moderada",
            "cold": "Baixa",
        }
        urgency_label = urgency_map.get(lead_quality, lead_quality)

        # Header precedence: risk > hot lead > default
        if lead.risk_level != "none":
            header = "🚨 <b>URGENTE — RISCO DETECTADO</b>"
        elif lead.lead_quality == "hot":
            header = "🔥 <b>LEAD QUENTE</b>"
        else:
            header = "🟢 <b>Novo lead theraFlow</b>"

        greeting = quote(
            f"Olá, {lead.whatsapp_name}. Acabei de receber seu contato. "
            f"Você gostaria de falar um pouco sobre o que te incomoda "
            f"e te faz buscar por terapia nesse momento?"
        )
        wa_link = f"https://wa.me/{lead.phone_number}?text={greeting}"

        return (
            f"{header}\n\n"
            f"<b>Nome:</b> {lead.whatsapp_name}\n"
            f"<b>Telefone:</b> <a href=\"{wa_link}\">{lead.phone_number}</a>\n"
            f"<b>Para quem:</b> {lead.who_for}\n"
            f"<b>Gênero:</b> {lead.gender}\n"
            f"<b>Tema:</b> {lead.topic}\n"
            f"<b>Quando quer iniciar:</b> {lead.urgency}\n\n"
            f"<b>Prioridade:</b> {urgency_label} ({lead.score} pts)\n"
            f"<b>Qualidade do lead:</b> {lead.lead_quality}\n"
            f"<b>Nível de risco:</b> {lead.risk_level}"
        )

    async def _post_message(self, text: str) -> None:
        """POST a message to the Telegram sendMessage endpoint.

        Args:
            text: HTML-formatted message body.

        Raises:
            httpx.HTTPStatusError: On non-2xx Telegram API responses.
            httpx.RequestError: On network-level failures.
        """
        payload: dict[str, str] = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        response = await self._http_client.post(self._send_url, json=payload)
        response.raise_for_status()
