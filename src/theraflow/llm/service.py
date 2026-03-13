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
- NÃO liste as opções de resposta — a conversa é livre, sem botões.
- NÃO invente informações, diagnósticos ou conselhos clínicos.
- NÃO faça perguntas adicionais além da que está sendo solicitada.
- NÃO peça permissão para fazer perguntas ("posso continuar?", "tudo bem?").
- Mantenha a resposta CURTA (1-2 frases no máximo).
- Quando a pessoa compartilhar algo sensível, valide brevemente antes de \
continuar ("Entendo...", "Obrigada por compartilhar...").
- Adapte o tom: mais leve no início, mais acolhedor em temas sensíveis.
- Vá direto ao ponto — sem rodeios ou introduções desnecessárias.
- NÃO cumprimente o usuário após a primeira mensagem. Nada de "Olá", "Oi", \
"Prazer", "Para começarmos" — vá direto à pergunta.
- NÃO use o nome do usuário em nenhuma mensagem."""

CLASSIFY_SYSTEM_PROMPT = """\
Você é um classificador de respostas em uma triagem de psicologia por WhatsApp.
Sua tarefa é mapear a resposta livre do usuário para UMA das categorias válidas.

Regras:
- Responda com APENAS o texto exato da categoria correspondente.
- Se a resposta não se encaixar claramente em nenhuma categoria, responda "UNCLEAR".
- NÃO adicione explicação, pontuação ou texto extra — apenas a categoria."""

EXTRACT_GREETING_PROMPT = """\
Você é um extrator de informações de uma triagem de psicologia por WhatsApp.
O bot perguntou o nome da pessoa e para quem seria o atendimento.

Analise a resposta do usuário e extraia:
1. "name": o nome próprio da pessoa (apenas o primeiro nome, capitalizado)
2. "who_for": classifique em UMA das categorias abaixo, ou null se não mencionado:
   - "Para mim" (para si mesmo/a)
   - "Para meu filho(a)" (para filho/a)
   - "Outro familiar" (outro parente)
   - "Outra pessoa" (amigo, colega, etc.)

