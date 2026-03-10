"""Unit tests for arbiteros_kernel.instruction_parsing.types."""

import pytest

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
        r = ToolParseResult("READ")
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
