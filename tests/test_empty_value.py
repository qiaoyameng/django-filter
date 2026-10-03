from unittest import mock

from django import forms
from django.test import TestCase

from django_filters import FilterSet
from django_filters.constants import (
    EMPTY_VALUE_OPERATORS,
    EQUALS,
    IS_EMPTY,
    IS_NOT_EMPTY,
    IS_NOT_NULL,
    IS_NULL,
    NOT_EQUALS,
)
from django_filters.fields import EmptyValue, EmptyValueField
from django_filters.filters import CharFilter, EmptyValueFilter
from django_filters.widgets import EmptyValueWidget
from tests.models import NullableText


class EmptyValueFieldTests(TestCase):
    def clean(self, value, operator=None):
        field = EmptyValueFilter(field_name="label").field
        if value is None and operator is None:
            raw = [None, None]
        elif operator is None:
            raw = [value, None]
        else:
            raw = [value, operator]
        return field.clean(raw)

    def test_field_structure(self):
        f = EmptyValueFilter(field_name="label")
        field = f.field
        self.assertIsInstance(field, EmptyValueField)
        self.assertIsInstance(field.widget, EmptyValueWidget)
        self.assertEqual(field.widget.suffixes, [None, "operator"])
        self.assertIsInstance(field.fields[0], forms.CharField)
        self.assertIsInstance(field.fields[1], forms.ChoiceField)

    def test_operator_choices(self):
        f = EmptyValueFilter(field_name="label")
        values = [value for value, label in f.field.fields[1].choices]
        self.assertEqual(
            values, ["", *EMPTY_VALUE_OPERATORS],
        )

    def test_restricted_operator_choices(self):
        f = EmptyValueFilter(field_name="label", operators=(EQUALS, IS_NULL))
        values = [value for value, label in f.field.fields[1].choices]
        self.assertEqual(values, ["", EQUALS, IS_NULL])

    def test_invalid_operator_declaration(self):
        with self.assertRaises(ValueError):
            EmptyValueFilter(field_name="label", operators=("exact",))

    def test_clean_no_params_is_none(self):
        # MultiValueField receives [None, None] when both inputs are omitted.
        self.assertIsNone(self.clean(None))

    def test_clean_value_only_defaults_to_equals(self):
        self.assertEqual(self.clean("apple"), EmptyValue("apple", EQUALS))

    def test_clean_empty_value_only_is_none(self):
        # The historical skip-empty-input habit: a bare empty value is a noop.
        self.assertIsNone(self.clean(""))

    def test_clean_equals_empty_string(self):
        self.assertEqual(self.clean("", EQUALS), EmptyValue("", EQUALS))

    def test_clean_equals_literal_null(self):
        self.assertEqual(self.clean("null", EQUALS), EmptyValue("null", EQUALS))

    def test_clean_valueless_operators_drop_value(self):
        for operator in (IS_NULL, IS_NOT_NULL, IS_EMPTY, IS_NOT_EMPTY):
            with self.subTest(operator=operator):
                self.assertEqual(
                    self.clean("anything", operator),
                    EmptyValue(None, operator),
                )

    def test_clean_equals_without_value_is_incomplete(self):
        with self.assertRaises(forms.ValidationError):
            self.clean(None, EQUALS)

    def test_form_rejects_unsupported_operator(self):
        class F(FilterSet):
            label = EmptyValueFilter(
                field_name="label", operators=(EQUALS, IS_NULL)
            )

            class Meta:
                model = NullableText
                fields = []

        fs = F(data={"label_operator": IS_EMPTY}, queryset=NullableText.objects.all())
        self.assertFalse(fs.is_valid())
        self.assertIn("label", fs.errors)

    def test_form_rejects_equals_without_value(self):
        class F(FilterSet):
            label = EmptyValueFilter(field_name="label")

            class Meta:
                model = NullableText
                fields = []

        fs = F(
            data={"label_operator": EQUALS}, queryset=NullableText.objects.all()
        )
        self.assertFalse(fs.is_valid())
        self.assertIn("label", fs.errors)

    def test_form_valueless_operator_without_value_is_valid(self):
        class F(FilterSet):
            label = EmptyValueFilter(field_name="label")

            class Meta:
                model = NullableText
                fields = []

        fs = F(
            data={"label_operator": IS_NULL}, queryset=NullableText.objects.all()
        )
        self.assertTrue(fs.is_valid(), fs.errors)
        self.assertEqual(
            fs.form.cleaned_data["label"], EmptyValue(None, IS_NULL)
        )

    def test_form_rejects_unknown_operator(self):
        class F(FilterSet):
            label = EmptyValueFilter(field_name="label")

            class Meta:
                model = NullableText
                fields = []

        fs = F(data={"label_operator": "exact"}, queryset=NullableText.objects.all())
        self.assertFalse(fs.is_valid())
        self.assertIn("label", fs.errors)

    def test_widget_param_names(self):
        class F(FilterSet):
            label = EmptyValueFilter(field_name="label")

            class Meta:
                model = NullableText
                fields = []

        html = str(F().form["label"])
        self.assertIn('name="label"', html)
        self.assertIn('name="label_operator"', html)


