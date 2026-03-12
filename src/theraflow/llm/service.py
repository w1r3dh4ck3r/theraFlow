"""LLM service for generating natural conversation responses.

Uses the OpenRouter API (OpenAI-compatible) to generate empathetic,
conversational Portuguese responses while keeping the state machine in control.
The LLM only generates message text — step progression, validation, and UI
structure (buttons/lists) remain hardcoded.
"""

from __future__ import annotations

from typing import Any

import httpx

from theraflow.logging import get_logger

log = get_logger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """\
Você é a assistente virtual da psicóloga Karoline Jangola — acolhedora, \
profissional e empática. Você está conduzindo uma triagem inicial por WhatsApp.

Regras ESTRITAS:
- Responda APENAS em português brasileiro, tom acolhedor e profissional.
- Gere SOMENTE o texto da mensagem — sem emojis, sem markdown, sem formatação.
- NÃO liste as opções de resposta — elas já aparecem como botões na interface.
- NÃO invente informações, diagnósticos ou conselhos clínicos.
- NÃO faça perguntas adicionais além da que está sendo solicitada.
- Mantenha a resposta CURTA (1-3 frases no máximo).
- Use o nome da pessoa quando disponível para personalizar.
- Quando a pessoa compartilhar algo sensível, valide brevemente antes de \
continuar ("Entendo...", "Obrigada por compartilhar...").
- Adapte o tom: mais leve no início, mais acolhedor em temas sensíveis."""

STEP_INSTRUCTIONS: dict[str, str] = {
    "GREETING": "Dê boas-vindas e pergunte se pode continuar com algumas perguntas rápidas.",
    "WHO_FOR": "Pergunte para quem seria o atendimento.",
    "GENDER": "Pergunte como a pessoa que receberá o atendimento se identifica.",
    "AGE_GROUP": "Pergunte a faixa etária da pessoa que receberá o atendimento.",
    "CITY": "Pergunte em qual cidade a pessoa está localizada.",
    "FORMAT": "Pergunte qual formato de atendimento a pessoa prefere.",
    "FIRST_THERAPY": "Pergunte se esta seria a primeira experiência com psicoterapia.",
    "TOPIC": "Pergunte qual tema a pessoa gostaria de trabalhar na terapia.",
    "URGENCY": "Pergunte quando a pessoa gostaria de começar a terapia.",
    "PREFERRED_TIME": "Pergunte qual período do dia é melhor para as sessões.",
    "APPOINTMENT_INTENT": "Pergunte se a pessoa gostaria de agendar uma sessão experimental.",
    "OPTIONAL_NOTE": (
        "Pergunte se há algo mais que a pessoa queira compartilhar. "
        "Mencione que pode digitar 'pular' para continuar."
    ),
    "CONSENT": (
        "Explique que para prosseguir é necessário consentimento LGPD para "
        "armazenar os dados para fins de agendamento."
    ),
    "CLOSING": "Agradeça, informe que entrarão em contato em breve para confirmar o agendamento.",
}


def _build_context(
    step: str,
    user_name: str,
    collected_data: dict[str, Any],
    last_answer: str | None,
) -> str:
    """Build user-message context for the LLM."""
    parts = [f"Etapa atual: {step}"]
    parts.append(f"Instrução: {STEP_INSTRUCTIONS.get(step, 'Continue a conversa.')}")

    if user_name:
        parts.append(f"Nome do usuário: {user_name}")

    if last_answer:
        parts.append(f"Última resposta do usuário: {last_answer}")

    if collected_data:
        summary = ", ".join(f"{k}={v}" for k, v in collected_data.items())
        parts.append(f"Dados coletados até agora: {summary}")

    parts.append(
        "Gere SOMENTE o texto da pergunta/mensagem. "
        "NÃO inclua as opções — elas serão exibidas como botões."
    )
    return "\n".join(parts)


async def generate_response(
    api_key: str,
    model: str,
    step: str,
    user_name: str,
    collected_data: dict[str, Any],
    last_answer: str | None = None,
    timeout: float = 10.0,
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Call the LLM to generate a natural response for the given step.

    Returns the generated text, or None on any failure (timeout, API error,
    empty response). The caller should fall back to the hardcoded prompt.
    """
    context = _build_context(step, user_name, collected_data, last_answer)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ],
        "max_tokens": 256,
        "temperature": 0.7,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        client = http_client or httpx.AsyncClient()
        try:
            resp = await client.post(
                OPENROUTER_URL,
                json=payload,
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            if not text:
                log.warning("llm_empty_response", step=step)
                return None
            log.debug("llm_response_generated", step=step, length=len(text))
            return text
        finally:
            if http_client is None:
                await client.aclose()
    except httpx.TimeoutException:
        log.warning("llm_timeout", step=step, timeout=timeout)
        return None
    except Exception as exc:
        log.warning("llm_error", step=step, error=str(exc))
        return None
