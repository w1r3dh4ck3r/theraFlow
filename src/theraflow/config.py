"""Application configuration via environment variables.

All settings are read from the process environment (or a ``.env`` file in the
working directory) using :class:`pydantic_settings.BaseSettings`.  Required
fields raise a :class:`~pydantic.ValidationError` at startup if missing, so
misconfiguration is caught immediately rather than at the first API call.

Typical ``.env`` file::

    WHATSAPP_PHONE_NUMBER_ID=123456789
    WHATSAPP_ACCESS_TOKEN=EAAB...
    GOOGLE_SERVICE_ACCOUNT_JSON=/run/secrets/sa.json
    GOOGLE_SHEETS_ID=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms
    TELEGRAM_BOT_TOKEN=7123456789:AAF...
    TELEGRAM_CHAT_ID=-1001234567890
    SCHEDULING_LINK=https://calendly.com/karoline/consulta
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for TheraFlow.

    All fields map directly to environment variables with the same name
    (upper-cased).  Pydantic-settings also loads a ``.env`` file from the
    current working directory when present.

    Attributes:
        whatsapp_phone_number_id: Meta-assigned numeric ID for the WhatsApp
            Business phone number that sends/receives messages.
        whatsapp_access_token: Permanent (or long-lived) access token used to
            authenticate requests to the WhatsApp Cloud API.
        google_service_account_json: Absolute path to the Google service-account
            JSON key file used by :class:`~theraflow.sheets.client.SheetsClient`.
        google_sheets_id: ID of the Google Spreadsheet that stores lead records.
        telegram_bot_token: Token for the Telegram Bot API, obtained from
            @BotFather.
        telegram_chat_id: Chat or group ID to which lead notifications are sent.
        scheduling_link: Public URL where prospects can self-schedule an
            appointment.  Embedded in the :data:`~theraflow.conversation.flow.Step.CLOSING`
            message.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------
    # WhatsApp Cloud API
    # ------------------------------------------------------------------
    whatsapp_phone_number_id: str = Field(
        ...,
        description="Meta-assigned numeric ID for the WhatsApp Business phone number.",
    )
    whatsapp_access_token: str = Field(
        ...,
        description="Permanent access token for the WhatsApp Cloud API.",
    )
    whatsapp_verify_token: str = Field(
        ...,
        description="Webhook verification token configured in the Meta App Dashboard.",
    )
    whatsapp_app_secret: str = Field(
        ...,
        description="App Secret used to validate the X-Hub-Signature-256 on incoming webhooks.",
    )

    # ------------------------------------------------------------------
    # Google Sheets
    # ------------------------------------------------------------------
    google_service_account_json: str = Field(
        ...,
        description="Absolute path to the Google service-account JSON key file.",
    )
    google_sheets_id: str = Field(
        ...,
        description="ID of the Google Spreadsheet used for lead storage.",
    )

    # ------------------------------------------------------------------
    # Telegram
    # ------------------------------------------------------------------
    telegram_bot_token: str = Field(
        ...,
        description="Telegram Bot API token from @BotFather.",
    )
    telegram_chat_id: str = Field(
        ...,
        description="Telegram chat or group ID for lead notifications.",
    )

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------
    scheduling_link: str = Field(
        default="https://exemplo.com/agendar",
        description=(
            "Public URL where prospects can self-schedule an appointment. "
            "Shown in the closing message of the qualification flow."
        ),
    )


#: Module-level singleton — import this everywhere settings are needed.
settings = Settings()
