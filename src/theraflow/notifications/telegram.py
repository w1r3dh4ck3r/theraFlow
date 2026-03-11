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

    async def send_lead_notification(self, lead: LeadData) -> None:
        """Send a lead summary notification to the configured Telegram chat.

        Formats the lead data as a Portuguese-language HTML message and POSTs
        it to the Telegram Bot API.  Any network or API error is caught,
        logged, and silently suppressed so the caller's flow is unaffected.

        Args:
            lead: A fully-populated :class:`~theraflow.sheets.client.LeadData`
                instance representing the new lead.
        """
        _, priority = calculate_score(lead.model_dump())
        text = self._format_message(lead, priority)

        log.info(
            "telegram_notify_attempt",
            lead_id=lead.lead_id,
            phone=mask_phone(lead.phone_number),
            score=lead.score,
            priority=priority,
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

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_message(lead: LeadData, priority: str) -> str:
        """Build the HTML-formatted notification message in Portuguese."""
        urgency_map = {
            "Hot": "URGENTE",
            "Warm": "Moderada",
            "Low": "Baixa",
        }
        urgency_label = urgency_map.get(priority, priority)

        header = "🔴 <b>LEAD URGENTE — QUER AGENDAR!</b>" if priority == "Hot" else "🟢 <b>Novo lead theraFlow</b>"
        from urllib.parse import quote
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
            f"<b>Condições (R$60 + tarde):</b> {lead.terms_agreement}\n"
            f"<b>Quando quer iniciar:</b> {lead.scheduling}\n\n"
            f"<b>Prioridade:</b> {urgency_label} ({lead.score} pts)"
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
