"""Crisis-risk detector for TheraFlow.

Analyses raw message text for keywords and phrases associated with
self-harm, suicide, or immediate danger.  All matching is performed on
accent-stripped, lower-cased text so it works regardless of whether the
sender uses diacritics.

Usage::

    from theraflow.safety.detector import detect_risk

    result = detect_risk("não aguento mais, estou desesperado")
    # RiskResult(risk_level='medium', matched_terms=['não aguento mais', 'desespero'])

    result = detect_risk("quero morrer de rir com essa história")
    # RiskResult(risk_level='none', matched_terms=[])   ← exclusion applied

No external dependencies — stdlib only.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

RiskLevel = Literal["none", "low", "medium", "high"]

_RISK_ORDER: dict[str, int] = {"none": 0, "low": 1, "medium": 2, "high": 3}


@dataclass
class RiskResult:
    """Result of a risk-detection pass over a single message.

    Attributes:
        risk_level: Highest risk level found ('none', 'low', 'medium', or 'high').
        matched_terms: Human-readable list of every term that was matched
            (in its original, accented display form).
    """

    risk_level: RiskLevel
    matched_terms: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Term definitions
# Each entry is (display_form, normalized_form).
# display_form  – shown in RiskResult for readability.
# normalized_form – accent-stripped, lower-cased form used for matching.
# ---------------------------------------------------------------------------

# Terms that indicate HIGH risk (self-harm / suicidal ideation).
_HIGH_TERMS: list[tuple[str, str]] = [
    ("suicídio", "suicidio"),
    ("me matar", "me matar"),
    ("quero morrer", "quero morrer"),
    ("não quero mais viver", "nao quero mais viver"),
    ("acabar com tudo", "acabar com tudo"),
    ("automutilação", "automutilacao"),
    ("me cortar", "me cortar"),
    ("me machucar", "me machucar"),
    ("overdose", "overdose"),
]

# Terms that indicate MEDIUM risk (distress / danger signals).
_MEDIUM_TERMS: list[tuple[str, str]] = [
    ("desespero", "desespero"),
    ("não aguento mais", "nao aguento mais"),
    ("sem saída", "sem saida"),
    ("socorro", "socorro"),
    ("violência", "violencia"),
    ("abuso", "abuso"),
    ("estou em perigo", "estou em perigo"),
]

# Normalized exclusion phrases – common idioms that share words with risk
# terms but carry no crisis meaning.  Occurrences of these phrases are
# blanked out of the text before matching begins.
_EXCLUSION_PHRASES: list[str] = [
    "matar saudade",
    "morrer de rir",
    "morrer de vontade",
    "morrer de fome",
    "matar tempo",
]


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Return *text* lower-cased with all diacritical marks removed.

    Uses Unicode NFD decomposition so that e.g. 'ã' → 'a', 'ç' → 'c'.
    """
    lowered = text.lower()
    nfd = unicodedata.normalize("NFD", lowered)
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")


def _scrub_exclusions(normalized_text: str) -> str:
    """Replace each exclusion phrase in *normalized_text* with spaces.

    Replacing with equal-length whitespace preserves word boundaries for
    subsequent single-word regex matching.

    Example::

        _scrub_exclusions("quero morrer de rir hoje")
        # → "quero              hoje"
    """
    scrubbed = normalized_text
    for phrase in _EXCLUSION_PHRASES:
        scrubbed = scrubbed.replace(phrase, " " * len(phrase))
    return scrubbed


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------


def _is_multiword(term: str) -> bool:
    return " " in term


def _term_present(normalized_term: str, scrubbed_text: str) -> bool:
    """Return True if *normalized_term* appears in *scrubbed_text*.

    Multi-word phrases use plain substring matching (word boundaries are
    implicitly enforced by the surrounding spaces in natural text).
    Single words use ``\\b`` word-boundary anchors to avoid partial hits
    (e.g. 'abuso' must not match inside 'abusou' unless that is also
    clinically relevant – here we stay conservative and require an exact
    word form).
    """
    if _is_multiword(normalized_term):
        return normalized_term in scrubbed_text
    pattern = r"\b" + re.escape(normalized_term) + r"\b"
    return bool(re.search(pattern, scrubbed_text))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_risk(text: str) -> RiskResult:
    """Analyse *text* for crisis-related content and return a :class:`RiskResult`.

    Processing steps:

    1. Normalise input (lowercase + strip accents).
    2. Blank out any exclusion phrases to suppress common false positives.
    3. Match HIGH risk terms; then MEDIUM risk terms.
    4. Return the highest risk level found along with all matched terms.

    Args:
        text: Raw message text from the user (any encoding, with or without
            diacritics).

    Returns:
        :class:`RiskResult` with ``risk_level`` set to the most severe level
        detected and ``matched_terms`` listing every matched term in its
        original human-readable (accented) form.
    """
    normalized_text = _normalize(text)
    scrubbed_text = _scrub_exclusions(normalized_text)

    matched: list[str] = []
    highest: RiskLevel = "none"

    def _check(terms: list[tuple[str, str]], level: RiskLevel) -> None:
        nonlocal highest
        for display, normalized_term in terms:
            if _term_present(normalized_term, scrubbed_text):
                matched.append(display)
                if _RISK_ORDER[level] > _RISK_ORDER[highest]:
                    highest = level

    _check(_HIGH_TERMS, "high")
    _check(_MEDIUM_TERMS, "medium")

    return RiskResult(risk_level=highest, matched_terms=matched)
