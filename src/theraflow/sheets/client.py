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

    score, priority = calculate_score(collected_data)
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
]


# ---------------------------------------------------------------------------
# Scoring helper
# ---------------------------------------------------------------------------


def calculate_score(data: dict[str, Any]) -> tuple[int, str]:
    """Calculate a lead priority score from collected conversation data.

    Scoring rules (additive):

    * ``appointment_interest == "Sim"`` → **+3**
    * ``urgency == "O quanto antes"`` → **+2**
    * ``note`` is non-empty → **+1**

    Priority labels by total score:

    * **0-2** → ``"Low"``
    * **3-5** → ``"Warm"``
    * **6+**  → ``"Hot"``

    Args:
        data: Collected lead data keyed by conversation ``data_key`` values.

    Returns:
        A ``(score, priority)`` tuple where *score* is the integer point total
        and *priority* is one of ``"Low"``, ``"Warm"``, or ``"Hot"``.
    """
    score = 0

    if data.get("appointment_interest") == "Sim":
        score += 3
    if data.get("urgency") == "O quanto antes":
        score += 2
    if (data.get("note") or "").strip():
        score += 1

    if score >= 6:
        priority = "Hot"
    elif score >= 3:
        priority = "Warm"
    else:
        priority = "Low"

    return score, priority


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

    def to_row(self) -> list[str | int]:
        """Return an ordered list of values matching :data:`COLUMNS`.

        Returns:
            Values in the same order as :data:`COLUMNS`, ready to pass
            directly to ``gspread``'s ``append_row()``.
        """
        return [getattr(self, col) for col in COLUMNS]


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
        """Initialise the client and load service account credentials.

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
        log.info(
            "sheets_client_initialized",
            sheet_id=sheet_id,
            service_account_json=service_account_json,
        )

    # ------------------------------------------------------------------
    # Private helpers (synchronous — run in executor)
    # ------------------------------------------------------------------

    def _append_row(self, row: list[str | int]) -> None:
        """Synchronously append *row* to the first worksheet.

        This method is **blocking** and must always be called via
        :meth:`write_lead` (which runs it in a thread executor).

        Args:
            row: Ordered list of cell values matching :data:`COLUMNS`.
        """
        gc = gspread.authorize(self._credentials)
        worksheet = gc.open_by_key(self._sheet_id).sheet1
        worksheet.append_row(row, value_input_option="USER_ENTERED")

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
            phone=lead.phone_number,
            score=lead.score,
            priority=lead.status,
        )
