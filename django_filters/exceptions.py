from django.core.exceptions import FieldError, ValidationError


class FieldLookupError(FieldError):
    def __init__(self, model_field, lookup_expr):
        super().__init__(
            "Unsupported lookup '%s' for field '%s'." % (lookup_expr, model_field)
        )


class RelatedSetValidationError(ValidationError):
    """
    Wraps a child ``FilterSet``'s error dict so that the parent form can
    expand it into per-field errors, preserving each child field's location.
    """
