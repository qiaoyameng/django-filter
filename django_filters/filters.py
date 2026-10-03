import copy
import json
import re

from collections import OrderedDict
from collections.abc import Iterable
from datetime import timedelta
from itertools import chain

from django import forms
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.core.validators import MaxValueValidator
from django.db import connections, models
from django.db.models import F, Func, Q, Value
from django.db.models.constants import LOOKUP_SEP
from django.db.models.fields.json import JSONField
from django.db.models.functions import Cast
from django.db.utils import NotSupportedError
from django.forms.utils import pretty_name
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from .conf import settings
from .constants import EMPTY_VALUES
from .fields import (
    BaseCSVField,
    BaseRangeField,
    ChoiceField,
    DateRangeField,
    DateTimeRangeField,
    IsoDateTimeField,
    IsoDateTimeRangeField,
    JSONPathField,
    LookupChoiceField,
    ModelChoiceField,
    ModelMultipleChoiceField,
    MultipleChoiceField,
    RangeField,
    TimeRangeField,
)
from .utils import get_model_field, label_for_filter

try:
    from django.utils.choices import normalize_choices
except ImportError:
    DJANGO_50 = False
else:
    DJANGO_50 = True


__all__ = [
    "AllValuesFilter",
    "AllValuesMultipleFilter",
    "BaseCSVFilter",
    "BaseInFilter",
    "BaseRangeFilter",
    "BooleanFilter",
    "CharFilter",
    "ChoiceFilter",
    "DateFilter",
    "DateFromToRangeFilter",
    "DateRangeFilter",
    "DateTimeFilter",
    "DateTimeFromToRangeFilter",
    "DurationFilter",
    "Filter",
    "IsoDateTimeFilter",
    "IsoDateTimeFromToRangeFilter",
    "JSONPath",
    "JSONPathFilter",
    "LookupChoiceFilter",
    "ModelChoiceFilter",
    "ModelMultipleChoiceFilter",
    "MultipleChoiceFilter",
    "NumberFilter",
    "NumericRangeFilter",
    "OrderingFilter",
    "RangeFilter",
    "TimeFilter",
    "TimeRangeFilter",
    "TypedChoiceFilter",
    "TypedMultipleChoiceFilter",
    "UUIDFilter",
]


class Filter:
    creation_counter = 0
    field_class = forms.Field

    def __init__(
        self,
        field_name=None,
        lookup_expr=None,
        *,
        label=None,
        method=None,
        distinct=False,
        exclude=False,
        **kwargs
    ):
        if lookup_expr is None:
            lookup_expr = settings.DEFAULT_LOOKUP_EXPR
        self.field_name = field_name
        self.lookup_expr = lookup_expr
        self.label = label
        self.method = method
        self.distinct = distinct
        self.exclude = exclude

        self.extra = kwargs
        self.extra.setdefault("required", False)

        self.creation_counter = Filter.creation_counter
        Filter.creation_counter += 1

    def get_method(self, qs):
        """Return filter method based on whether we're excluding
        or simply filtering.
        """
        return qs.exclude if self.exclude else qs.filter

    def method():
        """
        Filter method needs to be lazily resolved, as it may be dependent on
        the 'parent' FilterSet.
        """

        def fget(self):
            return self._method

        def fset(self, value):
            self._method = value

            # clear existing FilterMethod
            if isinstance(self.filter, FilterMethod):
                del self.filter

            # override filter w/ FilterMethod.
            if value is not None:
                self.filter = FilterMethod(self)

        return locals()

    method = property(**method())

    def label():
        def fget(self):
            if self._label is None and hasattr(self, "model"):
                self._label = label_for_filter(
                    self.model, self.field_name, self.lookup_expr, self.exclude
                )
            return self._label

        def fset(self, value):
            self._label = value

        return locals()

    label = property(**label())

    @property
    def field(self):
        if not hasattr(self, "_field"):
            field_kwargs = self.extra.copy()

            if settings.DISABLE_HELP_TEXT:
                field_kwargs.pop("help_text", None)

            self._field = self.field_class(label=self.label, **field_kwargs)
        return self._field

    def filter(self, qs, value):
        if value in EMPTY_VALUES:
            return qs
        if self.distinct:
            qs = qs.distinct()
        lookup = "%s__%s" % (self.field_name, self.lookup_expr)
        qs = self.get_method(qs)(**{lookup: value})
        return qs


