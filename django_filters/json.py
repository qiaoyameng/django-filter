import hashlib
import json

from django import forms
from django.core.exceptions import ValidationError
from django.db import NotSupportedError
from django.db.models import Q, TextField
from django.db.models.expressions import F, Func, Value
from django.db.models.fields import (
    BooleanField,
    CharField,
    DecimalField,
    Field,
    FloatField,
    IntegerField,
)
from django.db.models.functions import Cast
from django.utils.translation import gettext_lazy as _

from .widgets import SuffixedMultiWidget


TEXT_LOOKUPS = {"exact", "iexact", "contains", "icontains", "startswith", "endswith"}
ORDERING_LOOKUPS = {"gt", "gte", "lt", "lte"}
UNARY_LOOKUPS = {"has_key", "isnull"}
EXPLICIT_NULL_LOOKUPS = {"missing_key", "sql_isnull", "json_isnull"}
DEFAULT_LOOKUPS = frozenset(
    ("exact", "iexact", "contains", "icontains", "startswith", "endswith")
    + tuple(ORDERING_LOOKUPS)
    + tuple(UNARY_LOOKUPS)
    + tuple(EXPLICIT_NULL_LOOKUPS)
)

NUMERIC_DB_FIELDS = (
    DecimalField,
    FloatField,
    IntegerField,
)
ALL_UNARY_LOOKUPS = UNARY_LOOKUPS | EXPLICIT_NULL_LOOKUPS


def _field_instance(value):
    if isinstance(value, Field):
        return value
    if isinstance(value, type) and issubclass(value, Field):
        return value()
    return None


def _form_field_instance(value):
    if isinstance(value, forms.Field):
        return value
    if isinstance(value, type) and issubclass(value, forms.Field):
        return value()
    return None


class JSONPath:
    def __init__(
        self,
        path,
        form_field=None,
        lookups=DEFAULT_LOOKUPS,
        *,
        output_field=None,
        db_type=None,
        db_field=None,
        field_class=None,
        form_class=None,
        lookup_expr=None,
        lookup_choices=None,
    ):
        if not isinstance(path, (list, tuple)):
            raise TypeError("JSON path must be a list or tuple of keys and indices.")
        if not path:
            raise ValueError("JSON path must contain at least one key or index.")
        normalized_path = tuple(path)
        for segment in normalized_path:
            if isinstance(segment, bool) or not isinstance(segment, (str, int)):
                raise TypeError("JSON path segments must be strings or integers.")

        form_field = form_field if field_class is None else field_class
        if form_class is not None:
            form_field = form_class
        form_field = _form_field_instance(form_field) or forms.CharField()
        output_field = output_field if db_type is None else db_type
        if db_field is not None:
            output_field = db_field
        output_field = _field_instance(output_field)

        if lookup_choices is not None:
            lookups = lookup_choices
        if lookup_expr is not None:
            lookups = [lookup_expr]
        normalized_lookups = frozenset(
            lookup[0] if isinstance(lookup, (list, tuple)) else lookup
            for lookup in lookups
        )
        invalid_lookups = normalized_lookups - DEFAULT_LOOKUPS
        if invalid_lookups:
            raise ValueError(
                "Unsupported JSON path lookup: %s"
                % ", ".join(sorted(invalid_lookups))
            )
        if not normalized_lookups:
            raise ValueError("At least one JSON path lookup is required.")

        object.__setattr__(self, "path", normalized_path)
        object.__setattr__(self, "form_field", form_field)
        object.__setattr__(self, "lookups", normalized_lookups)
        object.__setattr__(self, "output_field", output_field)


class JSONPathLookup:
    def __init__(self, value, lookup_expr, path):
        self.value = value
        self.lookup_expr = lookup_expr
        self.path = path


class JSONPathWidget(SuffixedMultiWidget):
    suffixes = [None, "path", "lookup"]

    def __init__(self, attrs=None):
        super().__init__(
            (forms.TextInput, forms.TextInput, forms.Select),
            attrs,
        )

    def decompress(self, value):
        if value is None:
            return [None, None, None]
        return value


class JSONPathChoiceField(forms.ChoiceField):
    default_error_messages = {
        "invalid_choice": _("Select a valid JSON path."),
    }


class JSONLookupChoiceField(forms.ChoiceField):
    default_error_messages = {
        "invalid_choice": _("Select a valid JSON lookup."),
    }


