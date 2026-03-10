"""Conversation step definitions for the TheraFlow lead-qualification flow.

Each :class:`StepConfig` describes one question in the 14-step intake flow,
including the exact Portuguese prompt text from the design document, the valid
response options, and the key used to store the user's answer.

Steps with more than :data:`_MAX_BUTTONS` options are automatically rendered
as numbered text lists, because the WhatsApp Cloud API limits interactive
reply-button messages to three buttons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

# ---------------------------------------------------------------------------
# Step enum
# ---------------------------------------------------------------------------


class Step(StrEnum):
    """Every step in the 14-step qualification conversation.

    Values are used as serialisable state identifiers in :class:`UserSession`.
    ``HUMAN_HANDOFF`` is a terminal state triggered when the user declines the
    bot at Step 1 (GREETING).
    """

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

            * When ``len(options) <= _MAX_BUTTONS``, the step is rendered as
              WhatsApp interactive reply buttons.
            * When ``len(options) > _MAX_BUTTONS``, the step is rendered as a
              plain-text numbered list (because the API caps buttons at 3).
            * When ``options`` is empty, the step is free-text only.

        button_titles: Optional display-title overrides for the button labels,
            one entry per option.  Needed when any option string exceeds the
            20-character limit imposed by the WhatsApp Cloud API.  Must have
            the same length as ``options`` when provided.
        accepts_free_text: Whether arbitrary text input is a valid answer.
            Used for free-text steps (CITY, OPTIONAL_NOTE).
        data_key: Key under which the resolved answer is stored in
            ``UserSession.collected_data``.  ``None`` for steps that do not
            produce a stored value (GREETING, CONSENT, CLOSING).
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
    def use_numbered_list(self) -> bool:
        """True when this step should render as a plain-text numbered list."""
        return bool(self.options) and len(self.options) > _MAX_BUTTONS

    def full_prompt(self) -> str:
        """Return the prompt, appending a numbered option list when needed.

        For button steps or free-text steps the raw ``prompt`` is returned
        unchanged.  For numbered-list steps the options are appended as::

            1. Option one
            2. Option two
            …

        Returns:
            Complete message body string ready to send to the user.
        """
        if not self.use_numbered_list:
            return self.prompt
        lines: list[str] = [self.prompt, ""]
        for i, opt in enumerate(self.options, start=1):
            lines.append(f"{i}. {opt}")
        lines.append("")
        lines.append("Responda com o número da opção.")
        return "\n".join(lines)

    def to_buttons(self) -> list[dict[str, str]]:
        """Return button descriptors for :func:`~theraflow.whatsapp.sender.send_button_message`.

        Button IDs follow the pattern ``opt_0``, ``opt_1``, ``opt_2`` to allow
        round-tripping through :meth:`resolve_answer`.  Display titles come
        from ``button_titles`` (if provided) or from ``options``, truncated to
        the 20-character WhatsApp maximum.

        Returns:
            List of ``{"id": …, "title": …}`` dicts, one per option.
        """
        titles = self.button_titles if self.button_titles else self.options
        return [
            {"id": f"opt_{i}", "title": title[:20]}
            for i, title in enumerate(titles)
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
        """Normalise raw inbound input to a canonical option value.

        The canonical value is always the full option string from ``options``
        (not the possibly-truncated button display title).

        * **Button steps** — matched by button ID first, then by case-insensitive
          comparison against ``options`` and ``button_titles``.
        * **Numbered-list steps** — matched by 1-based numeric position or by
          case-insensitive full option text.
        * **Free-text steps** — any non-empty text is accepted verbatim.

        Args:
            text: Raw message body (``None`` for pure button-reply events).
            button_id: Payload ID from a ``button_reply`` event, e.g.
                ``"opt_0"``.
            button_title: Display title from a ``button_reply`` event.

        Returns:
            The resolved canonical value, or ``None`` if the input does not
            match any valid option.
        """
        if self.use_buttons:
            return self._resolve_button_answer(text, button_id, button_title)
        if self.use_numbered_list:
            return self._resolve_list_answer(text)
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
        # Primary: match the button payload ID → look up the option by index.
        if button_id:
            for i, opt in enumerate(self.options):
                if button_id == f"opt_{i}":
                    return opt

        # Fallback: case-insensitive text match (handles typed responses too).
        candidate = (button_title or text or "").strip().lower()
        if not candidate:
            return None

        for opt in self.options:
            if opt.strip().lower() == candidate:
                return opt

        # Also try matching against display-title overrides.
        for i, title in enumerate(self.button_titles):
            if title.strip().lower() == candidate and i < len(self.options):
                return self.options[i]

        return None

    def _resolve_list_answer(self, text: str | None) -> str | None:
        if not text:
            return None
        stripped = text.strip()

        # Numeric: "1", "2", …
        if stripped.isdigit():
            idx = int(stripped) - 1
            if 0 <= idx < len(self.options):
                return self.options[idx]

        # Full text match (case-insensitive).
        lower = stripped.lower()
        for opt in self.options:
            if opt.strip().lower() == lower:
                return opt

        return None


# ---------------------------------------------------------------------------
# Special-case constants
# ---------------------------------------------------------------------------

#: Exact option value at GREETING that triggers the human-handoff branch.
HUMAN_HANDOFF_OPTION: str = "Prefiro falar com uma pessoa"

#: Exact option value at CONSENT that triggers LGPD data-discard.
LGPD_DECLINE_OPTION: str = "Não"

#: Message sent when the user requests a human agent at Step 1.
HUMAN_HANDOFF_MESSAGE: str = (
    "Sem problema! Em breve um atendente entrará em contato com você "
    "diretamente. Até logo!"
)

#: Message sent when the user declines data consent (LGPD compliance).
LGPD_DECLINED_MESSAGE: str = (
    "Tudo bem. Suas informações não serão armazenadas. "
    "Se mudar de ideia, pode entrar em contato novamente. Até logo!"
)

#: Message sent when input does not match any valid option for the current step.
INVALID_INPUT_MESSAGE: str = (
    "Desculpe, não entendi. Por favor, escolha uma das opções disponíveis."
)

#: Keywords (lower-cased) that trigger a skip on the OPTIONAL_NOTE step.
SKIP_KEYWORDS: frozenset[str] = frozenset({"pular", "skip", "próximo", "proximo"})


# ---------------------------------------------------------------------------
# Canonical step configurations  (prompt copy from theraFlow_design_doc.md)
# ---------------------------------------------------------------------------

STEP_CONFIGS: dict[Step, StepConfig] = {
    # ------------------------------------------------------------------
    # Step 1 — Greeting
    # ------------------------------------------------------------------
    Step.GREETING: StepConfig(
        prompt=(
            "Olá, eu sou a assistente virtual da Karoline.\n\n"
            "Posso te fazer algumas perguntas rápidas para entender como podemos ajudar?"
        ),
        options=["Sim", HUMAN_HANDOFF_OPTION],
        # "Prefiro falar com uma pessoa" (28 chars) exceeds the 20-char limit.
        button_titles=["Sim", "Falar com alguém"],
        data_key=None,
    ),
    # ------------------------------------------------------------------
    # Step 2 — Who is therapy for?
    # ------------------------------------------------------------------
    Step.WHO_FOR: StepConfig(
        prompt="Para quem seria o atendimento?",
        options=["Para mim", "Para meu filho(a)", "Para meu parceiro(a)", "Outro familiar"],
        data_key="who_for",
    ),
    # ------------------------------------------------------------------
    # Step 3 — Gender
    # ------------------------------------------------------------------
    Step.GENDER: StepConfig(
        prompt="Qual opção melhor representa a pessoa interessada no atendimento?",
        options=["Mulher", "Homem", "Prefere não informar"],
        # "Prefere não informar" is exactly 20 chars — no override needed.
        data_key="gender",
    ),
    # ------------------------------------------------------------------
    # Step 4 — Age group
    # ------------------------------------------------------------------
    Step.AGE_GROUP: StepConfig(
        prompt="Qual a faixa etária?",
        options=[
            "Até 12", "13\u201317", "18\u201324", "25\u201334",
            "35\u201344", "45\u201359", "60+",
        ],
        data_key="age_group",
    ),
    # ------------------------------------------------------------------
    # Step 5 — City (free text)
    # ------------------------------------------------------------------
    Step.CITY: StepConfig(
        prompt="Em qual cidade você está?",
        accepts_free_text=True,
        data_key="city",
    ),
    # ------------------------------------------------------------------
    # Step 6 — Preferred format
    # ------------------------------------------------------------------
    Step.FORMAT: StepConfig(
        prompt="Você prefere atendimento:",
        options=["Online", "Presencial", "Tanto faz"],
        data_key="format",
    ),
    # ------------------------------------------------------------------
    # Step 7 — First therapy?
    # ------------------------------------------------------------------
    Step.FIRST_THERAPY: StepConfig(
        prompt="Seria sua primeira experiência em terapia?",
        options=["Sim", "Não"],
        data_key="first_therapy",
    ),
    # ------------------------------------------------------------------
    # Step 8 — Main topic
    # ------------------------------------------------------------------
    Step.TOPIC: StepConfig(
        prompt="Qual tema mais se aproxima do que você gostaria de trabalhar?",
        options=["Ansiedade", "Relacionamentos", "Autoestima", "Luto", "Família", "Outro"],
        data_key="topic",
    ),
    # ------------------------------------------------------------------
    # Step 9 — Urgency
    # ------------------------------------------------------------------
    Step.URGENCY: StepConfig(
        prompt="Você gostaria de começar:",
        options=["O quanto antes", "Nesta semana", "Neste mês", "Ainda estou pensando"],
        data_key="urgency",
    ),
    # ------------------------------------------------------------------
    # Step 10 — Preferred time of day
    # ------------------------------------------------------------------
    Step.PREFERRED_TIME: StepConfig(
        prompt="Qual período é melhor para você?",
        options=["Manhã", "Tarde", "Noite", "Flexível"],
        data_key="preferred_time",
    ),
    # ------------------------------------------------------------------
    # Step 11 — Appointment intent
    # ------------------------------------------------------------------
    Step.APPOINTMENT_INTENT: StepConfig(
        prompt="Você gostaria de agendar uma primeira conversa?",
        options=["Sim", "Quero tirar dúvidas primeiro"],
        # "Quero tirar dúvidas primeiro" (28 chars) exceeds the 20-char limit.
        button_titles=["Sim", "Tirar dúvidas"],
        data_key="appointment_interest",
    ),
    # ------------------------------------------------------------------
    # Step 12 — Optional note (free text, skippable)
    # ------------------------------------------------------------------
    Step.OPTIONAL_NOTE: StepConfig(
        prompt=(
            "Se quiser, pode escrever em uma frase o que te motivou a procurar "
            "atendimento agora.\n\nOu pode pular."
        ),
        accepts_free_text=True,
        data_key="note",
    ),
    # ------------------------------------------------------------------
    # Step 13 — LGPD consent
    # ------------------------------------------------------------------
    Step.CONSENT: StepConfig(
        prompt=(
            "Vamos registrar essas informações apenas para facilitar o primeiro contato.\n\n"
            "Você concorda em continuar?"
        ),
        options=["Sim", LGPD_DECLINE_OPTION],
        data_key=None,
    ),
    # ------------------------------------------------------------------
    # Step 14 — Closing (terminal; no user input expected)
    # ------------------------------------------------------------------
    Step.CLOSING: StepConfig(
        prompt=(
            "Perfeito, já organizei suas informações.\n\n"
            "A Karoline costuma responder novos contatos ainda hoje.\n\n"
            "Se preferir, você também pode ver horários disponíveis:\n\n"
            "[link de agendamento]"
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
    """Return the next step after *current* in the default linear flow.

    Args:
        current: The step that has just been answered.

    Returns:
        The following :class:`Step`, or ``None`` if *current* is the final
        step or is not in the standard :data:`STEP_ORDER` list.
    """
    try:
        idx = STEP_ORDER.index(current)
    except ValueError:
        return None
    next_idx = idx + 1
    return STEP_ORDER[next_idx] if next_idx < len(STEP_ORDER) else None