class CharFilter(Filter):
    field_class = forms.CharField


class BooleanFilter(Filter):
    field_class = forms.NullBooleanField


class ChoiceFilter(Filter):
    field_class = ChoiceField

    def __init__(self, *args, **kwargs):
        self.null_value = kwargs.get("null_value", settings.NULL_CHOICE_VALUE)
        super().__init__(*args, **kwargs)

    def filter(self, qs, value):
        if value != self.null_value:
            return super().filter(qs, value)

        qs = self.get_method(qs)(
            **{"%s__%s" % (self.field_name, self.lookup_expr): None}
        )
        return qs.distinct() if self.distinct else qs


class TypedChoiceFilter(Filter):
    field_class = forms.TypedChoiceField


class UUIDFilter(Filter):
    field_class = forms.UUIDField


class MultipleChoiceFilter(Filter):
    """
    This filter performs OR(by default) or AND(using conjoined=True) query
    on the selected options.

    Advanced usage
    --------------
    Depending on your application logic, when all or no choices are selected,
    filtering may be a no-operation. In this case you may wish to avoid the
    filtering overhead, particularly if using a `distinct` call.

    You can override `get_filter_predicate` to use a custom filter.
    By default it will use the filter's name for the key, and the value will
    be the model object - or in case of passing in `to_field_name` the
    value of that attribute on the model.

    Set `always_filter` to `False` after instantiation to enable the default
    `is_noop` test. You can override `is_noop` if you need a different test
    for your application.

    `distinct` defaults to `True` as to-many relationships will generally
    require this.
    """

    field_class = MultipleChoiceField

    always_filter = True

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("distinct", True)
        self.conjoined = kwargs.pop("conjoined", False)
        self.null_value = kwargs.get("null_value", settings.NULL_CHOICE_VALUE)
        super().__init__(*args, **kwargs)

    def is_noop(self, qs, value):
        """
        Return `True` to short-circuit unnecessary and potentially slow
        filtering.
        """
        if self.always_filter:
            return False

        # A reasonable default for being a noop...
        if self.extra.get("required") and len(value) == len(self.field.choices):
            return True

        return False

    def filter(self, qs, value):
        if not value:
            # Even though not a noop, no point filtering if empty.
            return qs

        if self.is_noop(qs, value):
            return qs

        if not self.conjoined:
            q = Q()
        for v in set(value):
            if v == self.null_value:
                v = None
            predicate = self.get_filter_predicate(v)
            if self.conjoined:
                qs = self.get_method(qs)(**predicate)
            else:
                q |= Q(**predicate)

        if not self.conjoined:
            qs = self.get_method(qs)(q)

        return qs.distinct() if self.distinct else qs

    def get_filter_predicate(self, v):
        name = self.field_name
        if name and self.lookup_expr != settings.DEFAULT_LOOKUP_EXPR:
            name = LOOKUP_SEP.join([name, self.lookup_expr])
        try:
            return {name: getattr(v, self.field.to_field_name)}
        except (AttributeError, TypeError):
            return {name: v}


class TypedMultipleChoiceFilter(MultipleChoiceFilter):
    field_class = forms.TypedMultipleChoiceField


class DateFilter(Filter):
    field_class = forms.DateField


class DateTimeFilter(Filter):
    field_class = forms.DateTimeField


class IsoDateTimeFilter(DateTimeFilter):
    """
    Uses IsoDateTimeField to support filtering on ISO 8601 formatted datetimes.

    For context see:

    * https://code.djangoproject.com/ticket/23448
    * https://github.com/encode/django-rest-framework/issues/1338
    * https://github.com/carltongibson/django-filter/pull/264
    """

    field_class = IsoDateTimeField


class TimeFilter(Filter):
    field_class = forms.TimeField


class DurationFilter(Filter):
    field_class = forms.DurationField


