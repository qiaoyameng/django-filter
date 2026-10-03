import json
from unittest import mock

from django import forms
from django.db import NotSupportedError, connection
from django.test import TestCase

from django_filters import FilterSet, JSONPath, JSONPathFilter
from django_filters.json import build_json_path_q
from tests.models import JSONRecord, User


class JSONRecordFilterSet(FilterSet):
    data = JSONPathFilter(
        field_name="data",
        paths=[
            JSONPath(
                ("profile", "age"),
                forms.IntegerField(),
                [
                    "exact", "gt", "lt", "has_key", "isnull", "missing_key",
                    "json_isnull", "sql_isnull",
                ],
            ),
            JSONPath(
                ("profile", "score"),
                forms.FloatField(),
                ["exact", "gte", "lte"],
            ),
            JSONPath(
                ("active",),
                forms.BooleanField(),
                ["exact"],
            ),
            JSONPath(
                ("a__b.c\"",),
                forms.CharField(),
                ["exact", "contains"],
            ),
            JSONPath(
                ("items", 0, "amount"),
                forms.IntegerField(),
                ["gt"],
            ),
            JSONPath(
                ("items", "0", "amount"),
                forms.IntegerField(),
                ["gt"],
            ),
        ],
    )

    class Meta:
        model = JSONRecord
        fields = []


class JSONPathFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.integer = JSONRecord.objects.create(
            data={"profile": {"age": 10, "score": 1.25}, "active": True,
                  "a__b.c\"": "x", "items": [{"amount": 10}]}
        )
        cls.string_number = JSONRecord.objects.create(
            data={"profile": {"age": "10", "score": "1.25"}, "active": "true",
                  "a__b.c\"": "10 OR 1=1", "items": [{}, {"amount": 10}]}
        )
        cls.json_null = JSONRecord.objects.create(
            data={"profile": {"age": None}, "active": None, "items": []}
        )
        cls.missing_key = JSONRecord.objects.create(data={"profile": {}})
        cls.sql_null = JSONRecord.objects.create(data=None)

    def ids(self, filterset):
        return list(filterset.qs.values_list("id", flat=True))

    def test_single_path_does_not_require_path_parameter(self):
        class SinglePathFilterSet(FilterSet):
            age = JSONPathFilter(
                field_name="data",
                path=["profile", "age"],
                form_field=forms.IntegerField(),
                lookups=["gt"],
            )

            class Meta:
                model = JSONRecord
                fields = []

        filterset = SinglePathFilterSet({"age": "5", "age_lookup": "gt"})
        self.assertTrue(filterset.is_valid(), filterset.errors)
        self.assertEqual(self.ids(filterset), [self.integer.pk])

    def test_single_path_rejects_tampered_path_parameter(self):
        class SinglePathFilterSet(FilterSet):
            age = JSONPathFilter(
                field_name="data",
                path=["profile", "age"],
                form_field=forms.IntegerField(),
                lookups=["gt"],
            )

            class Meta:
                model = JSONRecord
                fields = []

        filterset = SinglePathFilterSet({
            "age": "5",
            "age_path": json.dumps(["profile", "score"]),
            "age_lookup": "gt",
        })
        self.assertFalse(filterset.is_valid())

    def test_numeric_comparison_excludes_string_numbers(self):
        filterset = JSONRecordFilterSet(
            {"data": "5", "data_path": json.dumps(["profile", "age"]),
             "data_lookup": "gt"}
        )
        self.assertTrue(filterset.is_valid(), filterset.errors)
        self.assertEqual(self.ids(filterset), [self.integer.pk])

    def test_numeric_exact_excludes_string_numbers(self):
        filterset = JSONRecordFilterSet(
            {"data": "10", "data_path": json.dumps(["profile", "age"]),
             "data_lookup": "exact"}
        )
        self.assertTrue(filterset.is_valid(), filterset.errors)
        self.assertEqual(self.ids(filterset), [self.integer.pk])

    def test_float_comparison_excludes_string_numbers(self):
        filterset = JSONRecordFilterSet(
            {"data": "1.2", "data_path": json.dumps(["profile", "score"]),
             "data_lookup": "gte"}
        )
        self.assertTrue(filterset.is_valid(), filterset.errors)
        self.assertEqual(self.ids(filterset), [self.integer.pk])

    def test_string_exact_matches_special_key(self):
        filterset = JSONRecordFilterSet(
            {"data": "10 OR 1=1",
             "data_path": json.dumps(["a__b.c\""]),
             "data_lookup": "exact"}
        )
        self.assertTrue(filterset.is_valid(), filterset.errors)
        self.assertEqual(self.ids(filterset), [self.string_number.pk])

    def test_boolean_exact_distinguishes_boolean_from_string(self):
        filterset = JSONRecordFilterSet(
            {"data": "true", "data_path": json.dumps(["active"]),
             "data_lookup": "exact"}
        )
        self.assertTrue(filterset.is_valid(), filterset.errors)
        self.assertEqual(self.ids(filterset), [self.integer.pk])

    def test_array_index_distinguished_from_string_key(self):
        by_index = JSONRecordFilterSet(
            {"data": "5", "data_path": json.dumps(["items", 0, "amount"]),
             "data_lookup": "gt"}
        )
        by_key = JSONRecordFilterSet(
            {"data": "5", "data_path": json.dumps(["items", "0", "amount"]),
             "data_lookup": "gt"}
        )
        self.assertTrue(by_index.is_valid(), by_index.errors)
        self.assertTrue(by_key.is_valid(), by_key.errors)
        self.assertEqual(self.ids(by_index), [self.integer.pk])
        self.assertEqual(self.ids(by_key), [])

    def test_missing_json_null_and_sql_null_are_distinct(self):
        cases = {
            "has_key": [self.integer.pk, self.string_number.pk, self.json_null.pk],
            "isnull": [self.missing_key.pk, self.sql_null.pk],
            "missing_key": [self.missing_key.pk],
            "json_isnull": [self.json_null.pk],
            "sql_isnull": [self.sql_null.pk],
        }
        for lookup, expected in cases.items():
            with self.subTest(lookup=lookup):
                filterset = JSONRecordFilterSet(
                    {"data": "true",
                     "data_path": json.dumps(["profile", "age"]),
                     "data_lookup": lookup}
                )
                self.assertTrue(filterset.is_valid(), filterset.errors)
                self.assertEqual(self.ids(filterset), expected)

    def test_unknown_path_rejected_by_form(self):
        filterset = JSONRecordFilterSet(
            {"data": "10", "data_path": json.dumps(["profile", "unknown"]),
             "data_lookup": "exact"}
        )
        self.assertFalse(filterset.is_valid())
        self.assertIn("data", filterset.errors)

    def test_non_list_json_path_rejected_by_form(self):
        filterset = JSONRecordFilterSet(
            {"data": "10", "data_path": json.dumps({"profile": "age"}),
             "data_lookup": "exact"}
        )
        self.assertFalse(filterset.is_valid())

    def test_invalid_path_segment_rejected_at_declaration(self):
        with self.assertRaises(TypeError):
            JSONPath(["profile", 1.5], forms.IntegerField(), ["exact"])

    def test_non_json_model_field_raises_explicitly(self):
        class UserFilterSet(FilterSet):
            username = JSONPathFilter(
                field_name="username",
                path=["display"],
                form_field=forms.CharField(),
                lookups=["exact"],
            )

            class Meta:
                model = User
                fields = []

        filterset = UserFilterSet(
            {"username": "alex", "username_lookup": "exact"}
        )
        self.assertTrue(filterset.is_valid())
        with self.assertRaises(ValueError):
            list(filterset.qs)

    def test_unknown_lookup_rejected_by_form(self):
        filterset = JSONRecordFilterSet(
            {"data": "10", "data_path": json.dumps(["profile", "age"]),
             "data_lookup": "regex"}
        )
        self.assertFalse(filterset.is_valid())
        self.assertIn("data", filterset.errors)

    def test_lookup_separator_in_input_is_bound(self):
        filterset = JSONRecordFilterSet(
            {"data": "10__exact", "data_path": json.dumps(["a__b.c\""]),
             "data_lookup": "exact"}
        )
        self.assertTrue(filterset.is_valid(), filterset.errors)
        self.assertEqual(self.ids(filterset), [])
        sql, params = filterset.qs.query.sql_with_params()
        self.assertNotIn("10__exact", sql)
        self.assertIn("10__exact", params)

    def test_injected_lookup_in_value_does_not_change_sql(self):
        filterset = JSONRecordFilterSet(
            {"data": "10 OR 1=1", "data_path": json.dumps(["a__b.c\""]),
             "data_lookup": "exact"}
        )
        self.assertTrue(filterset.is_valid(), filterset.errors)
        self.assertEqual(self.ids(filterset), [self.string_number.pk])
        sql, params = filterset.qs.query.sql_with_params()
        self.assertNotIn("10 OR 1=1", sql)
        self.assertIn("10 OR 1=1", params)

    def test_generated_sql_binds_paths_and_values(self):
        filterset = JSONRecordFilterSet(
            {"data": "5", "data_path": json.dumps(["profile", "age"]),
             "data_lookup": "gt"}
        )
        self.assertTrue(filterset.is_valid(), filterset.errors)
        sql, params = filterset.qs.query.sql_with_params()
        self.assertNotIn('$."profile"."age"', sql)
        self.assertIn('$."profile"."age"', params)
        self.assertIn(5, params)

    def test_executed_query_uses_bound_parameters(self):
        filterset = JSONRecordFilterSet(
            {"data": "5", "data_path": json.dumps(["profile", "age"]),
             "data_lookup": "gt"}
        )
        self.assertTrue(filterset.is_valid(), filterset.errors)
        executed = []

        def capture(execute, sql, params, many, context):
            executed.append((sql, params))
            return execute(sql, params, many, context)

        with connection.execute_wrapper(capture):
            list(filterset.qs)
        sql, params = executed[0]
        self.assertNotIn('$."profile"."age"', sql)
        self.assertIn('$."profile"."age"', params)
        self.assertNotIn(" OR 1=1", sql)
        self.assertIn(5, params)

    def test_unsupported_backend_raises_explicitly(self):
        fake_connection = mock.Mock(
            vendor="oracle",
            display_name="Oracle",
            features=mock.Mock(
                supports_json_field=True,
                supports_json_negative_indexing=False,
            ),
        )
        with self.assertRaises(NotSupportedError):
            build_json_path_q(
                fake_connection,
                "data",
                JSONPath(["age"], forms.IntegerField(), ["gt"]),
                "gt",
                5,
            )

    def test_unsupported_negative_index_raises_explicitly(self):
        fake_connection = mock.Mock(
            vendor="mysql",
            display_name="MySQL",
            features=mock.Mock(
                supports_json_field=True,
                supports_json_negative_indexing=False,
            ),
        )
        with self.assertRaises(NotSupportedError):
            build_json_path_q(
                fake_connection,
                "data",
                JSONPath([-1], forms.IntegerField(), ["gt"]),
                "gt",
                5,
            )

    def test_declaration_rejects_unsupported_lookup(self):
        with self.assertRaises(ValueError):
            JSONPath(("age",), forms.IntegerField(), ["regex"])
