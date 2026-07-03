"""
DSL Rule Engine for TagRouter.

Parses and evaluates DSL rules that match contextual tags to routing decisions.
Supports rule splitting and merging based on performance metrics.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RuleNode:
    """A node in the DSL rule tree."""

    node_id: str
    condition: str  # DSL condition expression
    models: list[str] = field(default_factory=list)
    children: list["RuleNode"] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Performance tracking
    hit_count: int = 0
    success_count: int = 0
    total_cost: float = 0.0

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0

    @property
    def success_rate(self) -> float:
        if self.hit_count == 0:
            return 0.0
        return self.success_count / self.hit_count

    @property
    def avg_cost(self) -> float:
        if self.hit_count == 0:
            return 0.0
        return self.total_cost / self.hit_count


class DSLConditionEvaluator:
    """Evaluates DSL condition expressions against a set of tags."""

    def evaluate(self, condition: str, tags: dict[str, Any]) -> bool:
        """
        Evaluate a DSL condition expression against tags.

        Supported syntax:
        - tag_name: value           (equality)
        - tag_name: "value"         (string equality)
        - tag_name > value          (numeric comparison)
        - tag_name < value          (numeric comparison)
        - tag_name >= value         (numeric comparison)
        - tag_name <= value         (numeric comparison)
        - tag_name in [v1, v2]      (membership)
        - tag_name contains "str"   (string contains)
        - condition AND condition    (logical and)
        - condition OR condition     (logical or)
        - NOT condition              (logical not)
        - (condition)               (grouping)
        - *                         (always true / wildcard)
        """
        condition = condition.strip()

        if condition == "*" or condition == "true":
            return True
        if condition == "false":
            return False

        try:
            return self._parse_or(condition, tags)
        except Exception as e:
            logger.warning(f"Failed to evaluate condition '{condition}': {e}")
            return False

    def _parse_or(self, expr: str, tags: dict) -> bool:
        parts = self._split_by_keyword(expr, " OR ")
        if len(parts) > 1:
            return any(self._parse_and(p.strip(), tags) for p in parts)
        return self._parse_and(expr, tags)

    def _parse_and(self, expr: str, tags: dict) -> bool:
        parts = self._split_by_keyword(expr, " AND ")
        if len(parts) > 1:
            return all(self._parse_not(p.strip(), tags) for p in parts)
        return self._parse_not(expr, tags)

    def _parse_not(self, expr: str, tags: dict) -> bool:
        if expr.upper().startswith("NOT "):
            return not self._parse_primary(expr[4:].strip(), tags)
        return self._parse_primary(expr, tags)

    def _parse_primary(self, expr: str, tags: dict) -> bool:
        expr = expr.strip()

        # Parenthesized expression
        if expr.startswith("(") and expr.endswith(")"):
            return self._parse_or(expr[1:-1], tags)

        # "in" membership: tag_name in [v1, v2]
        in_match = re.match(r'^(\w+)\s+in\s+\[(.+)\]$', expr, re.IGNORECASE)
        if in_match:
            tag_name = in_match.group(1)
            values_str = in_match.group(2)
            values = [v.strip().strip('"\'') for v in values_str.split(",")]
            tag_val = tags.get(tag_name)
            return str(tag_val) in values

        # "contains" string check: tag_name contains "str"
        contains_match = re.match(r'^(\w+)\s+contains\s+"([^"]*)"$', expr, re.IGNORECASE)
        if contains_match:
            tag_name = contains_match.group(1)
            substring = contains_match.group(2)
            tag_val = str(tags.get(tag_name, ""))
            return substring.lower() in tag_val.lower()

        # Comparison operators
        cmp_match = re.match(r'^(\w+)\s*(>=|<=|>|<|!=|==|=)\s*(.+)$', expr)
        if cmp_match:
            tag_name = cmp_match.group(1)
            op = cmp_match.group(2)
            value_str = cmp_match.group(3).strip().strip('"\'')
            tag_val = tags.get(tag_name)
            return self._compare(tag_val, op, value_str)

        # Simple boolean tag presence
        if expr in tags:
            return bool(tags[expr])

        return False

    def _compare(self, tag_val: Any, op: str, value_str: str) -> bool:
        # Try numeric comparison first
        try:
            tag_num = float(tag_val) if tag_val is not None else None
            val_num = float(value_str)
            if tag_num is None:
                return False
            if op in ("=", "=="):
                return tag_num == val_num
            elif op == "!=":
                return tag_num != val_num
            elif op == ">":
                return tag_num > val_num
            elif op == "<":
                return tag_num < val_num
            elif op == ">=":
                return tag_num >= val_num
            elif op == "<=":
                return tag_num <= val_num
        except (TypeError, ValueError):
            pass

        # String comparison
        tag_str = str(tag_val) if tag_val is not None else ""
        if op in ("=", "=="):
            return tag_str == value_str
        elif op == "!=":
            return tag_str != value_str

        return False

    def _split_by_keyword(self, expr: str, keyword: str) -> list[str]:
        """Split expression by keyword, respecting parentheses."""
        parts = []
        depth = 0
        current = ""
        i = 0
        kw_upper = keyword.upper()

        while i < len(expr):
            if expr[i] == "(":
                depth += 1
                current += expr[i]
                i += 1
            elif expr[i] == ")":
                depth -= 1
                current += expr[i]
                i += 1
            elif depth == 0 and expr[i:i+len(keyword)].upper() == kw_upper:
                parts.append(current)
                current = ""
                i += len(keyword)
            else:
                current += expr[i]
                i += 1

        parts.append(current)
        return parts


class DSLRuleEngine:
    """
    Manages a tree of DSL rules and evaluates them against contextual tags.

    Rules are evaluated top-down; the first matching leaf node determines routing.
    """

    def __init__(self, rules: list[dict[str, Any]]):
        self.evaluator = DSLConditionEvaluator()
        self.roots: list[RuleNode] = self._build_tree(rules)

    def _build_tree(self, rules: list[dict[str, Any]]) -> list[RuleNode]:
        """Build a list of rule nodes from config dicts."""
        nodes = []
        for i, rule in enumerate(rules):
            node = self._build_node(rule, node_id=f"rule_{i}")
            nodes.append(node)
        return nodes

    def _build_node(self, rule: dict[str, Any], node_id: str) -> RuleNode:
        condition = rule.get("condition", "*")
        models = rule.get("models", [])
        metadata = rule.get("metadata", {})

        children = []
        for j, child_rule in enumerate(rule.get("children", [])):
            child = self._build_node(child_rule, node_id=f"{node_id}_c{j}")
            children.append(child)

        return RuleNode(
            node_id=node_id,
            condition=condition,
            models=models,
            children=children,
            metadata=metadata,
        )

    def match(self, tags: dict[str, Any]) -> tuple[RuleNode | None, list[str]]:
        """
        Find the best matching rule node for the given tags.

        Returns:
            (matched_node, model_candidates) or (None, []) if no match.
        """
        for root in self.roots:
            result = self._match_node(root, tags)
            if result is not None:
                node, models = result
                node.hit_count += 1
                return node, models

        return None, []

    def _match_node(
        self, node: RuleNode, tags: dict[str, Any]
    ) -> tuple[RuleNode, list[str]] | None:
        """Recursively match node and its children."""
        if not self.evaluator.evaluate(node.condition, tags):
            return None

        # Try children first (more specific rules)
        for child in node.children:
            result = self._match_child(child, tags)
            if result is not None:
                return result

        # This node matches
        if node.models:
            return node, node.models

        return None

    def _match_child(
        self, node: RuleNode, tags: dict[str, Any]
    ) -> tuple[RuleNode, list[str]] | None:
        if not self.evaluator.evaluate(node.condition, tags):
            return None

        for child in node.children:
            result = self._match_child(child, tags)
            if result is not None:
                return result

        if node.models:
            return node, node.models

        return None

    def record_outcome(
        self, node: RuleNode, success: bool, cost: float
    ) -> None:
        """Record the outcome of a routing decision for a node."""
        if success:
            node.success_count += 1
        node.total_cost += cost

    def to_dict(self) -> list[dict]:
        """Serialize the rule tree to a list of dicts."""
        return [self._node_to_dict(root) for root in self.roots]

    def _node_to_dict(self, node: RuleNode) -> dict:
        d: dict[str, Any] = {
            "node_id": node.node_id,
            "condition": node.condition,
            "models": node.models,
            "metadata": node.metadata,
            "stats": {
                "hit_count": node.hit_count,
                "success_rate": node.success_rate,
                "avg_cost": node.avg_cost,
            },
        }
        if node.children:
            d["children"] = [self._node_to_dict(c) for c in node.children]
        return d