class QuerySetRequestMixin:
    """
    Add callable functionality to filters that support the ``queryset``
    argument. If the ``queryset`` is callable, then it **must** accept the
    ``request`` object as a single argument.

    This is useful for filtering querysets by properties on the ``request``
    object, such as the user.

    Example::

        def departments(request):
            company = request.user.company
            return company.department_set.all()

        class EmployeeFilter(filters.FilterSet):
            department = filters.ModelChoiceFilter(queryset=departments)
            ...

    The above example restricts the set of departments to those in the logged-in
    user's associated company.

    """

    def __init__(self, *args, **kwargs):
        self.queryset = kwargs.get("queryset")
        super().__init__(*args, **kwargs)

    def get_request(self):
        try:
            return self.parent.request
        except AttributeError:
            return None

    def get_queryset(self, request):
        queryset = self.queryset

        if callable(queryset):
            return queryset(request)
        return queryset

    @property
    def field(self):
        request = self.get_request()
        queryset = self.get_queryset(request)

        if queryset is not None:
            self.extra["queryset"] = queryset

        return super().field


class ModelChoiceFilter(QuerySetRequestMixin, ChoiceFilter):
    field_class = ModelChoiceField

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("empty_label", settings.EMPTY_CHOICE_LABEL)
        super().__init__(*args, **kwargs)


class ModelMultipleChoiceFilter(QuerySetRequestMixin, MultipleChoiceFilter):
    field_class = ModelMultipleChoiceField


class NumberFilter(Filter):
    field_class = forms.DecimalField

    def get_max_validator(self):
        """
        Return a MaxValueValidator for the field, or None to disable.
        """
        return MaxValueValidator(1e50)

    @property
    def field(self):
        if not hasattr(self, "_field"):
            field = super().field
            max_validator = self.get_max_validator()
            if max_validator:
                field.validators.append(max_validator)

            self._field = field
        return self._field


class NumericRangeFilter(Filter):
    field_class = RangeField

    def filter(self, qs, value):
        if value:
            if value.start is not None and value.stop is not None:
                value = (value.start, value.stop)
            elif value.start is not None:
                self.lookup_expr = "startswith"
                value = value.start
            elif value.stop is not None:
                self.lookup_expr = "endswith"
                value = value.stop

        return super().filter(qs, value)


class RangeFilter(Filter):
    field_class = RangeField

    def filter(self, qs, value):
        if value:
            if value.start is not None and value.stop is not None:
                self.lookup_expr = "range"
                value = (value.start, value.stop)
            elif value.start is not None:
                self.lookup_expr = "gte"
                value = value.start
            elif value.stop is not None:
                self.lookup_expr = "lte"
                value = value.stop

        return super().filter(qs, value)


def _truncate(dt):
    return dt.date()


class DateRangeFilter(ChoiceFilter):
    choices = [
        ("today", _("Today")),
        ("yesterday", _("Yesterday")),
        ("week", _("Past 7 days")),
        ("month", _("This month")),
        ("year", _("This year")),
    ]

    filters = {
        "today": lambda qs, name: qs.filter(
            **{
                "%s__year" % name: now().year,
                "%s__month" % name: now().month,
                "%s__day" % name: now().day,
            }
        ),
        "yesterday": lambda qs, name: qs.filter(
            **{
                "%s__year" % name: (now() - timedelta(days=1)).year,
                "%s__month" % name: (now() - timedelta(days=1)).month,
                "%s__day" % name: (now() - timedelta(days=1)).day,
            }
        ),
        "week": lambda qs, name: qs.filter(
            **{
                "%s__gte" % name: _truncate(now() - timedelta(days=7)),
                "%s__lt" % name: _truncate(now() + timedelta(days=1)),
            }
        ),
        "month": lambda qs, name: qs.filter(
            **{"%s__year" % name: now().year, "%s__month" % name: now().month}
        ),
        "year": lambda qs, name: qs.filter(
            **{
                "%s__year" % name: now().year,
            }
        ),
    }

    def __init__(self, choices=None, filters=None, *args, **kwargs):
        if choices is not None:
            self.choices = choices
        if filters is not None:
            self.filters = filters

        if isinstance(self.choices, dict):
            if DJANGO_50:
                self.choices = normalize_choices(self.choices)
            else:
                raise ValueError("Django 5.0 or later is required for dict choices")

        all_choices = list(
            chain.from_iterable(
                [subchoice[0] for subchoice in choice[1]]
                if isinstance(choice[1], (list, tuple))  # This is an optgroup
                else [choice[0]]
                for choice in self.choices
            )
        )
        unique = set(all_choices) ^ set(self.filters)
        assert not unique, (
            "Keys must be present in both 'choices' and 'filters'. Missing keys: "
            "'%s'" % ", ".join(sorted(unique))
        )

        # null choice not relevant
        kwargs.setdefault("null_label", None)
        super().__init__(choices=self.choices, *args, **kwargs)

    def filter(self, qs, value):
        if not value:
            return qs

        assert value in self.filters

        qs = self.filters[value](qs, self.field_name)
        return qs.distinct() if self.distinct else qs


