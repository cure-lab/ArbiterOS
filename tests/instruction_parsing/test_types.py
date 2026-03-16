"""Unit tests for arbiteros_kernel.instruction_parsing.types."""

import pytest
from unittest.mock import patch

from arbiteros_kernel.instruction_parsing.types import (
    INSTRUCTION_TYPE_TO_CATEGORY,
    ToolParseResult,
    make_security_type,
)

# ---------------------------------------------------------------------------
# make_security_type
# ---------------------------------------------------------------------------


class TestMakeSecurityType:
    def test_all_required_keys_present(self):
        sec = make_security_type(
            confidentiality="HIGH",
            trustworthiness="LOW",
            confidence="UNKNOWN",
            reversible=False,
            authority="HUMAN_APPROVED",
        )
        assert set(sec.keys()) == {
            "confidentiality",
            "trustworthiness",
            "confidence",
            "reversible",
            "authority",
            "custom",
        }

    def test_values_passed_through(self):
        sec = make_security_type(
            confidentiality="MID",
            trustworthiness="HIGH",
            confidence="LOW",
            reversible=True,
            authority="POLICY_BLOCKED",
        )
        assert sec["confidentiality"] == "MID"
        assert sec["trustworthiness"] == "HIGH"
        assert sec["confidence"] == "LOW"
        assert sec["reversible"] is True
        assert sec["authority"] == "POLICY_BLOCKED"

    def test_custom_defaults_to_empty_dict(self):
        sec = make_security_type(
            confidentiality="LOW",
            trustworthiness="LOW",
            confidence="UNKNOWN",
            reversible=False,
            authority="UNKNOWN",
        )
        assert sec["custom"] == {}

    def test_custom_field_passed(self):
        sec = make_security_type(
            confidentiality="HIGH",
            trustworthiness="MID",
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
            custom={"reason": "test"},
        )
        assert sec["custom"] == {"reason": "test"}


# ---------------------------------------------------------------------------
# ToolParseResult
# ---------------------------------------------------------------------------


class TestToolParseResult:
    def test_instruction_type_only(self):
        r = ToolParseResult("READ", None)
        assert r.instruction_type == "READ"
        assert r.security_type is None

    def test_both_fields(self):
        sec = make_security_type(
            confidentiality="HIGH",
            trustworthiness="LOW",
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        )
        r = ToolParseResult("EXEC", sec)
        assert r.instruction_type == "EXEC"
        assert r.security_type is sec

    def test_named_tuple_unpacking(self):
        r = ToolParseResult("WRITE", None)
        itype, sec = r
        assert itype == "WRITE"
        assert sec is None


# ---------------------------------------------------------------------------
# INSTRUCTION_TYPE_TO_CATEGORY
# ---------------------------------------------------------------------------


class TestInstructionTypeToCategory:
    @pytest.mark.parametrize(
        "itype, expected_category",
        [
            ("REASON", "COGNITIVE.Reasoning"),
            ("PLAN", "COGNITIVE.Reasoning"),
            ("CRITIQUE", "COGNITIVE.Reasoning"),
            ("STORE", "MEMORY.Management"),
            ("RETRIEVE", "MEMORY.Management"),
            ("COMPRESS", "MEMORY.Management"),
            ("PRUNE", "MEMORY.Management"),
            ("READ", "EXECUTION.Env"),
            ("WRITE", "EXECUTION.Env"),
            ("EXEC", "EXECUTION.Env"),
            ("WAIT", "EXECUTION.Env"),
            ("ASK", "EXECUTION.Human"),
            ("RESPOND", "EXECUTION.Human"),
            ("USER_MESSAGE", "EXECUTION.Human"),
            ("DELEGATE", "EXECUTION.Agent"),
            ("SUBSCRIBE", "EXECUTION.Perception"),
            ("RECEIVE", "EXECUTION.Perception"),
        ],
    )
    def test_known_types(self, itype, expected_category):
        assert INSTRUCTION_TYPE_TO_CATEGORY[itype] == expected_category

    def test_unknown_type_not_in_map(self):
        assert "UNKNOWN_TYPE" not in INSTRUCTION_TYPE_TO_CATEGORY


# ---------------------------------------------------------------------------
# compute_taint_status_from_instructions
# ---------------------------------------------------------------------------