class JSONPathField(forms.Field):
    widget = JSONPathWidget

    def __init__(self, specs, *, single=False, **kwargs):
        self.specs = specs
        self.single = single
        super().__init__(**kwargs)
        self.widget.widgets[1] = forms.HiddenInput() if single else forms.Select()
        self._configure_widget_choices()

    def _configure_widget_choices(self):
        path_choices = [
            (json.dumps(spec.path, separators=(",", ":")), self.path_label(spec))
            for spec in self.specs.values()
        ]
        if not self.single:
            self.widget.widgets[1].choices = [("", "---------"), *path_choices]

    @staticmethod
    def path_label(spec):
        return ".".join(repr(segment) for segment in spec.path)

    def clean(self, value):
        if not value or isinstance(value, (list, tuple)):
            if not value or not [
                item for item in value if item not in self.empty_values
            ]:
                if self.required:
                    raise ValidationError(
                        self.error_messages["required"], code="required"
                    )
                return None
        else:
            raise ValidationError(self.error_messages["invalid"], code="invalid")

        value = list(value)
        while len(value) < 3:
            value.append(None)
        raw_value, raw_path, raw_lookup = value[:3]

        if self.single:
            if raw_path in (None, ""):
                raw_path = next(iter(self.specs))
            else:
                raw_path = self.normalize_path_value(raw_path)
        else:
            raw_path = self.normalize_path_value(raw_path)
        path_field = JSONPathChoiceField(
            choices=[
                (json.dumps(spec.path, separators=(",", ":")), "")
                for spec in self.specs.values()
            ],
            required=False,
        )
        path_key = path_field.clean(raw_path)
        spec = self.specs[path_key]

        lookup_field = JSONLookupChoiceField(
            choices=[(lookup, lookup) for lookup in sorted(spec.lookups)],
            required=False,
        )
        lookup = lookup_field.clean(raw_lookup)

        if lookup in ALL_UNARY_LOOKUPS:
            value_field = forms.NullBooleanField(required=False)
            cleaned_value = value_field.clean(raw_value)
            if cleaned_value is None:
                cleaned_value = True
        else:
            if raw_value in self.empty_values:
                raise ValidationError(
                    self.error_messages["incomplete"], code="incomplete"
                )
            cleaned_value = spec.form_field.clean(raw_value)

        return JSONPathLookup(cleaned_value, lookup, spec.path)

    @staticmethod
    def normalize_path_value(value):
        try:
            path = json.loads(value)
        except (TypeError, ValueError):
            return value
        if not isinstance(path, list):
            return value
        if any(
            isinstance(segment, bool) or not isinstance(segment, (str, int))
            for segment in path
        ):
            return value
        return json.dumps(path, separators=(",", ":"))


class JSONPathFunc(Func):
    def __init__(self, field_name, path, output_field, *, mode):
        super().__init__(F(field_name), output_field=output_field)
        self.json_path = tuple(path)
        self.mode = mode

    def compile_parts(self, compiler, connection):
        check_json_support(connection, self.json_path)
        column_sql, column_params = compiler.compile(self.source_expressions[0])
        path_sql_parts = []
        path_params = []

        if connection.vendor == "postgresql":
            for segment in self.json_path:
                path_sql, params = compiler.compile(Value(str(segment), CharField()))
                path_sql_parts.append(path_sql)
                path_params.extend(params)
        else:
            json_path = compile_json_path_sqlite_or_mysql(connection, self.json_path)
            path_sql, params = compiler.compile(Value(json_path, CharField()))
            path_sql_parts.append(path_sql)
            path_params.extend(params)

        return column_sql, path_sql_parts, [*column_params, *path_params]

    def as_sqlite(self, compiler, connection):
        column, paths, params = self.compile_parts(compiler, connection)
        path = paths[0]
        if self.mode == "type":
            return "JSON_TYPE(%s, %s)" % (column, path), params
        if self.mode == "text":
            return "JSON_EXTRACT(%s, %s)" % (column, path), params
        return "JSON_EXTRACT(%s, %s)" % (column, path), params

    def as_mysql(self, compiler, connection):
        column, paths, params = self.compile_parts(compiler, connection)
        path = paths[0]
        extracted = "JSON_EXTRACT(%s, %s)" % (column, path)
        if self.mode == "type":
            return "JSON_TYPE(%s)" % extracted, params
        if self.mode == "text":
            return "JSON_UNQUOTE(%s)" % extracted, params
        return extracted, params

    def as_postgresql(self, compiler, connection):
        column, paths, params = self.compile_parts(compiler, connection)
        arguments = ", ".join([column, *paths])
        if self.mode == "type":
            return "jsonb_typeof(jsonb_extract_path(%s))" % arguments, params
        if self.mode == "text":
            return "jsonb_extract_path_text(%s)" % arguments, params
        if isinstance(self.output_field, BooleanField):
            return "CAST(jsonb_extract_path_text(%s) AS BOOLEAN)" % arguments, params
        return "jsonb_extract_path_text(%s)" % arguments, params


def check_json_support(connection, path):
    if not connection.features.supports_json_field:
        raise NotSupportedError(
            "%s does not support JSONFields." % connection.display_name
        )
    if connection.vendor not in {"sqlite", "postgresql", "mysql"}:
        raise NotSupportedError(
            "JSONPathFilter is not supported on the %s database backend."
            % connection.vendor
        )
    if any(isinstance(segment, int) and segment < 0 for segment in path):
        if not connection.features.supports_json_negative_indexing:
            raise NotSupportedError(
                "Negative JSON array indices are not supported on this database backend."
            )


