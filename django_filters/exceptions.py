from django.core.exceptions import FieldError


class FieldLookupError(FieldError):
    def __init__(self, model_field, lookup_expr):
        super().__init__(
            "Unsupported lookup '%s' for field '%s'." % (lookup_expr, model_field)
        )


class FilterDependencyError(ValueError):
    """
    Raised when ``depends_on`` declarations between filters are invalid
    (unknown name, self-dependency or cycle) or when a filter's dependencies
    did not provide a usable value during evaluation.
    """
