"""pi tool parsing rule tests."""

from arbiteros_kernel.instruction_parsing.tool_parsers import PI_TOOL_PARSER_REGISTRY


def _parse(tool: str, args: dict):
    return PI_TOOL_PARSER_REGISTRY[tool](args)


def test_bash_uses_shell_analysis() -> None:
    result = _parse("bash", {"command": "rm -rf /tmp/old"})
    assert result.instruction_type == "EXEC"
    assert result.security_type["risk"] == "HIGH"
    assert result.security_type["reversible"] is False


def test_read_memory_path_is_retrieve() -> None:
    result = _parse("read", {"path": "/Users/test/.pi/agent/memory/daily/2026-09-08.md"})
    assert result.instruction_type == "RETRIEVE"
    assert result.security_type["confidentiality"] == "HIGH"


def test_edit_memory_path_is_store() -> None:
    result = _parse("edit", {"path": "/Users/test/.pi/agent/memory/MEMORY.md"})
    assert result.instruction_type == "STORE"
    assert result.security_type["trustworthiness"] == "HIGH"


def test_memory_tools_use_memory_instruction_types() -> None:
    assert _parse("memory_read", {}).instruction_type == "RETRIEVE"
    assert _parse("memory_search", {"query": "historical decision"}).instruction_type == "RETRIEVE"
    assert _parse("memory_status", {}).instruction_type == "READ"
    assert _parse("memory_write", {"content": "decision"}).instruction_type == "STORE"
    assert _parse("memory_forget", {"match": "stale"}).instruction_type == "PRUNE"
    assert _parse("memory_restore", {"recoveryId": "id"}).instruction_type == "STORE"


def test_scratchpad_list_is_retrieve_and_mutation_is_store() -> None:
    assert _parse("scratchpad", {"action": "list"}).instruction_type == "RETRIEVE"
    assert _parse("scratchpad", {"action": "add", "text": "todo"}).instruction_type == "STORE"


def test_all_pi_tools_registered() -> None:
    assert set(PI_TOOL_PARSER_REGISTRY) == {
        "bash",
        "read",
        "write",
        "edit",
        "memory_read",
        "memory_search",
        "memory_status",
        "memory_write",
        "memory_forget",
        "memory_restore",
        "scratchpad",
    }