class DateFromToRangeFilter(RangeFilter):
    field_class = DateRangeField


class DateTimeFromToRangeFilter(RangeFilter):
    field_class = DateTimeRangeField


class IsoDateTimeFromToRangeFilter(RangeFilter):
    field_class = IsoDateTimeRangeField


class TimeRangeFilter(RangeFilter):
    field_class = TimeRangeField


class AllValuesFilter(ChoiceFilter):
    @property
    def field(self):
        qs = self.model._default_manager.distinct()
        qs = qs.order_by(self.field_name).values_list(self.field_name, flat=True)
        self.extra["choices"] = [(o, o) for o in qs]
        return super().field


class AllValuesMultipleFilter(MultipleChoiceFilter):
    @property
    def field(self):
        qs = self.model._default_manager.distinct()
        qs = qs.order_by(self.field_name).values_list(self.field_name, flat=True)
        self.extra["choices"] = [(o, o) for o in qs]
        return super().field


class BaseCSVFilter(Filter):
    """
    Base class for CSV type filters, such as IN and RANGE.
    """

    base_field_class = BaseCSVField

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("help_text", _("Multiple values may be separated by commas."))
        super().__init__(*args, **kwargs)

        class ConcreteCSVField(self.base_field_class, self.field_class):
            pass

        ConcreteCSVField.__name__ = self._field_class_name(
            self.field_class, self.lookup_expr
        )

        self.field_class = ConcreteCSVField

    @classmethod
    def _field_class_name(cls, field_class, lookup_expr):
        """
        Generate a suitable class name for the concrete field class. This is not
        completely reliable, as not all field class names are of the format
        <Type>Field.

        ex::

            BaseCSVFilter._field_class_name(DateTimeField, 'year__in')

            returns 'DateTimeYearInField'

        """
        # DateTimeField => DateTime
        type_name = field_class.__name__
        if type_name.endswith("Field"):
            type_name = type_name[:-5]

        # year__in => YearIn
        parts = lookup_expr.split(LOOKUP_SEP)
        expression_name = "".join(p.capitalize() for p in parts)

        # DateTimeYearInField
        return str("%s%sField" % (type_name, expression_name))


class BaseInFilter(BaseCSVFilter):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("lookup_expr", "in")
        super().__init__(*args, **kwargs)


class BaseRangeFilter(BaseCSVFilter):
    base_field_class = BaseRangeField

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("lookup_expr", "range")
        super().__init__(*args, **kwargs)


