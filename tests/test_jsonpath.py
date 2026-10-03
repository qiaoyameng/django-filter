from unittest import mock

from django import forms
from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.db.utils import NotSupportedError
from django.test import TestCase

from django_filters import FilterSet
from django_filters.fields import JSONPathField, JSONPathLookup
from django_filters.filters import JSONPath, JSONPathFilter

from .models import JSONRecord


def make_filterset(**filter_kwargs):
    class JSONRecordFilter(FilterSet):
        data = JSONPathFilter(
            field_name="data",
            paths={
                "price": JSONPath(
                    ["price"],
                    field_class=forms.IntegerField,
                    lookups=["exact", "gt", "gte", "lt", "lte"],
                ),
                "price_float": JSONPath(
                    ["price"],
                    field_class=forms.FloatField,
                    lookups=["exact", "gte", "lte"],
                ),
                "price_text": JSONPath(
                    ["price"],
                    field_class=forms.CharField,
                    lookups=["exact", "icontains"],
                ),
                "name": JSONPath(
                    ["name"],
                    field_class=forms.CharField,
                    lookups=["exact", "icontains"],
                ),
                "active": JSONPath(
                    ["active"],
                    field_class=forms.BooleanField,
                    lookups=["exact"],
                ),
                "dunder": JSONPath(
                    ["a__b"], field_class=forms.IntegerField, lookups=["exact"]
                ),
                "dotted": JSONPath(
                    ["a.b"], field_class=forms.IntegerField, lookups=["exact"]
                ),
                "quoted": JSONPath(
                    ["he said 'hi'"],
                    field_class=forms.IntegerField,
                    lookups=["exact"],
                ),
                "deep": JSONPath(
                    ["nested", "x__y.z'w", 1],
                    field_class=forms.IntegerField,
                    lookups=["exact", "gte"],
                ),
            },
            **filter_kwargs,
        )

        class Meta:
            model = JSONRecord
            fields = []

    return JSONRecordFilter


class JSONPathFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        JSONRecord.objects.create(
            name="int",
            data={
                "price": 10,
                "name": "widget",
                "active": True,
                "a__b": 5,
                "a.b": 6,
                "he said 'hi'": 7,
                "nested": {"x__y.z'w": [10, 20]},
            },
        )
        JSONRecord.objects.create(
            name="float", data={"price": 10.5, "active": False}
        )
        JSONRecord.objects.create(
            name="str", data={"price": "10", "name": "10"}
        )
        JSONRecord.objects.create(
            name="json_null", data={"price": None, "name": None}
        )
        JSONRecord.objects.create(name="missing", data={})
        JSONRecord.objects.create(name="sql_null", data=None)

    def filter_names(self, filterset_class=None, **params):
        filterset_class = filterset_class or make_filterset()
        f = filterset_class(params, queryset=JSONRecord.objects.all())
        self.assertTrue(f.is_valid(), f.errors)
        return set(f.qs.values_list("name", flat=True))

    def query_params(self, path, lookup, value):
        return {
            "data_path": path,
            "data_lookup": lookup,
            "data_value": value,
        }

    def test_exact_integer(self):
        params = self.query_params("price", "exact", "10")
        self.assertEqual(self.filter_names(**params), {"int"})

    def test_integer_comparisons(self):
        self.assertEqual(
            self.filter_names(**self.query_params("price", "gte", "10")), {"int"}
        )
        self.assertEqual(
            self.filter_names(**self.query_params("price", "gt", "10")), set()
        )
        self.assertEqual(
            self.filter_names(**self.query_params("price", "lt", "11")), {"int"}
        )
        self.assertEqual(
            self.filter_names(**self.query_params("price", "lte", "9")), set()
        )

    def test_float_path_matches_integer_and_real(self):
        self.assertEqual(
            self.filter_names(**self.query_params("price_float", "gte", "10")),
            {"int", "float"},
        )
        self.assertEqual(
            self.filter_names(**self.query_params("price_float", "exact", "10.5")),
            {"float"},
        )
        self.assertEqual(
            self.filter_names(**self.query_params("price_float", "exact", "10")),
            {"int"},
        )

    def test_string_numbers_are_not_numeric(self):
        # The JSON string "10" never matches numeric comparisons, and the
        # JSON number 10 never matches text comparisons.
        self.assertEqual(
            self.filter_names(**self.query_params("price_text", "exact", "10")),
            {"str"},
        )
        self.assertEqual(
            self.filter_names(**self.query_params("price_text", "icontains", "1")),
            {"str"},
        )

    def test_text_lookups(self):
        self.assertEqual(
            self.filter_names(**self.query_params("name", "exact", "widget")),
            {"int"},
        )
        self.assertEqual(
            self.filter_names(**self.query_params("name", "icontains", "IDG")),
            {"int"},
        )

    def test_boolean_exact(self):
        self.assertEqual(
            self.filter_names(**self.query_params("active", "exact", "true")),
            {"int"},
        )
        self.assertEqual(
            self.filter_names(**self.query_params("active", "exact", "false")),
            {"float"},
        )

    def test_json_null_never_matches(self):
        # JSON null, missing keys and SQL NULL are excluded from all comparisons.
        for params in [
            self.query_params("price", "exact", "10"),
            self.query_params("price_float", "gte", "0"),
            self.query_params("price_text", "icontains", "0"),
            self.query_params("active", "exact", "true"),
        ]:
            names = self.filter_names(**params)
            for excluded in ("json_null", "missing", "sql_null"):
                self.assertNotIn(excluded, names)

    def test_special_char_keys(self):
        self.assertEqual(
            self.filter_names(**self.query_params("dunder", "exact", "5")),
            {"int"},
        )
        self.assertEqual(
            self.filter_names(**self.query_params("dotted", "exact", "6")),
            {"int"},
        )
        self.assertEqual(
            self.filter_names(**self.query_params("quoted", "exact", "7")),
            {"int"},
        )

    def test_deep_path_with_array_index(self):
        self.assertEqual(
            self.filter_names(**self.query_params("deep", "exact", "20")),
            {"int"},
        )
        self.assertEqual(
            self.filter_names(**self.query_params("deep", "exact", "10")),
            set(),
        )
        self.assertEqual(
            self.filter_names(**self.query_params("deep", "gte", "15")),
            {"int"},
        )


