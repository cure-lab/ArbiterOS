"""Instruction parsing for ArbiterOS Kernel."""

import logging
import sys

from .builder import InstructionBuilder

__all__ = ["InstructionBuilder"]

# ---------------------------------------------------------------------------
# Configure Standard Logging for the entire instruction_parsing package.
# ---------------------------------------------------------------------------
_pkg_logger = logging.getLogger(__name__)

if not _pkg_logger.handlers:
    handler = logging.StreamHandler()

    fmt_str = "[%(asctime)s] %(levelname)-8s [%(name)s] [%(filename)s:%(lineno)d] - %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    formatter = logging.Formatter(fmt_str, datefmt=date_fmt)
    handler.setFormatter(formatter)

    _pkg_logger.addHandler(handler)
    _pkg_logger.setLevel(logging.DEBUG)

    _pkg_logger.propagate = False
