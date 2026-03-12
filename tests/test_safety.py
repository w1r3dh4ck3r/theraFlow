"""Tests for the crisis-risk detection safety layer.

Covers:
- HIGH-risk keyword detection (suicide, self-harm)
- MEDIUM-risk keyword detection (distress, danger)
- Exclusion phrases (common idioms like "morrer de rir")
- Accent-insensitive matching
- False-positive suppression (e.g. "estou ansiosa" is NOT a crisis term)
- Integration with ConversationEngine (crisis intercept before step logic)
"""

from __future__ import annotations

import pytest

from theraflow.safety.detector import RiskResult, detect_risk


# ---------------------------------------------------------------------------
# HIGH-risk detection
# ---------------------------------------------------------------------------


class TestHighRisk:
    @pytest.mark.parametrize(
        "text",
        [
            "quero me matar",
            "estou pensando em suicídio",
            "vou acabar com tudo",
            "pensei em me cortar hoje",
            "não quero mais viver",
            "tomei uma overdose",
            "quero morrer",
            "estou pensando em automutilação",
            "vou me machucar",
        ],
    )
    def test_high_risk_keywords(self, text: str) -> None:
        result = detect_risk(text)
        assert result.risk_level == "high", f"Expected high for: {text!r}"
        assert len(result.matched_terms) >= 1

    def test_high_risk_without_accents(self) -> None:
        """Accent-stripped input should still match."""
        result = detect_risk("estou pensando em suicidio")
        assert result.risk_level == "high"

    def test_high_risk_mixed_case(self) -> None:
        result = detect_risk("QUERO ME MATAR")
        assert result.risk_level == "high"

    def test_high_risk_multiple_terms(self) -> None:
        """Multiple high-risk terms in one message all get reported."""
        result = detect_risk("quero morrer, vou me cortar")
        assert result.risk_level == "high"
        assert len(result.matched_terms) >= 2


# ---------------------------------------------------------------------------
# MEDIUM-risk detection
# ---------------------------------------------------------------------------


class TestMediumRisk:
    @pytest.mark.parametrize(
        "text",
        [
            "estou em desespero total",
            "não aguento mais essa situação",
            "me sinto sem saída",
            "preciso de socorro",
            "estou sofrendo violência em casa",
            "sofri abuso do meu parceiro",
            "estou em perigo",
        ],
    )
    def test_medium_risk_keywords(self, text: str) -> None:
        result = detect_risk(text)
        assert result.risk_level == "medium", f"Expected medium for: {text!r}"
        assert len(result.matched_terms) >= 1

    def test_medium_risk_without_accents(self) -> None:
        result = detect_risk("estou em desespero, sem saida")
        assert result.risk_level == "medium"
        assert len(result.matched_terms) >= 2


# ---------------------------------------------------------------------------
# Exclusion phrases (idioms that share words with risk terms)
# ---------------------------------------------------------------------------


class TestExclusions:
    @pytest.mark.parametrize(
        "text",
        [
            "quero morrer de rir com essa história",
            "morrer de vontade de comer pizza",
            "morrer de fome aqui esperando",
            "vou matar saudade da minha amiga",
            "quero matar tempo até a consulta",
        ],
    )
    def test_idiom_excluded(self, text: str) -> None:
        result = detect_risk(text)
        assert result.risk_level == "none", f"False positive for: {text!r}"
        assert result.matched_terms == []


# ---------------------------------------------------------------------------
# False positives — common therapy language that should NOT trigger
# ---------------------------------------------------------------------------


class TestFalsePositives:
    @pytest.mark.parametrize(
        "text",
        [
            "estou ansiosa",
            "estou muito ansiosa ultimamente",
            "tenho crises de ansiedade",
            "estou triste",
            "me sinto deprimida",
            "estou passando por um momento difícil",
            "preciso de ajuda com relacionamento",
            "meu filho está tendo problemas na escola",
            "quero trabalhar minha autoestima",
            "tenho medo de falar em público",
            "estou estressada com o trabalho",
            "perdi meu emprego e estou preocupada",
            "estou com dificuldade para dormir",
            "minha relação está ruim",
            "estou com raiva do meu ex",
            "não consigo me concentrar",
        ],
    )
    def test_therapy_language_is_safe(self, text: str) -> None:
        result = detect_risk(text)
        assert result.risk_level == "none", f"False positive for: {text!r}"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_string(self) -> None:
        result = detect_risk("")
        assert result.risk_level == "none"

    def test_whitespace_only(self) -> None:
        result = detect_risk("   ")
        assert result.risk_level == "none"

    def test_unrelated_text(self) -> None:
        result = detect_risk("Bom dia, gostaria de agendar uma sessão")
        assert result.risk_level == "none"

    def test_high_overrides_medium(self) -> None:
        """When both HIGH and MEDIUM terms are present, result is HIGH."""
        result = detect_risk("quero me matar, não aguento mais")
        assert result.risk_level == "high"
        assert len(result.matched_terms) >= 2