class LookupChoiceFilter(Filter):
    """
    A combined filter that allows users to select the lookup expression from a dropdown.

    * ``lookup_choices`` is an optional argument that accepts multiple input
      formats, and is ultimately normalized as the choices used in the lookup
      dropdown. See ``.get_lookup_choices()`` for more information.

    * ``field_class`` is an optional argument that allows you to set the inner
      form field class used to validate the value. Default: ``forms.CharField``

    ex::

        price = django_filters.LookupChoiceFilter(
            field_class=forms.DecimalField,
            lookup_choices=[
                ('exact', 'Equals'),
                ('gt', 'Greater than'),
                ('lt', 'Less than'),
            ]
        )

    """

    field_class = forms.CharField
    outer_class = LookupChoiceField

    def __init__(
        self, field_name=None, lookup_choices=None, field_class=None, **kwargs
    ):
        self.empty_label = kwargs.pop("empty_label", settings.EMPTY_CHOICE_LABEL)

        super(LookupChoiceFilter, self).__init__(field_name=field_name, **kwargs)

        self.lookup_choices = lookup_choices
        if field_class is not None:
            self.field_class = field_class

    @classmethod
    def normalize_lookup(cls, lookup):
        """
        Normalize the lookup into a tuple of ``(lookup expression, display value)``

        If the ``lookup`` is already a tuple, the tuple is not altered.
        If the ``lookup`` is a string, a tuple is returned with the lookup
        expression used as the basis for the display value.

        ex::

            >>> LookupChoiceFilter.normalize_lookup(('exact', 'Equals'))
            ('exact', 'Equals')

            >>> LookupChoiceFilter.normalize_lookup('has_key')
            ('has_key', 'Has key')

        """
        if isinstance(lookup, str):
            return (lookup, pretty_name(lookup))
        return (lookup[0], lookup[1])

    def get_lookup_choices(self):
        """
        Get the lookup choices in a format suitable for ``django.forms.ChoiceField``.
        If the filter is initialized with ``lookup_choices``, this value is normalized
        and passed to the underlying ``LookupChoiceField``. If no choices are provided,
        they are generated from the corresponding model field's registered lookups.
        """
        lookups = self.lookup_choices
        if lookups is None:
            field = get_model_field(self.model, self.field_name)
            lookups = field.get_lookups()

        return [self.normalize_lookup(lookup) for lookup in lookups]

    @property
    def field(self):
        if not hasattr(self, "_field"):
            inner_field = super().field
            lookups = self.get_lookup_choices()

            self._field = self.outer_class(
                inner_field,
                lookups,
                label=self.label,
                empty_label=self.empty_label,
                required=self.extra["required"],
            )

        return self._field

    def filter(self, qs, lookup):
        if not lookup:
            return super().filter(qs, None)

        self.lookup_expr = lookup.lookup_expr
        return super().filter(qs, lookup.value)


class JSONExtract(Func):
    """
    Extracts a value from a JSON document using ``json_extract()``, with the
    JSON path passed as a bound parameter instead of being interpolated into
    the SQL.
    """

    function = "json_extract"
    arity = 2
    output_field = JSONField()


class JSONType(Func):
    """
    Returns the JSON type of a value within a JSON document using
    ``json_type()``, with the JSON path passed as a bound parameter.
    """

    function = "json_type"
    arity = 2
    output_field = models.CharField()


