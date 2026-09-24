from django.core.exceptions import FieldError, ValidationError


class FieldLookupError(FieldError):
    def __init__(self, model_field, lookup_expr):
        super().__init__(
            "Unsupported lookup '%s' for field '%s'." % (lookup_expr, model_field)
        )


class FilterNotCombinable(TypeError):
    """
    Raised when a filter cannot be expressed as a ``Q`` object and therefore
    cannot be used as a leaf inside a condition tree.
    """


class ConditionTreeError(ValidationError):
    """
    Raised when a condition tree passed to a ``FilterSet`` is invalid.

    The ``path`` attribute locates the offending node within the tree, e.g.
    ``("and", 0, "or", 1)``, rendered in the message as ``and[0].or[1]``.
    """

    def __init__(self, message, path=()):
        self.path = tuple(path)
        if self.path:
            message = "%s: %s" % (self.format_path(self.path), message)
        super().__init__(message)

    @staticmethod
    def format_path(path):
        parts = []
        for step in path:
            if isinstance(step, int):
                if parts:
                    parts[-1] += "[%d]" % step
                else:
                    parts.append("[%d]" % step)
            else:
                parts.append(str(step))
        return ".".join(parts)