class TestComputeTaintStatus:
    """compute_taint_status_from_instructions must always return a concrete level.

    UNKNOWN may appear in individual instructions (parsing stage), but the
    final aggregated result must be LOW, MID, or HIGH — never UNKNOWN.
    """

    def _make_instr(self, trust: str, conf: str) -> dict:
        """Build a minimal instruction dict with the given security levels."""
        from arbiteros_kernel.instruction_parsing.types import make_security_type

        return {
            "security_type": make_security_type(
                trustworthiness=trust,
                confidentiality=conf,
                confidence="UNKNOWN",
                reversible=True,
                authority="UNKNOWN",
            )
        }

    # ------------------------------------------------------------------
    # Empty / all-UNKNOWN → MID with a warning
    # ------------------------------------------------------------------

    def test_empty_list_returns_mid(self):
        from arbiteros_kernel.instruction_parsing.types import (
            compute_taint_status_from_instructions,
        )

        result = compute_taint_status_from_instructions([])
        assert result.trustworthiness == "MID"
        assert result.confidentiality == "MID"

    def test_empty_list_logs_warning(self):
        from arbiteros_kernel.instruction_parsing import types
        from arbiteros_kernel.instruction_parsing.types import (
            compute_taint_status_from_instructions,
        )

        with patch.object(types.logger, "warning") as mock_warn:
            compute_taint_status_from_instructions([])
        assert mock_warn.call_count == 2
        joined = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "trustworthiness" in joined
        assert "confidentiality" in joined

    def test_all_unknown_instructions_returns_mid(self):
        from arbiteros_kernel.instruction_parsing.types import (
            compute_taint_status_from_instructions,
        )

        instructions = [self._make_instr("UNKNOWN", "UNKNOWN")] * 3
        result = compute_taint_status_from_instructions(instructions)
        assert result.trustworthiness == "MID"
        assert result.confidentiality == "MID"

    def test_all_unknown_instructions_logs_warning(self):
        from arbiteros_kernel.instruction_parsing import types
        from arbiteros_kernel.instruction_parsing.types import (
            compute_taint_status_from_instructions,
        )

        instructions = [self._make_instr("UNKNOWN", "UNKNOWN")]
        with patch.object(types.logger, "warning") as mock_warn:
            compute_taint_status_from_instructions(instructions)
        assert mock_warn.call_count == 2
        joined = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "trustworthiness" in joined
        assert "confidentiality" in joined

    # ------------------------------------------------------------------
    # Concrete levels — no normalisation, no warning
    # ------------------------------------------------------------------

    def test_concrete_levels_returned_unchanged(self):
        from arbiteros_kernel.instruction_parsing import types
        from arbiteros_kernel.instruction_parsing.types import (
            compute_taint_status_from_instructions,
        )

        instructions = [self._make_instr("HIGH", "LOW")]
        with patch.object(types.logger, "warning") as mock_warn:
            result = compute_taint_status_from_instructions(instructions)
        assert result.trustworthiness == "HIGH"
        assert result.confidentiality == "LOW"
        mock_warn.assert_not_called()

    def test_trustworthiness_minimum_wins(self):
        from arbiteros_kernel.instruction_parsing.types import (
            compute_taint_status_from_instructions,
        )

        instructions = [
            self._make_instr("HIGH", "LOW"),
            self._make_instr("LOW", "LOW"),
            self._make_instr("MID", "LOW"),
        ]
        result = compute_taint_status_from_instructions(instructions)
        assert result.trustworthiness == "LOW"

    def test_confidentiality_maximum_wins(self):
        from arbiteros_kernel.instruction_parsing.types import (
            compute_taint_status_from_instructions,
        )

        instructions = [
            self._make_instr("HIGH", "LOW"),
            self._make_instr("HIGH", "HIGH"),
            self._make_instr("HIGH", "MID"),
        ]
        result = compute_taint_status_from_instructions(instructions)
        assert result.confidentiality == "HIGH"

    # ------------------------------------------------------------------
    # Mixed UNKNOWN + concrete — concrete supersedes UNKNOWN
    # ------------------------------------------------------------------

    def test_unknown_trust_superseded_by_low(self):
        """LOW < UNKNOWN in ordering, so LOW trust wins over UNKNOWN."""
        from arbiteros_kernel.instruction_parsing.types import (
            compute_taint_status_from_instructions,
        )

        instructions = [
            self._make_instr("UNKNOWN", "HIGH"),
            self._make_instr("LOW", "HIGH"),
        ]
        result = compute_taint_status_from_instructions(instructions)
        assert result.trustworthiness == "LOW"

    def test_unknown_trust_superseded_by_high_gives_mid(self):
        """If the only concrete trust is HIGH, UNKNOWN (score 0.5) wins → normalised to MID."""
        from arbiteros_kernel.instruction_parsing import types
        from arbiteros_kernel.instruction_parsing.types import (
            compute_taint_status_from_instructions,
        )

        instructions = [
            self._make_instr("UNKNOWN", "HIGH"),
            self._make_instr("HIGH", "HIGH"),
        ]
        with patch.object(types.logger, "warning") as mock_warn:
            result = compute_taint_status_from_instructions(instructions)
        assert result.trustworthiness == "MID"
        assert mock_warn.call_count >= 1
        joined = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "trustworthiness" in joined

    def test_unknown_conf_superseded_by_high(self):
        """HIGH > UNKNOWN in ordering, so HIGH conf wins over UNKNOWN."""
        from arbiteros_kernel.instruction_parsing.types import (
            compute_taint_status_from_instructions,
        )

        instructions = [
            self._make_instr("HIGH", "UNKNOWN"),
            self._make_instr("HIGH", "HIGH"),
        ]
        result = compute_taint_status_from_instructions(instructions)
        assert result.confidentiality == "HIGH"

    def test_unknown_conf_superseded_by_low_gives_mid(self):
        """If the only concrete conf is LOW, UNKNOWN (score 0.5) wins → normalised to MID."""
        from arbiteros_kernel.instruction_parsing import types
        from arbiteros_kernel.instruction_parsing.types import (
            compute_taint_status_from_instructions,
        )

        instructions = [
            self._make_instr("HIGH", "UNKNOWN"),
            self._make_instr("HIGH", "LOW"),
        ]
        with patch.object(types.logger, "warning") as mock_warn:
            result = compute_taint_status_from_instructions(instructions)
        assert result.confidentiality == "MID"
        assert mock_warn.call_count >= 1
        joined = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "confidentiality" in joined
