ALL_FIELDS = "__all__"


EMPTY_VALUES = ([], (), {}, "", None)


# Explicit empty-value operators. They are transmitted via a separate form
# parameter (the "<name>_operator" input) so that the value parameter remains a
# real, independently cleaned value. In particular, neither the empty string
# nor a literal "null" is used as a sentinel for SQL NULL.
EQUALS = "equals"
NOT_EQUALS = "not_equals"
IS_NULL = "isnull"
IS_NOT_NULL = "isnotnull"
IS_EMPTY = "isempty"
IS_NOT_EMPTY = "isnotempty"

# Operators that do not consume a value.
VALUELESS_OPERATORS = frozenset(
    {IS_NULL, IS_NOT_NULL, IS_EMPTY, IS_NOT_EMPTY}
)

# Operators supported by every text-capable EmptyValueFilter.
EMPTY_VALUE_OPERATORS = (
    EQUALS,
    NOT_EQUALS,
    IS_NULL,
    IS_NOT_NULL,
    IS_EMPTY,
    IS_NOT_EMPTY,
)