class JSONPathFormValidationTests(TestCase):
    def setUp(self):
        JSONRecord.objects.create(name="row", data={"price": 10})
        self.filterset_class = make_filterset()

    def is_valid(self, **params):
        f = self.filterset_class(params, queryset=JSONRecord.objects.all())
        return f.is_valid()

    def test_unknown_path_rejected(self):
        self.assertFalse(
            self.is_valid(
                data_path="nope", data_lookup="exact", data_value="10"
            )
        )

    def test_unknown_lookup_rejected(self):
        self.assertFalse(
            self.is_valid(
                data_path="price", data_lookup="regex", data_value="10"
            )
        )

    def test_lookup_not_allowed_for_path_rejected(self):
        # "icontains" is a valid lookup for other paths, but not "price".
        self.assertFalse(
            self.is_valid(
                data_path="price", data_lookup="icontains", data_value="10"
            )
        )

    def test_invalid_value_rejected(self):
        self.assertFalse(
            self.is_valid(
                data_path="price", data_lookup="exact", data_value="abc"
            )
        )

    def test_incomplete_input_rejected(self):
        self.assertFalse(self.is_valid(data_path="price"))
        self.assertFalse(self.is_valid(data_path="price", data_lookup="exact"))
        self.assertFalse(self.is_valid(data_value="10"))

    def test_empty_input_is_unfiltered(self):
        f = self.filterset_class({}, queryset=JSONRecord.objects.all())
        self.assertTrue(f.is_valid())
        self.assertEqual(f.qs.count(), 1)

    def test_widget_suffixes(self):
        f = self.filterset_class(queryset=JSONRecord.objects.all())
        widget = f.form.fields["data"].widget
        self.assertEqual(widget.suffixes, ["path", "lookup", "value"])


