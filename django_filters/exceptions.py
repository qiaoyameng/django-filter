from django.core.exceptions import FieldError


class FieldLookupError(FieldError):
    def __init__(self, model_field, lookup_expr):
        super().__init__(
            "Unsupported lookup '%s' for field '%s'." % (lookup_expr, model_field)
        )


class NotComposableFilterError(Exception):
    """
    Raised by ``Filter.get_q()`` when a filter cannot be expressed as a
    ``Q`` object, and therefore cannot be used as a condition tree leaf.
    """


class ConditionTreeError(Exception):
    """
    One or more errors found while validating a condition tree.

    ``errors`` is a list of ``(path, message)`` tuples, where ``path``
    locates the offending node within the tree, e.g. ``$.or[0].and[1].value``.
    """

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__(
            "; ".join("%s: %s" % (path, message) for path, message in self.errors)
        )