Responda APENAS com JSON válido, sem explicação:
{"name": "...", "who_for": "..." ou null}"""

STEP_INSTRUCTIONS: dict[str, str] = {
    "GREETING": "NÃO gere texto para esta etapa — ela usa mensagem fixa.",
    "WHO_FOR": (
        "Pergunte para quem seria o atendimento: para a própria pessoa "
        "ou para alguém (filho, familiar, outra pessoa)?"
    ),
    "GENDER": "NÃO gere texto para esta etapa — ela usa botões fixos.",
    "FIRST_THERAPY": "Vá direto à pergunta: esta seria a primeira experiência com terapia?",
    "TOPIC": "Vá direto à pergunta: qual tema gostaria de trabalhar na terapia?",
    "URGENCY": "Vá direto à pergunta: quando gostaria de começar a terapia?",
    "TERMS": (
        "Informe o valor social de R$ 60,00 por sessão no turno da tarde "
        "e pergunte se está de acordo com essas condições."
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

    if user_name and step == "GREETING":
        parts.append(f"Nome do usuário: {user_name}")

    if last_answer:
        parts.append(f"Última resposta do usuário: {last_answer}")

    if collected_data:
        filtered = {k: v for k, v in collected_data.items() if k != "name"}
        if filtered:
            summary = ", ".join(f"{k}={v}" for k, v in filtered.items())
            parts.append(f"Dados coletados até agora: {summary}")

    parts.append(
        "Gere SOMENTE o texto da pergunta/mensagem. "
        "A conversa é natural e aberta — NÃO liste opções."
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


STEP_CLASSIFY_HINTS: dict[str, str] = {
    "WHO_FOR": "Para quem é o atendimento (para si, filho, familiar, outra pessoa).",
    "GENDER": "Como a pessoa se identifica (mulher, homem, prefiro não informar).",
    "FIRST_THERAPY": "Se é a primeira vez fazendo terapia (sim ou não).",
    "TOPIC": "O tema principal que a pessoa quer trabalhar na terapia.",
    "URGENCY": "Quando quer começar a terapia (urgente, esta semana, este mês, ainda pensando).",
    "TERMS": "Se concorda com o valor de R$ 60 por sessão no turno da tarde (sim ou não).",
}


async def classify_answer(
    api_key: str,
    model: str,
    step: str,
    options: list[str],
    user_text: str,
    timeout: float = 8.0,
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Classify free-text user input into one of the canonical options.

    Returns the matching option string, or None if the LLM returns "UNCLEAR"
    or on any failure. The caller should reprompt when None is returned.
    """
    hint = STEP_CLASSIFY_HINTS.get(step, "Classifique a resposta do usuário.")
    options_str = "\n".join(f"- {opt}" for opt in options)
    context = (
        f"Etapa: {step}\n"
        f"Contexto: {hint}\n\n"
        f"Categorias válidas:\n{options_str}\n\n"
        f"Resposta do usuário: \"{user_text}\"\n\n"
        "Responda com a categoria correspondente ou UNCLEAR."
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": CLASSIFY_SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ],
        "max_tokens": 50,
        "temperature": 0.0,
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
            result = data["choices"][0]["message"]["content"].strip()

            if not result or result.upper() == "UNCLEAR":
                log.debug("llm_classify_unclear", step=step, user_text=user_text)
                return None

            # Match against options (case-insensitive)
            for opt in options:
                if opt.lower() == result.lower():
                    log.debug("llm_classify_matched", step=step, result=opt)
                    return opt

            # Partial match — LLM returned something close
            for opt in options:
                if opt.lower() in result.lower() or result.lower() in opt.lower():
                    log.debug("llm_classify_partial_match", step=step, result=opt)
                    return opt

            log.warning("llm_classify_no_match", step=step, result=result, options=options)
            return None
        finally:
            if http_client is None:
                await client.aclose()
    except httpx.TimeoutException:
        log.warning("llm_classify_timeout", step=step, timeout=timeout)
        return None
    except Exception as exc:
        log.warning("llm_classify_error", step=step, error=str(exc))
        return None


async def extract_greeting_info(
    api_key: str,
    model: str,
    user_text: str,
    timeout: float = 8.0,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, str | None]:
    """Extract name and optionally who_for from the greeting response.

    Returns {"name": "...", "who_for": "..." or None}.
    On failure, returns {"name": None, "who_for": None}.
    """
    import json as _json

    context = f'Resposta do usuário: "{user_text}"'
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": EXTRACT_GREETING_PROMPT},
            {"role": "user", "content": context},
        ],
        "max_tokens": 80,
        "temperature": 0.0,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        client = http_client or httpx.AsyncClient()
        try:
            resp = await client.post(
                OPENROUTER_URL, json=payload, headers=headers, timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            result = data["choices"][0]["message"]["content"].strip()
            # Strip markdown code fences if present
            if result.startswith("```"):
                result = result.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            parsed = _json.loads(result)
            name = parsed.get("name")
            who_for = parsed.get("who_for")

            # Validate who_for against canonical options
            valid_who_for = ["Para mim", "Para meu filho(a)", "Outro familiar", "Outra pessoa"]
            if who_for and who_for not in valid_who_for:
                # Try case-insensitive match
                matched = None
                for opt in valid_who_for:
                    if opt.lower() == who_for.lower():
                        matched = opt
                        break
                who_for = matched

            log.debug("llm_greeting_extracted", name=name, who_for=who_for)
            return {"name": name, "who_for": who_for}
        finally:
            if http_client is None:
                await client.aclose()
    except Exception as exc:
        log.warning("llm_greeting_extract_error", error=str(exc))
        return {"name": None, "who_for": None}
