import json

from django import forms
from django.db.models import Q

from .constants import EMPTY_VALUES
from .exceptions import ConditionTreeError, FilterNotCombinable

__all__ = [
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_NODES",
    "compile_condition_tree",
]

OPERATORS = ("and", "or", "not")

DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_NODES = 64


def compile_condition_tree(
    filterset, tree, *, max_depth=DEFAULT_MAX_DEPTH, max_nodes=DEFAULT_MAX_NODES
):
    """
    Validate ``tree`` against the filters declared on ``filterset`` and
    compile it into a ``Q`` object.

    ``tree`` may be a mapping or a JSON string. Leaves take the form
    ``{"filter": <name>, "value": <value>}`` where ``<name>`` must be a
    filter declared on the filterset; internal nodes are ``{"and": [...]}``,
    ``{"or": [...]}`` or ``{"not": <node>}``.

    Returns a ``(q, requires_distinct)`` tuple. ``requires_distinct`` is
    True when any leaf uses a filter declared with ``distinct=True``.

    Raises ``ConditionTreeError`` — locating the offending node via its
    ``path`` attribute — before any query is executed.
    """
    compiler = ConditionTreeCompiler(filterset, max_depth, max_nodes)
    return compiler.compile(tree)


class ConditionTreeCompiler:
    """
    Single-use compiler for condition trees. A fresh instance is created per
    ``FilterSet`` instance so that compilation state never leaks across
    requests.
    """

    def __init__(self, filterset, max_depth, max_nodes):
        self.filters = filterset.filters
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.node_count = 0
        self.requires_distinct = False

    def compile(self, tree):
        if isinstance(tree, str):
            try:
                tree = json.loads(tree)
            except json.JSONDecodeError as exc:
                raise ConditionTreeError("Invalid JSON: %s." % exc) from exc
        q = self.visit(tree, (), 1)
        return q, self.requires_distinct

    def visit(self, node, path, depth):
        self.node_count += 1
        if self.node_count > self.max_nodes:
            raise ConditionTreeError(
                "Condition tree exceeds the maximum of %d nodes." % self.max_nodes,
                path,
            )
        if depth > self.max_depth:
            raise ConditionTreeError(
                "Condition tree exceeds the maximum depth of %d." % self.max_depth,
                path,
            )
        if not isinstance(node, dict):
            raise ConditionTreeError(
                "Expected a condition object, got %r." % (node,), path
            )
        if "filter" in node:
            return self.visit_leaf(node, path)
        if len(node) != 1:
            raise ConditionTreeError(
                "A condition group must have exactly one of 'and', 'or' or "
                "'not'.",
                path,
            )
        operator, children = next(iter(node.items()))
        if operator not in OPERATORS:
            raise ConditionTreeError(
                "Unknown operator %r; expected one of 'and', 'or' or 'not'."
                % (operator,),
                path,
            )
        if operator == "not":
            return ~self.visit(children, path + ("not",), depth + 1)
        if not isinstance(children, (list, tuple)):
            raise ConditionTreeError(
                "'%s' expects a list of conditions." % operator,
                path + (operator,),
            )
        child_qs = [
            self.visit(child, path + (operator, index), depth + 1)
            for index, child in enumerate(children)
        ]
        if operator == "and":
            q = Q()
            for child_q in child_qs:
                q &= child_q
            return q
        if not child_qs:
            # An empty 'or' group matches nothing.
            return Q(pk__in=[])
        q = child_qs[0]
        for child_q in child_qs[1:]:
            q |= child_q
        return q

    def visit_leaf(self, node, path):
        unexpected = set(node) - {"filter", "value"}
        if unexpected:
            raise ConditionTreeError(
                "Unexpected keys %s; a leaf condition only accepts 'filter' "
                "and 'value'." % sorted(unexpected),
                path,
            )
        name = node["filter"]
        if not isinstance(name, str) or name not in self.filters:
            raise ConditionTreeError(
                "Unknown filter %r; leaves must reference a filter declared "
                "on the filterset." % (name,),
                path + ("filter",),
            )
        if "value" not in node:
            raise ConditionTreeError(
                "Leaf for filter '%s' requires a 'value'." % name, path
            )

        filter_ = self.filters[name]
        try:
            value = filter_.field.clean(node["value"])
        except forms.ValidationError as exc:
            raise ConditionTreeError(
                "Invalid value for filter '%s': %s"
                % (name, "; ".join(exc.messages)),
                path + ("value",),
            ) from exc
        if value in EMPTY_VALUES:
            raise ConditionTreeError(
                "Filter '%s' does not accept an empty value inside a "
                "condition tree." % name,
                path + ("value",),
            )

        try:
            q = filter_.get_q(value)
        except FilterNotCombinable as exc:
            raise ConditionTreeError(str(exc), path) from exc
        if filter_.distinct:
            self.requires_distinct = True
        return q
