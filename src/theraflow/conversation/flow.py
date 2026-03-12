"""Conversation step definitions for the TheraFlow lead-qualification flow.

Each :class:`StepConfig` describes one question in the intake flow,
including the exact Portuguese prompt text, the valid response options,
and the key used to store the user's answer.

Steps with more than :data:`_MAX_BUTTONS` options are automatically rendered
as numbered text lists, because the WhatsApp Cloud API limits interactive
reply-button messages to three buttons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from theraflow.config import settings

# ---------------------------------------------------------------------------
# Step enum
# ---------------------------------------------------------------------------


class Step(StrEnum):
    """Every step in the qualification conversation."""

    GREETING = "GREETING"
    WHO_FOR = "WHO_FOR"
    GENDER = "GENDER"
    AGE_GROUP = "AGE_GROUP"
    CITY = "CITY"
    FORMAT = "FORMAT"
    FIRST_THERAPY = "FIRST_THERAPY"
    TOPIC = "TOPIC"
    URGENCY = "URGENCY"
    PREFERRED_TIME = "PREFERRED_TIME"
    APPOINTMENT_INTENT = "APPOINTMENT_INTENT"
    OPTIONAL_NOTE = "OPTIONAL_NOTE"
    CONSENT = "CONSENT"
    CLOSING = "CLOSING"
    # Terminal / special-case states (not part of the main STEP_ORDER list)
    HUMAN_HANDOFF = "HUMAN_HANDOFF"


# ---------------------------------------------------------------------------
# StepConfig
# ---------------------------------------------------------------------------

#: WhatsApp Cloud API maximum number of reply buttons per interactive message.
_MAX_BUTTONS: int = 3


@dataclass
class StepConfig:
    """Configuration for a single conversation step.

    Attributes:
        prompt: Exact Portuguese message text to send to the user.
        options: Ordered list of valid response values.
        button_titles: Optional display-title overrides for the button labels.
        accepts_free_text: Whether arbitrary text input is a valid answer.
        data_key: Key under which the resolved answer is stored in
            ``UserSession.collected_data``.
    """

    prompt: str
    options: list[str] = field(default_factory=list)
    button_titles: list[str] = field(default_factory=list)
    accepts_free_text: bool = False
    data_key: str | None = None

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------

    @property
    def use_buttons(self) -> bool:
        """True when this step should render as WhatsApp reply buttons."""
        return bool(self.options) and len(self.options) <= _MAX_BUTTONS

    @property
    def use_list(self) -> bool:
        """True when this step should render as a WhatsApp list message."""
        return bool(self.options) and len(self.options) > _MAX_BUTTONS

    def full_prompt(self) -> str:
        """Return the prompt text (used for list and free-text steps)."""
        return self.prompt

    def to_buttons(self) -> list[dict[str, str]]:
        """Return button descriptors for WhatsApp interactive messages."""
        titles = self.button_titles if self.button_titles else self.options
        return [
            {"id": f"opt_{i}", "title": title[:20]}
            for i, title in enumerate(titles)
        ]

    def to_list_rows(self) -> list[dict[str, str]]:
        """Return row descriptors for WhatsApp list messages."""
        return [
            {"id": f"opt_{i}", "title": opt[:24]}
            for i, opt in enumerate(self.options)
        ]

    # ------------------------------------------------------------------
    # Answer resolution
    # ------------------------------------------------------------------

    def resolve_answer(
        self,
        text: str | None,
        button_id: str | None,
        button_title: str | None,
    ) -> str | None:
        """Normalise raw inbound input to a canonical option value."""
        if self.use_buttons:
            return self._resolve_button_answer(text, button_id, button_title)
        if self.use_list:
            # Try 1-based numeric index first, then fall back to text matching.
            answer = self._resolve_list_answer(text)
            if answer is not None:
                return answer
            return self._resolve_button_answer(text, button_id, button_title)
        if self.accepts_free_text:
            stripped = (text or "").strip()
            return stripped if stripped else None
        return None

    # ------------------------------------------------------------------
    # Internal resolution helpers
    # ------------------------------------------------------------------

    def _resolve_button_answer(
        self,
        text: str | None,
        button_id: str | None,
        button_title: str | None,
    ) -> str | None:
        if button_id:
            for i, opt in enumerate(self.options):
                if button_id == f"opt_{i}":
                    return opt

        candidate = (button_title or text or "").strip().lower()
        if not candidate:
            return None

        for opt in self.options:
            if opt.strip().lower() == candidate:
                return opt

        for i, title in enumerate(self.button_titles):
            if title.strip().lower() == candidate and i < len(self.options):
                return self.options[i]

        return None

    def _resolve_list_answer(self, text: str | None) -> str | None:
        if not text:
            return None
        stripped = text.strip()

        if stripped.isdigit():
            idx = int(stripped) - 1
            if 0 <= idx < len(self.options):
                return self.options[idx]

        lower = stripped.lower()
        for opt in self.options:
            if opt.strip().lower() == lower:
                return opt

        return None


# ---------------------------------------------------------------------------
# Special-case constants
# ---------------------------------------------------------------------------

#: Option label used at GREETING to request a human agent.
HUMAN_HANDOFF_OPTION: str = "Falar com alguém"

#: Option label used at CONSENT to decline data processing.
CONSENT_DECLINE_OPTION: str = "Não"

#: Keyword at OPTIONAL_NOTE that skips the free-text step.
OPTIONAL_NOTE_SKIP_KEYWORD: str = "pular"

HUMAN_HANDOFF_MESSAGE: str = (
    "Vou te conectar com um de nossos atendentes.\n\n"
    "Em breve alguém entrará em contato com você. Até logo!"
)

LGPD_DECLINED_MESSAGE: str = (
    "Entendemos sua escolha. Seus dados não serão armazenados.\n\n"
    "Caso mude de ideia, pode nos contatar novamente. Até logo!"
)

# Kept for backward compatibility / other callers.
DECLINE_OPTION: str = "Não concordo"
ACCEPT_OPTION: str = "Concordo"

TERMS_DECLINED_MESSAGE: str = (
    "Agradecemos muito seu interesse.\n\n"
    "Infelizmente, nosso atendimento social funciona exclusivamente "
    "no turno da tarde, com o valor de R$ 60,00 por sessão.\n\n"
    "Caso suas condições mudem, ficaremos felizes em atendê-lo. "
    "Até breve!"
)

SCHEDULING_DECLINED_MESSAGE: str = (
    "Registramos seu interesse e entraremos em contato em breve.\n\n"
    "Agradecemos pelo seu tempo!"
)

SCHEDULING_DECLINE_OPTION: str = "Ainda estou pensando"

INVALID_INPUT_MESSAGE: str = (
    "Desculpe, não entendi sua resposta.\n\n"
    "Por favor, escolha uma das opções disponíveis."
)


# ---------------------------------------------------------------------------
# Canonical step configurations
# ---------------------------------------------------------------------------

STEP_CONFIGS: dict[Step, StepConfig] = {
    Step.GREETING: StepConfig(
        prompt=(
            "Olá! Sou o assistente virtual da Karoline Jangola.\n\n"
            "Vou fazer algumas perguntas rápidas para entender "
            "como podemos ajudar. Posso continuar?"
        ),
        options=["Sim", HUMAN_HANDOFF_OPTION],
        data_key=None,
    ),
    Step.WHO_FOR: StepConfig(
        prompt="Para quem seria o atendimento?",
        options=["Para mim", "Para meu filho(a)", "Outro familiar", "Outra pessoa"],
        data_key="who_for",
    ),
    Step.GENDER: StepConfig(
        prompt=(
            "Qual opção melhor representa a pessoa "
            "que receberá o atendimento?"
        ),
        options=["Mulher", "Homem", "Prefiro não informar"],
        data_key="gender",
    ),
    Step.AGE_GROUP: StepConfig(
        prompt="Qual é a faixa etária da pessoa que receberá o atendimento?",
        options=[
            "Menor de 12",
            "12\u201317",
            "18\u201324",
            "25\u201334",
            "35\u201344",
            "45\u201354",
            "55 ou mais",
        ],
        data_key="age_group",
    ),
    Step.CITY: StepConfig(
        prompt="Em qual cidade você está localizado(a)?",
        accepts_free_text=True,
        data_key="city",
    ),
    Step.FORMAT: StepConfig(
        prompt="Qual formato de atendimento você prefere?",
        options=["Online", "Presencial", "Indiferente"],
        data_key="format",
    ),
    Step.FIRST_THERAPY: StepConfig(
        prompt="Esta seria sua primeira experiência com psicoterapia?",
        options=["Sim", "Não"],
        data_key="first_therapy",
    ),
    Step.TOPIC: StepConfig(
        prompt=(
            "Qual tema mais se aproxima do que "
            "você gostaria de trabalhar?"
        ),
        options=[
            "Ansiedade",
            "Relacionamentos",
            "Autoestima",
            "Luto",
            "Família",
            "Outro",
        ],
        data_key="topic",
    ),
    Step.URGENCY: StepConfig(
        prompt="Para quando gostaria de iniciar sua terapia?",
        options=[
            "O quanto antes",
            "Nesta semana",
            "Neste mês",
            "Ainda estou pensando",
        ],
        data_key="urgency",
    ),
    Step.PREFERRED_TIME: StepConfig(
        prompt="Qual período do dia é melhor para você?",
        options=["Manhã", "Tarde", "Noite", "Indiferente"],
        data_key="preferred_time",
    ),
    Step.APPOINTMENT_INTENT: StepConfig(
        prompt="Gostaria de agendar uma sessão experimental?",
        options=["Sim", "Ainda estou pensando"],
        data_key="appointment_interest",
    ),
    Step.OPTIONAL_NOTE: StepConfig(
        prompt=(
            "Há algo mais que queira compartilhar antes de finalizarmos?\n\n"
            "(Digite sua mensagem ou envie *pular* para continuar.)"
        ),
        accepts_free_text=True,
        data_key="note",
    ),
    Step.CONSENT: StepConfig(
        prompt=(
            "Para prosseguir, precisamos do seu consentimento para "
            "armazenar seus dados conforme a LGPD.\n\n"
            "Você autoriza o uso dos seus dados para fins de "
            "agendamento de consultas?"
        ),
        options=["Sim", CONSENT_DECLINE_OPTION],
        data_key="consent",
    ),
    Step.CLOSING: StepConfig(
        prompt=(
            "Perfeito! Registramos suas informações.\n\n"
            "Entraremos em contato muito em breve para "
            "confirmar o agendamento.\n\n"
            "Agradecemos pela confiança!"
        ),
        data_key=None,
    ),
}


# ---------------------------------------------------------------------------
# Step ordering and navigation
# ---------------------------------------------------------------------------

#: Linear step progression from GREETING through CLOSING.
STEP_ORDER: list[Step] = [
    Step.GREETING,
    Step.WHO_FOR,
    Step.GENDER,
    Step.AGE_GROUP,
    Step.CITY,
    Step.FORMAT,
    Step.FIRST_THERAPY,
    Step.TOPIC,
    Step.URGENCY,
    Step.PREFERRED_TIME,
    Step.APPOINTMENT_INTENT,
    Step.OPTIONAL_NOTE,
    Step.CONSENT,
    Step.CLOSING,
]


def next_step(current: Step) -> Step | None:
    """Return the next step after *current* in the default linear flow."""
    try:
        idx = STEP_ORDER.index(current)
    except ValueError:
        return None
    next_idx = idx + 1
    return STEP_ORDER[next_idx] if next_idx < len(STEP_ORDER) else None