class JSONPathDeclarationTests(TestCase):
    def test_invalid_paths(self):
        with self.assertRaises(ImproperlyConfigured):
            JSONPath([])
        with self.assertRaises(ImproperlyConfigured):
            JSONPath(["a", -1])
        with self.assertRaises(ImproperlyConfigured):
            JSONPath(["a", True])
        with self.assertRaises(ImproperlyConfigured):
            JSONPath(["a", 1.5])
        with self.assertRaises(ImproperlyConfigured):
            JSONPath(["a", ""])

    def test_unsupported_field_class(self):
        with self.assertRaises(ImproperlyConfigured):
            JSONPath(["a"], field_class=forms.DateField)

    def test_unsupported_lookup(self):
        with self.assertRaises(ImproperlyConfigured):
            JSONPath(["a"], lookups=["regex"])
        with self.assertRaises(ImproperlyConfigured):
            JSONPath(["a"], lookups=[])
        with self.assertRaises(ImproperlyConfigured):
            JSONPath(["a"], lookups=["exact__lt"])

    def test_invalid_paths_dict(self):
        with self.assertRaises(ImproperlyConfigured):
            JSONPathFilter(paths={})
        with self.assertRaises(ImproperlyConfigured):
            JSONPathFilter(paths={"x": "not a jsonpath"})

    def test_non_json_field_rejected(self):
        class BadFilter(FilterSet):
            name = JSONPathFilter(
                field_name="name", paths={"x": JSONPath(["x"])}
            )

            class Meta:
                model = JSONRecord
                fields = []

        f = BadFilter(
            {"name_path": "x", "name_lookup": "exact", "name_value": "a"},
            queryset=JSONRecord.objects.all(),
        )
        self.assertTrue(f.is_valid())
        with self.assertRaises(ImproperlyConfigured):
            f.qs


class JSONPathSQLTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        JSONRecord.objects.create(name="row", data={"price": 10})

    def bound_query(self, **params):
        f = make_filterset()(params, queryset=JSONRecord.objects.all())
        self.assertTrue(f.is_valid(), f.errors)
        return f.qs.query.sql_with_params()

    def test_value_is_bound_parameter(self):
        sql, params = self.bound_query(
            data_path="price", data_lookup="gte", data_value="987654"
        )
        self.assertNotIn("987654", sql)
        self.assertIn(987654, params)

    def test_text_value_is_bound_parameter(self):
        value = "zyx'-\"; DROP TABLE tests_jsonrecord; --"
        sql, params = self.bound_query(
            data_path="name", data_lookup="exact", data_value=value
        )
        self.assertNotIn(value, sql)
        self.assertIn(value, params)

    def test_json_path_is_bound_parameter(self):
        sql, params = self.bound_query(
            data_path="price", data_lookup="exact", data_value="10"
        )
        self.assertNotIn('$."price"', sql)
        self.assertIn('$."price"', params)

    def test_unsupported_backend_rejected(self):
        f = make_filterset()(
            {"data_path": "price", "data_lookup": "exact", "data_value": "10"},
            queryset=JSONRecord.objects.all(),
        )
        self.assertTrue(f.is_valid())
        with mock.patch.object(connections["default"], "vendor", "postgresql"):
            with self.assertRaises(NotSupportedError):
                list(f.qs)

    def test_double_quote_keys_rejected_on_sqlite(self):
        filterset_class = make_filterset()
        f = filterset_class(
            {"data_path": "quoted", "data_lookup": "exact", "data_value": "7"},
            queryset=JSONRecord.objects.all(),
        )
        # The "quoted" path here is ["he said 'hi'"], which is fine; declare a
        # path with a double quote in the key to check the explicit error.
        f.qs  # noqa: B018 - force evaluation of the valid case first
        bad_class = type(
            "BadFilter",
            (FilterSet,),
            {
                "data": JSONPathFilter(
                    field_name="data",
                    paths={"bad": JSONPath(['say "hi"'], lookups=["exact"])},
                ),
                "Meta": type(
                    "Meta", (), {"model": JSONRecord, "fields": []}
                ),
            },
        )
        bad = bad_class(
            {"data_path": "bad", "data_lookup": "exact", "data_value": "1"},
            queryset=JSONRecord.objects.all(),
        )
        self.assertTrue(bad.is_valid())
        with self.assertRaises(NotSupportedError):
            list(bad.qs)
