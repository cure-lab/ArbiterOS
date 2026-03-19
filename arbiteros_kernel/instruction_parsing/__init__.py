"""Instruction parsing for ArbiterOS Kernel."""

import logging

from rich.logging import RichHandler

from .builder import InstructionBuilder

__all__ = ["InstructionBuilder"]

# ---------------------------------------------------------------------------
# Configure RichHandler for the entire instruction_parsing package.
# Sub-module loggers (getLogger(__name__)) are all children of this logger
# and inherit its handler automatically.
# propagate=False prevents duplicate output if the root logger has handlers.
# ---------------------------------------------------------------------------
_pkg_logger = logging.getLogger(__name__)
if not _pkg_logger.handlers:
    _pkg_logger.addHandler(RichHandler(rich_tracebacks=True, show_path=True))
    _pkg_logger.propagate = False

_pkg_logger.setLevel(logging.INFO)
