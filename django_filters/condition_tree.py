import operator
from functools import reduce

from django.core.exceptions import ValidationError
from django.db.models import Q

from .constants import EMPTY_VALUES
from .exceptions import ConditionTreeError, NotComposableFilterError

__all__ = [
    "And",
    "Or",
    "Not",
    "Leaf",
    "ConditionTreeCompiler",
]


class Leaf:
    """
    A condition tree leaf referencing a declared filter by name.

    ``value`` is cleaned by the filter's form field before being turned
    into a ``Q`` object via the filter's ``get_q()`` method.
    """

    def __init__(self, filter, value):
        self.filter = filter
        self.value = value

    def __repr__(self):
        return "Leaf(%r, %r)" % (self.filter, self.value)


class _Group:
    def __init__(self, *children):
        self.children = list(children)

    def __repr__(self):
        return "%s(%s)" % (
            type(self).__name__,
            ", ".join(repr(child) for child in self.children),
        )


class And(_Group):
    """A condition tree node matching when all of its children match."""


class Or(_Group):
    """A condition tree node matching when any of its children match."""


class Not:
    """A condition tree node negating its single child."""

    def __init__(self, child):
        self.child = child

    def __repr__(self):
        return "Not(%r)" % (self.child,)


class ConditionTreeCompiler:
    """
    Validate a condition tree against a filterset's filters and compile it
    into a single ``Q`` object.

    The tree may be built from ``And``/``Or``/``Not``/``Leaf`` node objects
    or from plain dicts, e.g.::

        {"or": [
            {"and": [{"filter": "status", "value": "published"},
                     {"filter": "title", "value": "django"}]},
            {"not": {"filter": "author", "value": ["alice", "bob"]}},
        ]}

    Leaf values are cleaned by the corresponding filter's form field, and
    leaves may only reference filters declared on the filterset. All
    validation happens before any query is executed; errors are collected
    and raised as a single ``ConditionTreeError`` with per-node paths.
    """

    def __init__(self, filters, *, max_depth, max_nodes):
        self.filters = filters
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.errors = []
        self.node_count = 0
        self._node_limit_reported = False
        #: Whether any used leaf filter requires ``distinct()``.
        self.distinct = False

    def compile(self, tree):
        """
        Return the compiled ``Q`` object, or ``None`` if the tree does not
        constrain the query (e.g. it only contains empty groups). Raise
        ``ConditionTreeError`` if the tree is invalid.
        """
        q = self._compile_node(tree, path="$", depth=1)
        if self.errors:
            raise ConditionTreeError(self.errors)
        return q

    def error(self, path, message):
        self.errors.append((path, message))

    def _compile_node(self, node, path, depth):
        self.node_count += 1
        if self.node_count > self.max_nodes:
            if not self._node_limit_reported:
                self._node_limit_reported = True
                self.error(
                    path,
                    "Condition tree exceeds the maximum of %d nodes." % self.max_nodes,
                )
            return None
        if depth > self.max_depth:
            self.error(
                path,
                "Condition tree exceeds the maximum depth of %d." % self.max_depth,
            )
            return None

        spec = self._normalize(node, path)
        if spec is None:
            return None
        kind, payload = spec

        if kind == "leaf":
            filter_name, value = payload
            return self._compile_leaf(filter_name, value, path)
        if kind == "not":
            q = self._compile_node(payload, path + ".not", depth + 1)
            return ~q if q is not None else None

        children = [
            self._compile_node(child, "%s.%s[%d]" % (path, kind, index), depth + 1)
            for index, child in enumerate(payload)
        ]
        children = [q for q in children if q is not None]
        if not children:
            # Empty groups (or groups whose children are all no-ops) do not
            # constrain the query.
            return None
        combine = operator.and_ if kind == "and" else operator.or_
        return reduce(combine, children)

    def _normalize(self, node, path):
        """
        Normalize a node object or dict spec into a ``(kind, payload)``
        tuple, recording an error and returning ``None`` if invalid.
        """
        if isinstance(node, Leaf):
            return "leaf", (node.filter, node.value)
        if isinstance(node, And):
            return "and", node.children
        if isinstance(node, Or):
            return "or", node.children
        if isinstance(node, Not):
            return "not", node.child
        if isinstance(node, dict):
            if "filter" in node or "value" in node:
                unexpected = set(node) - {"filter", "value"}
                if unexpected:
                    self.error(
                        path,
                        "Unexpected keys %s. Leaves may only reference a "
                        "declared filter; lookups cannot be supplied here."
                        % sorted(unexpected),
                    )
                    return None
                missing = {"filter", "value"} - set(node)
                if missing:
                    self.error(path, "Leaf is missing keys %s." % sorted(missing))
                    return None
                return "leaf", (node["filter"], node["value"])
            for kind in ("and", "or", "not"):
                if kind in node:
                    unexpected = set(node) - {kind}
                    if unexpected:
                        self.error(path, "Unexpected keys %s." % sorted(unexpected))
                        return None
                    payload = node[kind]
                    if kind in ("and", "or"):
                        if not isinstance(payload, (list, tuple)):
                            self.error(
                                path + "." + kind,
                                "Expected a list of child nodes, got %s."
                                % type(payload).__name__,
                            )
                            return None
                        return kind, list(payload)
                    return "not", payload
            self.error(
                path,
                "Invalid condition node. Expected one of 'and', 'or', 'not', "
                "or a {'filter', 'value'} leaf.",
            )
            return None
        self.error(
            path,
            "Invalid condition node of type %s." % type(node).__name__,
        )
        return None

    def _compile_leaf(self, filter_name, value, path):
        if not isinstance(filter_name, str) or filter_name not in self.filters:
            self.error(
                path + ".filter",
                "Unknown filter %r. Leaves may only reference filters "
                "declared on the FilterSet." % (filter_name,),
            )
            return None
        filter_ = self.filters[filter_name]

        try:
            cleaned = filter_.field.clean(value)
        except ValidationError as exc:
            self.error(path + ".value", "; ".join(exc.messages))
            return None

        if cleaned in EMPTY_VALUES:
            return None

        try:
            q = filter_.get_q(cleaned)
        except NotComposableFilterError as exc:
            self.error(path + ".filter", str(exc))
            return None

        if q is not None and filter_.distinct:
            self.distinct = True
        return q
