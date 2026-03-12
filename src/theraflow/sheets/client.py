"""Google Sheets lead storage client.

Provides :class:`SheetsClient` for appending lead records to a Google
Spreadsheet via the ``gspread`` library, authenticated with a service account
JSON key file.  Synchronous gspread calls are offloaded to a thread-pool
executor so they never block the asyncio event loop.

Typical usage::

    from theraflow.sheets.client import SheetsClient, LeadData, calculate_score
    from theraflow.config import settings

    client = SheetsClient(
        service_account_json=settings.google_service_account_json,
        sheet_id=settings.google_sheets_id,
    )

    score, lead_quality = calculate_score(collected_data)
    lead = LeadData(score=score, status="new", **fields)
    await client.write_lead(lead)
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import gspread
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from pydantic import BaseModel, Field

from theraflow.logging import get_logger
from theraflow.utils import mask_phone

log = get_logger(__name__)

# Google API scopes required for read/write access to Sheets and Drive.
_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Canonical column order — must match the LeadData field declaration order.
COLUMNS: list[str] = [
    "lead_id",
    "timestamp",
    "whatsapp_name",
    "phone_number",
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
    "score",
    "status",
    "lead_quality",
    "risk_level",
    "intent",
    "confidence",
]

COLUMN_HEADERS: list[str] = [
    "ID do Lead",
    "Data/Hora",
    "Nome WhatsApp",
    "Telefone",
    "Para quem",
    "Gênero",
    "Faixa etária",
    "Cidade",
    "Formato",
    "Primeira terapia",
    "Tema",
    "Urgência",
    "Horário preferido",
    "Interesse em agendar",
    "Observação",
    "Consentimento LGPD",
    "Pontuação",
    "Status",
    "Qualidade do Lead",
    "Nível de Risco",
    "Intenção",
    "Confiança",
]

FOLLOW_UP_COLUMNS: list[str] = [
    "follow_up_id",
    "timestamp",
    "whatsapp_name",
    "phone_number",
    "who_for",
    "gender",
    "topic",
    "urgency",
    "status",
]

FOLLOW_UP_HEADERS: list[str] = [
    "ID",
    "Data/Hora",
    "Nome WhatsApp",
    "Telefone",
    "Para quem",
    "Gênero",
    "Tema",
    "Urgência",
    "Status",
]

CONVERSATION_LOG_COLUMNS: list[str] = [
    "timestamp",
    "phone_number",
    "whatsapp_name",
    "step",
    "direction",
    "content",
]

CONVERSATION_LOG_HEADERS: list[str] = [
    "Data/Hora",
    "Telefone",
    "Nome WhatsApp",
    "Etapa",
    "Direção",
    "Conteúdo",
]


# ---------------------------------------------------------------------------
# Scoring helper
# ---------------------------------------------------------------------------

# Topic values considered too generic to award the clear-pain signal.
_VAGUE_TOPICS: frozenset[str] = frozenset({
    "ok", "sim", "não", "nao", "ajuda", "help", "outro", "outros", "other",
    "nada", "tudo", "etc", "geral", "normal", "qualquer",
})


def calculate_score(data: dict[str, Any]) -> tuple[int, str]:
    """Calculate a multi-axis lead quality score from collected conversation data.

    Scoring axes (additive):

    * Clear topic/pain demonstrated → **+20**
    * Name and phone number provided → **+15**
    * Scheduling or pricing interest shown → **+20**
    * Terms accepted (afternoon + R$60) → **+15**
    * Agreed to schedule soon → **+20**
    * Vague / single-word responses throughout → **-10**
    * Completed the full flow → **+10**

    ``lead_quality`` label by total score:

    * **< 30**  → ``"cold"``
    * **30-59** → ``"warm"``
    * **60+**   → ``"hot"``

    Args:
        data: Collected lead data keyed by conversation ``data_key`` values.

    Returns:
        A ``(score, lead_quality)`` tuple where *score* is the integer point
        total and *lead_quality* is one of ``"cold"``, ``"warm"``, or ``"hot"``.
    """
    score = 0

    topic: str = data.get("topic", "") or ""
    urgency: str = data.get("urgency", "") or ""
    appointment: str = data.get("appointment_interest", "") or ""
    whatsapp_name: str = data.get("whatsapp_name", "") or ""
    phone_number: str = data.get("phone_number", "") or ""

    # +20: demonstrated clear pain / topic
    if topic and topic.lower() not in _VAGUE_TOPICS and len(topic) > 2:
        score += 20

    # +15: provided name and contact number
    if whatsapp_name and phone_number:
        score += 15

    # +20: expressed interest in scheduling
    if urgency in ("O quanto antes", "Nesta semana", "Neste mês"):
        score += 20

    # +15: wants to book appointment
    if appointment == "Sim":
        score += 15

    # +20: committed to starting soon
    if urgency in ("O quanto antes", "Nesta semana"):
        score += 20

    # +10: provided a personal note
    if data.get("note"):
        score += 10

    # -10: vague / single-word answers across multiple fields
    vague_count = sum(
        1
        for v in data.values()
        if isinstance(v, str) and 0 < len(v.strip()) <= 2
    )
    if vague_count >= 2:
        score -= 10

    if score >= 60:
        lead_quality = "hot"
    elif score >= 30:
        lead_quality = "warm"
    else:
        lead_quality = "cold"

    return score, lead_quality


def derive_intent(data: dict[str, Any]) -> str:
    """Derive the user's primary intent from collected conversation data.

    Returns:
        * ``"crisis"``  — if ``risk_level`` is set to anything other than
          ``"none"`` (highest priority; checked first).
        * ``"booking"`` — if the user accepted a scheduling option (i.e.
          ``scheduling`` is present and is not ``"Ainda estou pensando"``).
        * ``"info"``    — if a topic was provided but scheduling was explicitly
          declined (``"Ainda estou pensando"``).
        * ``"unclear"`` — fallback when intent cannot be determined.

    Args:
        data: Collected lead data keyed by conversation ``data_key`` values.
    """
    risk_level: str = data.get("risk_level", "none") or "none"
    urgency: str = data.get("urgency", "") or ""
    topic: str = data.get("topic", "") or ""

    if risk_level != "none":
        return "crisis"

    if urgency and urgency != "Ainda estou pensando":
        return "booking"

    if topic and urgency == "Ainda estou pensando":
        return "info"

    return "unclear"


# ---------------------------------------------------------------------------
# Lead data model
# ---------------------------------------------------------------------------


class LeadData(BaseModel):
    """Pydantic model representing a complete lead record for storage.

    Field order matches :data:`COLUMNS` and the Google Sheet column layout.

    Attributes:
        lead_id: Unique UUID4 identifier generated automatically.
        timestamp: ISO 8601 UTC timestamp of lead creation (auto-generated).
        whatsapp_name: Contact's WhatsApp display name.
        phone_number: E.164 phone number without the leading ``+``.
        who_for: Who the therapy is intended for.
        gender: Reported gender.
        age_group: Age group bucket.
        city: City of residence.
        format: Preferred therapy format (e.g. online / presencial).
        first_therapy: Whether this is the contact's first time in therapy.
        topic: Main topic or concern to address.
        urgency: Desired start timeframe.
        preferred_time: Preferred appointment time slot.
        appointment_interest: Whether the contact wants to schedule an appointment.
        note: Optional free-text note provided by the contact.
        consent: LGPD consent response.
        score: Computed priority score (see :func:`calculate_score`).
        status: Lead status — defaults to ``"new"``.
    """

    lead_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    whatsapp_name: str
    phone_number: str
    who_for: str
    gender: str
    age_group: str
    city: str
    format: str
    first_therapy: str
    topic: str
    urgency: str
    preferred_time: str
    appointment_interest: str
    note: str
    consent: str
    score: int
    status: str = "new"
    lead_quality: str = "cold"
    risk_level: str = "none"
    intent: str = "unclear"
    confidence: float = 0.0

    def to_row(self) -> list[str | int]:
        """Return an ordered list of values matching :data:`COLUMNS`.

        Returns:
            Values in the same order as :data:`COLUMNS`, ready to pass
            directly to ``gspread``'s ``append_row()``.
        """
        return [getattr(self, col) for col in COLUMNS]


class FollowUpData(BaseModel):
    """Data for a follow-up contact (declined appointment)."""

    follow_up_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    whatsapp_name: str
    phone_number: str
    who_for: str
    gender: str
    topic: str
    urgency: str
    status: str = "pendente"

    def to_row(self) -> list[str]:
        return [getattr(self, col) for col in FOLLOW_UP_COLUMNS]


# ---------------------------------------------------------------------------
# Sheets client
# ---------------------------------------------------------------------------


class SheetsClient:
    """Google Sheets client for persisting lead records.

    Authenticates once using a service account JSON key file, then appends
    one row per lead to the first worksheet of the target spreadsheet.
    All synchronous ``gspread`` calls are offloaded to a thread-pool executor
    so the asyncio event loop is never blocked.

    Attributes:
        _sheet_id: Google Spreadsheet ID (from the sheet URL).
        _credentials: Authenticated :class:`ServiceAccountCredentials`
            instance used to authorise ``gspread``.
    """

    def __init__(self, service_account_json: str, sheet_id: str) -> None:
        """Initialise the client, load credentials, and cache the worksheet.

        Args:
            service_account_json: Absolute or relative path to the Google
                service account JSON key file.
            sheet_id: Google Spreadsheet ID (the long string in the sheet URL
                between ``/d/`` and ``/edit``).
        """
        self._sheet_id = sheet_id
        self._credentials: ServiceAccountCredentials = (
            ServiceAccountCredentials.from_service_account_file(
                service_account_json,
                scopes=_SCOPES,
            )
        )
        self._gc = gspread.authorize(self._credentials)
        self._spreadsheet = self._gc.open_by_key(self._sheet_id)
        self._worksheet = self._spreadsheet.sheet1
        self._follow_up_worksheet = self._get_or_create_follow_up_sheet()
        self._conversation_log_worksheet = self._get_or_create_conversation_log_sheet()
        self._ensure_headers()
        self._ensure_follow_up_headers()
        self._ensure_conversation_log_headers()
        log.info(
            "sheets_client_initialized",
            sheet_id=sheet_id,
            service_account_json=service_account_json,
        )

    # ------------------------------------------------------------------
    # Private helpers (synchronous — run in executor)
    # ------------------------------------------------------------------

    def _get_or_create_conversation_log_sheet(self) -> gspread.Worksheet:
        """Get or create the 'Conversation Log' worksheet tab."""
        try:
            return self._spreadsheet.worksheet("Conversation Log")
        except gspread.exceptions.WorksheetNotFound:
            ws = self._spreadsheet.add_worksheet(
                title="Conversation Log", rows=5000, cols=len(CONVERSATION_LOG_COLUMNS),
            )
            log.info("sheets_conversation_log_tab_created")
            return ws

    def _get_or_create_follow_up_sheet(self) -> gspread.Worksheet:
        """Get or create the 'Follow Up' worksheet tab."""
        try:
            return self._spreadsheet.worksheet("Follow Up")
        except gspread.exceptions.WorksheetNotFound:
            ws = self._spreadsheet.add_worksheet(title="Follow Up", rows=1000, cols=len(FOLLOW_UP_COLUMNS))
            log.info("sheets_follow_up_tab_created")
            return ws

    def _ensure_conversation_log_headers(self) -> None:
        """Write column headers to row 1 of Conversation Log sheet if empty."""
        try:
            first_row = self._conversation_log_worksheet.row_values(1)
            if not first_row:
                self._conversation_log_worksheet.append_row(
                    CONVERSATION_LOG_HEADERS, value_input_option="USER_ENTERED",
                )
                self._conversation_log_worksheet.format("1:1", {
                    "textFormat": {"bold": True},
                    "backgroundColor": {"red": 0.92, "green": 0.94, "blue": 0.98},
                })
                self._conversation_log_worksheet.freeze(rows=1)
                log.info("sheets_conversation_log_headers_written")
        except Exception:
            log.warning("sheets_conversation_log_headers_check_failed")

    def _ensure_follow_up_headers(self) -> None:
        """Write column headers to row 1 of Follow Up sheet if empty."""
        try:
            first_row = self._follow_up_worksheet.row_values(1)
            if not first_row:
                self._follow_up_worksheet.append_row(FOLLOW_UP_HEADERS, value_input_option="USER_ENTERED")
                self._follow_up_worksheet.format("1:1", {
                    "textFormat": {"bold": True},
                    "backgroundColor": {"red": 0.98, "green": 0.92, "blue": 0.9},
                })
                self._follow_up_worksheet.freeze(rows=1)
                log.info("sheets_follow_up_headers_written")
        except Exception:
            log.warning("sheets_follow_up_headers_check_failed")

    def _ensure_headers(self) -> None:
        """Write column headers to row 1 and format the main sheet."""
        try:
            # Rename tab if still default
            if self._worksheet.title == "Sheet1":
                self._worksheet.update_title("Leads")
                log.info("sheets_tab_renamed", title="Leads")

            first_row = self._worksheet.row_values(1)
            if not first_row:
                self._worksheet.append_row(COLUMN_HEADERS, value_input_option="USER_ENTERED")
                # Bold + freeze header row
                self._worksheet.format("1:1", {
                    "textFormat": {"bold": True},
                    "backgroundColor": {"red": 0.9, "green": 0.93, "blue": 0.98},
                })
                self._worksheet.freeze(rows=1)
                log.info("sheets_headers_written")
        except Exception:
            log.warning("sheets_headers_check_failed")

    def _reauthorize(self) -> None:
        """Re-authorize gspread and refresh the cached worksheets."""
        self._gc = gspread.authorize(self._credentials)
        self._spreadsheet = self._gc.open_by_key(self._sheet_id)
        self._worksheet = self._spreadsheet.sheet1
        self._follow_up_worksheet = self._get_or_create_follow_up_sheet()
        self._conversation_log_worksheet = self._get_or_create_conversation_log_sheet()

    def _append_row(self, row: list[str | int]) -> None:
        """Synchronously append *row* to the first worksheet.

        Uses the cached :attr:`_worksheet`.  On a
        :class:`gspread.exceptions.APIError` (e.g. token expiry) the client
        re-authorises once and retries before propagating the error.

        This method is **blocking** and must always be called via
        :meth:`write_lead` (which runs it in a thread executor).

        Args:
            row: Ordered list of cell values matching :data:`COLUMNS`.
        """
        try:
            self._worksheet.append_row(row, value_input_option="USER_ENTERED")
        except gspread.exceptions.APIError:
            log.warning("sheets_reauthorizing", reason="APIError on append_row")
            self._reauthorize()
            self._worksheet.append_row(row, value_input_option="USER_ENTERED")

    def _append_conversation_log_row(self, row: list[str]) -> None:
        """Synchronously append *row* to the Conversation Log worksheet."""
        try:
            self._conversation_log_worksheet.append_row(row, value_input_option="USER_ENTERED")
        except gspread.exceptions.APIError:
            log.warning("sheets_reauthorizing", reason="APIError on conversation_log append_row")
            self._reauthorize()
            self._conversation_log_worksheet.append_row(row, value_input_option="USER_ENTERED")

    def _append_follow_up_row(self, row: list[str]) -> None:
        """Synchronously append *row* to the Follow Up worksheet."""
        try:
            self._follow_up_worksheet.append_row(row, value_input_option="USER_ENTERED")
        except gspread.exceptions.APIError:
            log.warning("sheets_reauthorizing", reason="APIError on follow_up append_row")
            self._reauthorize()
            self._follow_up_worksheet.append_row(row, value_input_option="USER_ENTERED")

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def write_lead(self, lead: LeadData) -> None:
        """Append a lead record as a new row in the Google Sheet.

        The underlying ``gspread`` call is blocking; it is dispatched to the
        default :class:`~concurrent.futures.ThreadPoolExecutor` via
        ``asyncio.get_event_loop().run_in_executor`` to keep the event loop
        free.

        Args:
            lead: Fully populated :class:`LeadData` instance to persist.
        """
        row = lead.to_row()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._append_row, row)
        log.info(
            "sheets_lead_written",
            lead_id=lead.lead_id,
            phone=mask_phone(lead.phone_number),
            score=lead.score,
            priority=lead.status,
        )

    async def log_conversation(
        self,
        phone: str,
        name: str,
        step: str,
        direction: str,
        content: str,
    ) -> None:
        """Append a conversation log entry to the Conversation Log sheet.

        Args:
            phone: E.164 phone number.
            name: WhatsApp display name.
            step: Current conversation step.
            direction: ``"in"`` for user messages, ``"out"`` for bot responses.
            content: Message text (truncated to 500 chars).
        """
        row = [
            datetime.now(UTC).isoformat(),
            phone,
            name,
            step,
            direction,
            content[:500],
        ]
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._append_conversation_log_row, row)

    async def write_follow_up(self, follow_up: FollowUpData) -> None:
        """Append a follow-up record to the Follow Up tab."""
        row = follow_up.to_row()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._append_follow_up_row, row)
        log.info(
            "sheets_follow_up_written",
            follow_up_id=follow_up.follow_up_id,
            phone=mask_phone(follow_up.phone_number),
        )