class EmptyValueFilterPredicateTests(TestCase):
    def filter(self, value):
        return EmptyValueFilter(field_name="label")

    def test_none_is_noop(self):
        qs = mock.Mock(spec=["filter"])
        result = self.filter(None).filter(qs, None)
        self.assertIs(result, qs)
        qs.filter.assert_not_called()

    def predicate(self, value):
        qs = mock.Mock(spec=["filter"])
        self.filter(None).filter(qs, value)
        qs.filter.assert_called_once()
        return qs.filter.call_args[0][0]

    def test_equals_predicate(self):
        q_object = self.predicate(EmptyValue("null", EQUALS))
        self.assertFalse(q_object.negated)
        self.assertEqual(q_object.children, [("label__exact", "null")])

    def test_not_equals_negates_rather_than_excludes(self):
        q_object = self.predicate(EmptyValue("apple", NOT_EQUALS))
        self.assertTrue(q_object.negated)
        self.assertEqual(q_object.children, [("label__exact", "apple")])

    def test_isnull_predicate(self):
        q_object = self.predicate(EmptyValue(None, IS_NULL))
        self.assertEqual(q_object.children, [("label__isnull", True)])

    def test_isnotnull_predicate(self):
        q_object = self.predicate(EmptyValue(None, IS_NOT_NULL))
        self.assertEqual(q_object.children, [("label__isnull", False)])

    def test_isempty_predicate(self):
        q_object = self.predicate(EmptyValue(None, IS_EMPTY))
        self.assertEqual(q_object.children, [("label__exact", "")])

    def test_isnotempty_predicate(self):
        q_object = self.predicate(EmptyValue(None, IS_NOT_EMPTY))
        self.assertFalse(q_object.negated)
        self.assertEqual(len(q_object.children), 2)
        self.assertIn(("label__isnull", False), q_object.children)
        negated = q_object.children[1]
        self.assertTrue(negated.negated)
        self.assertEqual(negated.children, [("label__exact", "")])

    def test_empty_string_value_is_not_short_circuited(self):
        # The generic Filter.filter short circuits on EMPTY_VALUES; the
        # empty-value filter must still issue the equals-empty comparison.
        qs = mock.Mock(spec=["filter"])
        result = self.filter(None).filter(qs, EmptyValue("", EQUALS))
        self.assertIsNot(result, qs)
        qs.filter.assert_called_once()

    def test_exclude_isnull(self):
        class F(FilterSet):
            label = EmptyValueFilter(field_name="label", exclude=True)

            class Meta:
                model = NullableText
                fields = []

        fs = F(
            data={"label_operator": IS_NULL},
            queryset=NullableText.objects.all(),
        )
        self.assertTrue(fs.is_valid())
        # exclude + isnull(True) negated => non-null rows
        NullableText.objects.create(label=None)
        non_null = NullableText.objects.create(label="x")
        self.assertEqual(list(fs.qs), [non_null])


class EmptyValueFilterQueryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.null_row = NullableText.objects.create(label=None)
        cls.empty_row = NullableText.objects.create(label="")
        cls.word_row = NullableText.objects.create(label="apple")
        cls.literal_null_row = NullableText.objects.create(label="null")
        cls.all_pks = set(NullableText.objects.values_list("pk", flat=True))

    def filterset(self, **data):
        class F(FilterSet):
            label = EmptyValueFilter(field_name="label")

            class Meta:
                model = NullableText
                fields = []

        return F(
            data=data, queryset=NullableText.objects.order_by("pk")
        )

    def result(self, **data):
        fs = self.filterset(**data)
        self.assertTrue(fs.is_valid(), fs.errors)
        return set(fs.qs.values_list("pk", flat=True))

    def test_no_params_no_filtering(self):
        self.assertEqual(self.result(), self.all_pks)

    def test_equals_literal_null_is_not_sql_null(self):
        self.assertEqual(
            self.result(label="null", label_operator=EQUALS),
            {self.literal_null_row.pk},
        )

    def test_equals_word(self):
        self.assertEqual(
            self.result(label="apple", label_operator=EQUALS),
            {self.word_row.pk},
        )

    def test_equals_empty_string(self):
        self.assertEqual(
            self.result(label="", label_operator=EQUALS),
            {self.empty_row.pk},
        )

    def test_not_equals_literal_null(self):
        self.assertEqual(
            self.result(label="null", label_operator=NOT_EQUALS),
            {self.null_row.pk, self.empty_row.pk, self.word_row.pk},
        )

    def test_not_equals_empty_string(self):
        self.assertEqual(
            self.result(label="", label_operator=NOT_EQUALS),
            {self.null_row.pk, self.word_row.pk, self.literal_null_row.pk},
        )

    def test_isnull(self):
        self.assertEqual(
            self.result(label_operator=IS_NULL), {self.null_row.pk}
        )

    def test_isnotnull(self):
        self.assertEqual(
            self.result(label_operator=IS_NOT_NULL),
            self.all_pks - {self.null_row.pk},
        )

    def test_isempty(self):
        self.assertEqual(
            self.result(label_operator=IS_EMPTY), {self.empty_row.pk}
        )

    def test_isnotempty(self):
        self.assertEqual(
            self.result(label_operator=IS_NOT_EMPTY),
            {self.word_row.pk, self.literal_null_row.pk},
        )

    def test_valueless_operator_ignores_value(self):
        self.assertEqual(
            self.result(label="apple", label_operator=IS_NULL),
            {self.null_row.pk},
        )

    def test_bare_value_defaults_to_equals(self):
        self.assertEqual(
            self.result(label="apple"), {self.word_row.pk}
        )

    def test_bare_empty_value_is_noop(self):
        self.assertEqual(self.result(label=""), self.all_pks)

    def test_results_are_mutually_distinct(self):
        equals_null = self.result(label="null", label_operator=EQUALS)
        equals_empty = self.result(label="", label_operator=EQUALS)
        isnull = self.result(label_operator=IS_NULL)
        isempty = self.result(label_operator=IS_EMPTY)
        self.assertFalse(equals_null & isnull)
        self.assertFalse(equals_null & isempty)
        self.assertFalse(isnull & isempty)
        self.assertFalse(equals_empty & isnull)
        # ordinary words fall into none of the three empty-value buckets
        self.assertNotIn(self.word_row.pk, equals_null | equals_empty | isnull | isempty)


class PlainCharFilterEmptyHabitTests(TestCase):
    def test_plain_char_filter_still_skips_empty_input(self):
        class F(FilterSet):
            label = CharFilter(field_name="label")

            class Meta:
                model = NullableText
                fields = []

        NullableText.objects.create(label=None)
        row = NullableText.objects.create(label="apple")
        fs = F(data={"label": ""}, queryset=NullableText.objects.all())
        self.assertTrue(fs.is_valid())
        self.assertEqual(set(fs.qs.values_list("pk", flat=True)), {1, 2})

        fs = F(data={"label": "apple"}, queryset=NullableText.objects.all())
        self.assertEqual(set(fs.qs.values_list("pk", flat=True)), {row.pk})