class JSONPath:
    """
    Declaration of a single filterable path inside a ``JSONField``, used
    with ``JSONPathFilter``.

    * ``path`` is a list of key segments (strings) and array indexes
      (integers) addressing a value inside the JSON document. Segments are
      never interpreted as ORM lookups, so keys may contain ``__``, ``.`` or
      quotes.

    * ``field_class`` is the form field class used to validate the
      comparison value. It also determines which JSON types are comparable and
      the database type numeric values are compared as.

    * ``lookups`` are the lookup expressions allowed for this path.

    * ``db_type`` is an optional model field instance used to cast extracted
      values. Defaults to a type inferred from ``field_class``.
    """

    INTEGER = "integer"
    NUMBER = "number"
    TEXT = "text"
    BOOLEAN = "boolean"

    #: Form field classes mapped to ``(kind, db type factory)``. The kind
    #: determines which JSON types are comparable; the db type is used to cast
    #: extracted values for comparison. Note that ``forms.DecimalField`` and
    #: ``forms.FloatField`` are subclasses of ``forms.IntegerField``, so the
    #: order matters.
    FIELD_TYPES = (
        (
            forms.DecimalField,
            NUMBER,
            lambda: models.DecimalField(max_digits=38, decimal_places=10),
        ),
        (forms.FloatField, NUMBER, models.FloatField),
        (forms.IntegerField, INTEGER, models.BigIntegerField),
        (forms.BooleanField, BOOLEAN, None),
        (forms.CharField, TEXT, None),
    )

    #: Lookup expressions that may be declared for a path.
    ALLOWED_LOOKUPS = frozenset(
        [
            "exact",
            "iexact",
            "gt",
            "gte",
            "lt",
            "lte",
            "contains",
            "icontains",
            "startswith",
            "istartswith",
            "endswith",
            "iendswith",
        ]
    )

    LOOKUP_RE = re.compile(r"^[a-z0-9_]+$")

    #: JSON types, as returned by the backend's JSON type function, that are
    #: considered comparable for each kind, per database vendor.
    JSON_TYPE_NAMES = {
        "sqlite": {
            INTEGER: ("integer",),
            NUMBER: ("integer", "real"),
            TEXT: ("text",),
            BOOLEAN: ("true", "false"),
        },
    }

    def __init__(
        self, path, *, field_class=forms.CharField, lookups=("exact",), db_type=None
    ):
        self.path = self.validate_path(path)
        self.field_class, self._field_template = self.validate_field_class(field_class)
        self.kind, default_db_type = self.resolve_field_type(self.field_class)
        self.lookups = self.validate_lookups(lookups)
        if db_type is not None and not isinstance(db_type, models.Field):
            raise ImproperlyConfigured(
                "JSONPath 'db_type' must be a model field instance, got %r."
                % (db_type,)
            )
        self.db_type = db_type if db_type is not None else default_db_type

    @staticmethod
    def validate_path(path):
        if not isinstance(path, (list, tuple)) or not path:
            raise ImproperlyConfigured(
                "JSONPath 'path' must be a non-empty list or tuple of key "
                "segments and array indexes."
            )
        segments = []
        for segment in path:
            if isinstance(segment, bool) or not isinstance(segment, (str, int)):
                raise ImproperlyConfigured(
                    "JSONPath segments must be strings or integers, got %r."
                    % (segment,)
                )
            if isinstance(segment, str) and not segment:
                raise ImproperlyConfigured("JSONPath key segments must not be empty.")
            if isinstance(segment, int) and segment < 0:
                raise ImproperlyConfigured(
                    "JSONPath array indexes must not be negative."
                )
            segments.append(segment)
        return tuple(segments)

    @staticmethod
    def validate_field_class(field_class):
        template = None
        if isinstance(field_class, forms.Field):
            template = field_class
            field_class = type(template)
        if not (isinstance(field_class, type) and issubclass(field_class, forms.Field)):
            raise ImproperlyConfigured(
                "JSONPath 'field_class' must be a form field class or "
                "instance, got %r." % (field_class,)
            )
        return field_class, template

    @classmethod
    def resolve_field_type(cls, field_class):
        for base, kind, db_type_factory in cls.FIELD_TYPES:
            if issubclass(field_class, base):
                return kind, db_type_factory() if db_type_factory else None
        raise ImproperlyConfigured(
            "JSONPath does not support the %r form field class. Supported "
            "base classes are: %s."
            % (
                field_class,
                ", ".join(base.__name__ for base, _, _ in cls.FIELD_TYPES),
            )
        )

    @classmethod
    def validate_lookups(cls, lookups):
        if isinstance(lookups, str) or not lookups:
            raise ImproperlyConfigured(
                "JSONPath 'lookups' must be a non-empty iterable of lookup "
                "expressions."
            )
        validated = []
        for lookup in lookups:
            if not isinstance(lookup, str) or not cls.LOOKUP_RE.match(lookup):
                raise ImproperlyConfigured(
                    "JSONPath lookup %r is not a valid lookup expression."
                    % (lookup,)
                )
            if lookup not in cls.ALLOWED_LOOKUPS:
                raise ImproperlyConfigured(
                    "JSONPath does not support the %r lookup. Supported "
                    "lookups are: %s."
                    % (lookup, ", ".join(sorted(cls.ALLOWED_LOOKUPS)))
                )
            if lookup not in validated:
                validated.append(lookup)
        return tuple(validated)

    def build_form_field(self, **kwargs):
        """Return a fresh form field instance for cleaning a comparison value."""
        if self._field_template is not None:
            field = copy.deepcopy(self._field_template)
            for name, value in kwargs.items():
                setattr(field, name, value)
            return field
        return self.field_class(**kwargs)

    def compile_json_path(self):
        """
        Compile the declared path to a JSON path string. Keys are quoted and
        escaped with ``json.dumps()``; the result is meant to be passed to
        the database as a bound parameter, never interpolated into SQL.
        """
        parts = ["$"]
        for segment in self.path:
            if isinstance(segment, int):
                parts.append("[%d]" % segment)
            else:
                parts.append(".%s" % json.dumps(segment))
        return "".join(parts)

    def check_supported(self, vendor):
        """Raise ``NotSupportedError`` if the path cannot be addressed on ``vendor``."""
        if vendor == "sqlite" and any(
            isinstance(segment, str) and '"' in segment for segment in self.path
        ):
            raise NotSupportedError(
                "JSONPathFilter cannot address keys containing double quotes on "
                "the 'sqlite' backend."
            )


