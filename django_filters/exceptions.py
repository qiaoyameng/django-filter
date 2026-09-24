from django.core.exceptions import FieldError


class FieldLookupError(FieldError):
    def __init__(self, model_field, lookup_expr):
        super().__init__(
            "Unsupported lookup '%s' for field '%s'." % (lookup_expr, model_field)
        )


class FilterDependencyError(Exception):
    """
    Raised when a filter cannot be applied because one or more of its
    declared ``depends_on`` dependencies did not produce a context.
    """
