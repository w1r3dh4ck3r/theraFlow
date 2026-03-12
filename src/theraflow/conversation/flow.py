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
    FIRST_THERAPY = "FIRST_THERAPY"
    TOPIC = "TOPIC"
    URGENCY = "URGENCY"
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
    natural: bool = False
    data_key: str | None = None

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------

    @property
    def use_buttons(self) -> bool:
        """True when this step should render as WhatsApp reply buttons."""
        if self.natural:
            return False
        return bool(self.options) and len(self.options) <= _MAX_BUTTONS

    @property
    def use_list(self) -> bool:
        """True when this step should render as a WhatsApp list message."""
        if self.natural:
            return False
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
        # Natural steps: try button/option matching first, fall back to raw text
        if self.natural:
            # Try button payload (WhatsApp interactive reply)
            if button_id:
                resolved = self._resolve_button_answer(text, button_id, button_title)
                if resolved:
                    return resolved
            # Try exact text match against options
            if text and self.options:
                lower = text.strip().lower()
                for opt in self.options:
                    if opt.lower() == lower:
                        return opt
                # Try 1-based numeric index
                if text.strip().isdigit():
                    idx = int(text.strip()) - 1
                    if 0 <= idx < len(self.options):
                        return self.options[idx]
            # Return raw text for LLM classification
            stripped = (text or "").strip()
            return stripped if stripped else None

        if self.use_buttons:
            return self._resolve_button_answer(text, button_id, button_title)
        if self.use_list:
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

HUMAN_HANDOFF_MESSAGE: str = (
    "Vou te conectar com um de nossos atendentes.\n\n"
    "Em breve alguém entrará em contato com você. Até logo!"
)

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
            "Olá! Seja muito bem-vinda à Terapia Humanizada com Karoline Jangola!\n\n"
            "Qual é o seu nome? E o atendimento seria para você mesma "
            "ou para algum conhecido ou familiar?"
        ),
        accepts_free_text=True,
        data_key=None,
    ),
    Step.WHO_FOR: StepConfig(
        prompt="Para quem seria o atendimento?",
        options=["Para mim", "Para meu filho(a)", "Outro familiar", "Outra pessoa"],
        natural=True,
        data_key="who_for",
    ),
    Step.GENDER: StepConfig(
        prompt="Como você se identifica?",
        options=["Mulher", "Homem", "Prefiro não responder"],
        data_key="gender",
    ),
    Step.FIRST_THERAPY: StepConfig(
        prompt="Esta seria sua primeira experiência com terapia?",
        options=["Sim", "Não"],
        natural=True,
        data_key="first_therapy",
    ),
    Step.TOPIC: StepConfig(
        prompt="Qual tema você gostaria de trabalhar na terapia?",
        options=[
            "Ansiedade",
            "Relacionamentos",
            "Autoestima",
            "Luto",
            "Família",
            "Outro",
        ],
        natural=True,
        data_key="topic",
    ),
    Step.URGENCY: StepConfig(
        prompt="Para quando gostaria de iniciar?",
        options=[
            "O quanto antes",
            "Nesta semana",
            "Neste mês",
            "Ainda estou pensando",
        ],
        natural=True,
        data_key="urgency",
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
    Step.FIRST_THERAPY,
    Step.TOPIC,
    Step.URGENCY,
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