class JSONPathFilter(Filter):
    """
    Filters a ``JSONField`` by user-selected, pre-declared JSON paths.

    * ``paths`` maps path names to ``JSONPath`` declarations. The path name
      is what users select; the declared path itself is never taken from
      user input, so JSON keys containing ``__``, ``.`` or quotes cannot be
      misinterpreted as ORM lookups.

    The comparison value is validated by the form field class declared for
    the selected path, and is always passed to the database as a bound
    parameter.

    ex::

        class ProductFilter(FilterSet):
            attrs = JSONPathFilter(
                field_name="attrs",
                paths={
                    "price": JSONPath(
                        ["price"],
                        field_class=forms.DecimalField,
                        lookups=["exact", "gte", "lte"],
                    ),
                    "first_tag": JSONPath(
                        ["tags", 0], lookups=["exact", "icontains"]
                    ),
                },
            )

            class Meta:
                model = Product
                fields = []

    filters query strings such as
    ``?attrs_path=price&attrs_lookup=gte&attrs_value=10``.
    """

    def __init__(self, field_name=None, *, paths, **kwargs):
        if not isinstance(paths, dict) or not paths:
            raise ImproperlyConfigured(
                "JSONPathFilter requires a non-empty 'paths' dict mapping "
                "path names to JSONPath declarations."
            )
        for name, config in paths.items():
            if not isinstance(name, str) or not name:
                raise ImproperlyConfigured(
                    "JSONPathFilter path names must be non-empty strings, got %r."
                    % (name,)
                )
            if not isinstance(config, JSONPath):
                raise ImproperlyConfigured(
                    "JSONPathFilter path %r must be declared with a JSONPath "
                    "instance." % (name,)
                )
        self.paths = dict(paths)
        super().__init__(field_name=field_name, **kwargs)

    @property
    def field(self):
        if not hasattr(self, "_field"):
            field_kwargs = {
                "label": self.label,
                "required": self.extra["required"],
            }
            if not settings.DISABLE_HELP_TEXT and "help_text" in self.extra:
                field_kwargs["help_text"] = self.extra["help_text"]
            self._field = JSONPathField(self.paths, **field_kwargs)
        return self._field

    def get_json_field(self):
        try:
            field = get_model_field(self.model, self.field_name)
        except FieldDoesNotExist as e:
            raise ImproperlyConfigured(
                "JSONPathFilter cannot resolve field %r on model %s."
                % (self.field_name, self.model.__name__)
            ) from e
        if not isinstance(field, JSONField):
            raise ImproperlyConfigured(
                "JSONPathFilter can only filter JSONFields. %r on model %s "
                "is a %s."
                % (self.field_name, self.model.__name__, type(field).__name__)
            )
        return field

    @staticmethod
    def get_extracted_output_field(config):
        if config.kind == JSONPath.TEXT:
            return models.CharField()
        if config.kind == JSONPath.BOOLEAN:
            return models.BooleanField()
        return JSONField()

    def filter(self, qs, value):
        if value is None:
            return qs

        config = self.paths[value.path]
        self.get_json_field()

        connection = connections[qs.db]
        try:
            json_types = JSONPath.JSON_TYPE_NAMES[connection.vendor][config.kind]
        except KeyError:
            raise NotSupportedError(
                "JSONPathFilter does not support the %r database backend."
                % connection.vendor
            )
        config.check_supported(connection.vendor)

        path = Value(config.compile_json_path(), output_field=models.CharField())
        source = F(self.field_name)
        extracted = JSONExtract(source, path)
        json_type = JSONType(source, path)

        value_alias = "_jsonpath_%x_value" % id(self)
        type_alias = "_jsonpath_%x_type" % id(self)

        if config.db_type is not None:
            value_expression = Cast(extracted, config.db_type)
        else:
            extracted.output_field = self.get_extracted_output_field(config)
            value_expression = extracted

        qs = qs.annotate(**{value_alias: value_expression, type_alias: json_type})

        condition = Q(**{"%s__in" % type_alias: json_types}) & Q(
            **{"%s__%s" % (value_alias, value.lookup): value.value}
        )
        if self.distinct:
            qs = qs.distinct()
        return self.get_method(qs)(condition)