def compile_json_path_sqlite_or_mysql(connection, path):
    check_json_support(connection, path)
    sql_path = "$"
    for segment in path:
        if isinstance(segment, int):
            if segment < 0 and connection.vendor == "sqlite":
                sql_path += "[#%s]" % segment
            else:
                sql_path += "[%s]" % segment
        else:
            sql_path += "." + json.dumps(segment)
    return sql_path


def json_type_names(connection, output_field):
    internal_type = output_field.get_internal_type()
    if isinstance(output_field, BooleanField):
        return {"sqlite": ("true",), "mysql": ("BOOLEAN",), "postgresql": ("boolean",)}
    if isinstance(output_field, NUMERIC_DB_FIELDS):
        if internal_type == "IntegerField":
            return {
                "sqlite": ("integer",),
                "mysql": ("INTEGER",),
                "postgresql": ("number",),
            }
        return {
            "sqlite": ("integer", "real"),
            "mysql": ("INTEGER", "DOUBLE", "DECIMAL"),
            "postgresql": ("number",),
        }
    return {
        "sqlite": ("text",),
        "mysql": ("STRING",),
        "postgresql": ("string",),
    }


def build_json_path_q(connection, field_name, spec, lookup, value):
    check_json_support(connection, spec.path)
    suffix = hashlib.sha1(
        ("%s:%s:%s" % (field_name, repr(spec.path), lookup)).encode()
    ).hexdigest()[:12]
    type_alias = "json_type_%s" % suffix
    value_alias = "json_value_%s" % suffix
    expressions = {}

    if lookup == "has_key":
        condition = build_presence_condition(
            expressions, field_name, spec, type_alias, value
        )
        return expressions, condition

    if lookup == "missing_key":
        condition = build_missing_key_condition(
            expressions, field_name, spec, type_alias, value
        )
        return expressions, condition

    if lookup == "sql_isnull":
        condition = Q(**{field_name + "__isnull": value})
        return expressions, condition

    if lookup == "json_isnull":
        condition = build_json_null_condition(
            expressions, field_name, spec, type_alias, value
        )
        return expressions, condition

    if lookup == "isnull":
        condition = build_presence_condition(
            expressions, field_name, spec, type_alias, not value
        )
        return expressions, condition

    expressions, condition = build_value_condition(
        connection,
        expressions,
        field_name,
        spec,
        lookup,
        value,
        type_alias,
        value_alias,
    )
    return expressions, condition


def build_type_expression(expressions, field_name, spec, type_alias):
    expressions[type_alias] = JSONPathFunc(
        field_name, spec.path, CharField(), mode="type"
    )


def build_presence_condition(expressions, field_name, spec, type_alias, present):
    build_type_expression(expressions, field_name, spec, type_alias)
    return Q(**{"%s__isnull" % type_alias: not present})


def build_missing_key_condition(expressions, field_name, spec, type_alias, missing):
    build_type_expression(expressions, field_name, spec, type_alias)
    return Q(
        **{
            "%s__isnull" % type_alias: missing,
            field_name + "__isnull": not missing,
        }
    )


def build_json_null_condition(expressions, field_name, spec, type_alias, is_null):
    build_type_expression(expressions, field_name, spec, type_alias)
    if not is_null:
        return Q(**{"%s__isnull" % type_alias: False}) & ~Q(
            **{"%s__exact" % type_alias: "null"}
        )
    return Q(**{"%s__exact" % type_alias: "null"})


def infer_output_field(spec):
    output_field = spec.output_field
    if output_field is not None:
        return output_field
    if isinstance(spec.form_field, (forms.DecimalField, forms.FloatField)):
        return FloatField()
    if isinstance(spec.form_field, forms.IntegerField):
        return IntegerField()
    if isinstance(spec.form_field, (forms.BooleanField, forms.NullBooleanField)):
        return BooleanField()
    return CharField()


def build_value_condition(
    connection,
    expressions,
    field_name,
    spec,
    lookup,
    value,
    type_alias,
    value_alias,
):
    output_field = infer_output_field(spec)
    build_type_expression(expressions, field_name, spec, type_alias)

    text_mode = not isinstance(output_field, (*NUMERIC_DB_FIELDS, BooleanField))
    extract = JSONPathFunc(
        field_name,
        spec.path,
        TextField() if text_mode else output_field,
        mode="text" if text_mode else "raw",
    )
    if isinstance(output_field, NUMERIC_DB_FIELDS):
        extract = Cast(extract, output_field=output_field)
    expressions[value_alias] = extract

    if lookup == "exact" and value is None:
        return expressions, Q(**{"%s__exact" % type_alias: "null"})

    type_names = json_type_names(connection, output_field)[connection.vendor]
    condition = Q(
        **{
            "%s__in" % type_alias: type_names,
            "%s__%s" % (value_alias, lookup): value,
        }
    )
    return expressions, condition