class OrderingFilter(BaseCSVFilter, ChoiceFilter):
    """
    Enable queryset ordering. As an extension of ``ChoiceFilter`` it accepts
    two additional arguments that are used to build the ordering choices.

    * ``fields`` is a mapping of {model field name: parameter name}. The
      parameter names are exposed in the choices and mask/alias the field
      names used in the ``order_by()`` call. Similar to field ``choices``,
      ``fields`` accepts the 'list of two-tuples' syntax that retains order.
      ``fields`` may also just be an iterable of strings. In this case, the
      field names simply double as the exposed parameter names.

    * ``field_labels`` is an optional argument that allows you to customize
      the display label for the corresponding parameter. It accepts a mapping
      of {field name: human readable label}. Keep in mind that the key is the
      field name, and not the exposed parameter name.

    Additionally, you can just provide your own ``choices`` if you require
    explicit control over the exposed options. For example, when you might
    want to disable descending sort options.

    This filter is also CSV-based, and accepts multiple ordering params. The
    default select widget does not enable the use of this, but it is useful
    for APIs.

    """

    descending_fmt = _("%s (descending)")

    def __init__(self, *args, **kwargs):
        """
        ``fields`` may be either a mapping or an iterable.
        ``field_labels`` must be a map of field names to display labels
        """
        fields = kwargs.pop("fields", {})
        fields = self.normalize_fields(fields)
        field_labels = kwargs.pop("field_labels", {})

        self.param_map = {v: k for k, v in fields.items()}

        if "choices" not in kwargs:
            kwargs["choices"] = self.build_choices(fields, field_labels)

        kwargs.setdefault("label", _("Ordering"))
        kwargs.setdefault("help_text", "")
        kwargs.setdefault("null_label", None)
        super().__init__(*args, **kwargs)

    def get_ordering_value(self, param):
        descending = param.startswith("-")
        param = param[1:] if descending else param
        field_name = self.param_map.get(param, param)

        return "-%s" % field_name if descending else field_name

    def filter(self, qs, value):
        if value in EMPTY_VALUES:
            return qs

        ordering = [
            self.get_ordering_value(param)
            for param in value
            if param not in EMPTY_VALUES
        ]
        return qs.order_by(*ordering)

    @classmethod
    def normalize_fields(cls, fields):
        """
        Normalize the fields into an ordered map of {field name: param name}
        """
        # fields is a mapping, copy into new OrderedDict
        if isinstance(fields, dict):
            return OrderedDict(fields)

        # convert iterable of values => iterable of pairs (field name, param name)
        assert isinstance(
            fields, Iterable
        ), "'fields' must be an iterable (e.g., a list, tuple, or mapping)."

        # fields is an iterable of field names
        assert all(
            isinstance(field, str)
            or isinstance(field, Iterable)
            and len(field) == 2  # may need to be wrapped in parens
            for field in fields
        ), "'fields' must contain strings or (field name, param name) pairs."

        return OrderedDict([(f, f) if isinstance(f, str) else f for f in fields])

    def build_choices(self, fields, labels):
        ascending = [
            (param, labels.get(field, _(pretty_name(param))))
            for field, param in fields.items()
        ]
        descending = [
            ("-%s" % param, labels.get("-%s" % param, self.descending_fmt % label))
            for param, label in ascending
        ]

        # interleave the ascending and descending choices
        return [val for pair in zip(ascending, descending) for val in pair]


class FilterMethod:
    """
    This helper is used to override Filter.filter() when a 'method' argument
    is passed. It proxies the call to the actual method on the filter's parent.
    """

    def __init__(self, filter_instance):
        self.f = filter_instance

    def __call__(self, qs, value):
        if value in EMPTY_VALUES:
            return qs

        return self.method(qs, self.f.field_name, value)

    @property
    def method(self):
        """
        Resolve the method on the parent filterset.
        """
        instance = self.f

        # noop if 'method' is a function
        if callable(instance.method):
            return instance.method

        # otherwise, method is the name of a method on the parent FilterSet.
        assert hasattr(
            instance, "parent"
        ), "Filter '%s' must have a parent FilterSet to find '.%s()'" % (
            instance.field_name,
            instance.method,
        )

        parent = instance.parent
        method = getattr(parent, instance.method, None)

        assert callable(
            method
        ), "Expected parent FilterSet '%s.%s' to have a '.%s()' method." % (
            parent.__class__.__module__,
            parent.__class__.__name__,
            instance.method,
        )

        return method
