import datetime as dt
import decimal
import unittest
from datetime import datetime, time, timedelta, tzinfo

import django
import pytz
from django import forms
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.utils import timezone

from django_filters.fields import (
    BaseCSVField,
    BaseRangeField,
    ChoiceField,
    DateRangeField,
    EmptyValueField,
    EmptyValueOperation,
    DateTimeRangeField,
    IsoDateTimeField,
    IsoDateTimeRangeField,
    Lookup,
    LookupChoiceField,
    RangeField,
    TimeRangeField,
)
from django_filters.widgets import (
    BaseCSVWidget,
    CSVWidget,
    EmptyValueWidget,
    RangeWidget,
)


def to_d(float_value):
    return decimal.Decimal("%.2f" % float_value)


class LookupTests(TestCase):
    def test_empty_attrs(self):
        with self.assertRaisesMessage(ValueError, ""):
            Lookup(None, None)

        with self.assertRaisesMessage(ValueError, ""):
            Lookup("", "")

    def test_empty_value(self):
        with self.assertRaisesMessage(ValueError, ""):
            Lookup("", "exact")

    def test_empty_lookup_expr(self):
        with self.assertRaisesMessage(ValueError, ""):
            Lookup("Value", "")


class RangeFieldTests(TestCase):
    def test_field(self):
        f = RangeField()
        self.assertEqual(len(f.fields), 2)

    def test_clean(self):
        w = RangeWidget()
        f = RangeField(widget=w, required=False)

        self.assertEqual(f.clean(["12.34", "55"]), slice(to_d(12.34), to_d(55)))
        self.assertIsNone(f.clean([]))


class ChoiceFieldTests(TestCase):
    def test_callable_choices_is_lazy(self):
        def choices():
            self.fail("choices should not be called during initialization")

        ChoiceField(choices=choices)


class DateRangeFieldTests(TestCase):
    def test_field(self):
        f = DateRangeField()
        self.assertEqual(len(f.fields), 2)

    @override_settings(USE_TZ=False)
    def test_clean(self):
        w = RangeWidget()
        f = DateRangeField(widget=w, required=False)
        self.assertEqual(
            f.clean(["2015-01-01", "2015-01-10"]),
            slice(
                datetime(2015, 1, 1, 0, 0, 0), datetime(2015, 1, 10, 23, 59, 59, 999999)
            ),
        )
        self.assertIsNone(f.clean([]))


class DateTimeRangeFieldTests(TestCase):
    def test_field(self):
        f = DateTimeRangeField()
        self.assertEqual(len(f.fields), 2)

    @override_settings(USE_TZ=False)
    def test_clean(self):
        w = RangeWidget()
        f = DateTimeRangeField(widget=w)
        self.assertEqual(
            f.clean(["2015-01-01 10:30", "2015-01-10 8:45"]),
            slice(datetime(2015, 1, 1, 10, 30, 0), datetime(2015, 1, 10, 8, 45, 0)),
        )


class IsoDateTimeRangeFieldTests(TestCase):
    def test_field(self):
        f = IsoDateTimeRangeField()
        self.assertEqual(len(f.fields), 2)

    def test_clean(self):
        w = RangeWidget()
        f = IsoDateTimeRangeField(widget=w)
        expected = slice(
            datetime(2015, 1, 1, 9, 30, 1, 123000, tzinfo=dt.timezone.utc),
            datetime(2015, 1, 10, 7, 45, 2, 345000, tzinfo=dt.timezone.utc),
        )
        actual = f.clean(
            ["2015-01-01T10:30:01.123000+01:00", "2015-01-10T08:45:02.345000+01:00"]
        )
        self.assertEqual(expected, actual)


class TimeRangeFieldTests(TestCase):
    def test_field(self):
        f = DateRangeField()
        self.assertEqual(len(f.fields), 2)

    def test_clean(self):
        w = RangeWidget()
        f = TimeRangeField(widget=w)

        self.assertEqual(
            f.clean(["10:15", "12:30"]), slice(time(10, 15, 0), time(12, 30, 0))
        )


class LookupChoiceFieldTests(TestCase):
    def test_field(self):
        inner = forms.DecimalField()
        f = LookupChoiceField(inner, [("gt", "gt"), ("lt", "lt")])
        self.assertEqual(len(f.fields), 2)

    def test_clean(self):
        inner = forms.DecimalField()
        f = LookupChoiceField(inner, [("gt", "gt"), ("lt", "lt")], required=False)
        self.assertEqual(f.clean(["12.34", "lt"]), Lookup(to_d(12.34), "lt"))
        self.assertEqual(f.clean([]), None)

        with self.assertRaisesMessage(forms.ValidationError, "Select a lookup."):
            f.clean(["12.34", ""])

    def test_render_used_html5(self):
        inner = forms.DecimalField()
        f = LookupChoiceField(inner, [("gt", "gt"), ("lt", "lt")], empty_label=None)
        self.assertHTMLEqual(
            f.widget.render("price", ""),
            """
            <input type="number" step="any" name="price" />
            <select name="price_lookup">
                <option value="gt">gt</option>
                <option value="lt">lt</option>
            </select>""",
        )
        self.assertHTMLEqual(
            f.widget.render("price", ["abc", "lt"]),
            """
            <input type="number" step="any" name="price" value="abc" />
            <select name="price_lookup">
                <option value="gt">gt</option>
                <option selected="selected" value="lt">lt</option>
            </select>""",
        )


class EmptyValueFieldTests(TestCase):
    operators = [
        "equals",
        "not_equals",
        "isnull",
        "isnotnull",
        "isempty",
        "isnotempty",
    ]

    def make_field(self, operators=None, **kwargs):
        return EmptyValueField(
            operators or self.operators, required=False, **kwargs
        )

    def make_form(self, field):
        class F(forms.Form):
            value = field

        return F

    def clean(self, query_string, field=None):
        field = field or self.make_field()
        form = self.make_form(field)(QueryDict(query_string))
        self.assertTrue(form.is_valid(), form.errors)
        return form.cleaned_data["value"]

    def test_field(self):
        f = self.make_field()
        self.assertEqual(len(f.fields), 2)
        self.assertIsInstance(f.widget, EmptyValueWidget)

    def test_no_params_compresses_to_none(self):
        self.assertIsNone(self.clean(""))

    def test_blank_value_without_operator_is_none(self):
        # empty string submitted but no operator -> skipped, old behavior
        self.assertIsNone(self.clean("value="))

    def test_value_only_defaults_to_equals(self):
        self.assertEqual(
            self.clean("value=word"),
            EmptyValueOperation("equals", "word"),
        )

    def test_literal_null_is_a_plain_value(self):
        self.assertEqual(
            self.clean("value=null"),
            EmptyValueOperation("equals", "null"),
        )

    def test_blank_value_with_equals_keeps_empty_string(self):
        self.assertEqual(
            self.clean("value=&value__operator=equals"),
            EmptyValueOperation("equals", ""),
        )

    def test_unary_operators(self):
        self.assertEqual(
            self.clean("value__operator=isnull"),
            EmptyValueOperation("isnull", None),
        )
        self.assertEqual(
            self.clean("value__operator=isempty"),
            EmptyValueOperation("isempty", None),
        )

    def test_equals_without_value_compresses_to_none(self):
        self.assertIsNone(self.clean("value__operator=equals"))
        self.assertIsNone(self.clean("value__operator=not_equals"))

    def test_hyphenated_operator_spellings(self):
        self.assertEqual(
            self.clean("value=x&value__operator=not-equals"),
            EmptyValueOperation("not_equals", "x"),
        )
        self.assertEqual(
            self.clean("value__operator=is-not-null"),
            EmptyValueOperation("isnotnull", None),
        )

    def test_unsupported_operator_raises_form_error(self):
        form = self.make_form(self.make_field())(
            QueryDict("value__operator=regex")
        )
        self.assertFalse(form.is_valid())
        self.assertIn("value", form.errors)

    def test_unsupported_operator_for_restricted_field(self):
        field = self.make_field(operators=["equals", "isnull"])
        form = self.make_form(field)(QueryDict("value__operator=isempty"))
        self.assertFalse(form.is_valid())
        self.assertIn("value", form.errors)

        form = self.make_form(field)(QueryDict("value__operator=isnull"))
        self.assertTrue(form.is_valid(), form.errors)

    def test_widget_uses_double_underscore_operator_param(self):
        field = self.make_field()
        html = field.widget.render("value", None)
        self.assertIn('name="value__operator"', html)
        self.assertIn('name="value"', html)


class IsoDateTimeFieldTests(TestCase):
    reference_str = "2015-07-19T13:34:51.759"
    reference_dt = datetime(2015, 7, 19, 13, 34, 51, 759000)
    field = IsoDateTimeField()

    def parse_input(self, value):
        return self.field.strptime(value, IsoDateTimeField.ISO_8601)

    def test_datetime_string_is_parsed(self):
        d = self.parse_input(self.reference_str)
        self.assertTrue(isinstance(d, datetime))

    def test_datetime_string_with_timezone_is_parsed(self):
        d = self.parse_input(self.reference_str + "+01:00")
        self.assertTrue(isinstance(d, datetime))

    def test_datetime_zulu(self):
        d = self.parse_input(self.reference_str + "Z")
        self.assertTrue(isinstance(d, datetime))

    @unittest.skipUnless(django.VERSION < (5, 0), "pytz support removed in Django 5.0")
    @override_settings(TIME_ZONE="UTC")
    def test_datetime_timezone_awareness(self):
        utc, tokyo = pytz.timezone("UTC"), pytz.timezone("Asia/Tokyo")

        # by default, use the server timezone
        reference = utc.localize(self.reference_dt)
        parsed = self.parse_input(self.reference_str)
        self.assertIsInstance(parsed.tzinfo, tzinfo)
        self.assertEqual(parsed, reference)

        # if set, use the active timezone
        reference = tokyo.localize(self.reference_dt)
        with timezone.override(tokyo):
            parsed = self.parse_input(self.reference_str)
        self.assertIsInstance(parsed.tzinfo, tzinfo)
        self.assertEqual(parsed.tzinfo.zone, tokyo.zone)
        self.assertEqual(parsed, reference)

        # if provided, utc offset should have precedence
        reference = utc.localize(self.reference_dt - timedelta(hours=1))
        parsed = self.parse_input(self.reference_str + "+01:00")
        self.assertIsInstance(parsed.tzinfo, tzinfo)
        self.assertEqual(parsed, reference)

    @override_settings(USE_TZ=False)
    def test_datetime_timezone_naivety(self):
        reference = self.reference_dt.replace()

        parsed = self.parse_input(self.reference_str + "+01:00")
        self.assertIsNone(parsed.tzinfo)
        self.assertEqual(parsed, reference - timedelta(hours=1))

        parsed = self.parse_input(self.reference_str)
        self.assertIsNone(parsed.tzinfo)
        self.assertEqual(parsed, reference)

    def test_datetime_non_iso_format(self):
        f = IsoDateTimeField()
        parsed = f.strptime("19-07-2015T51:34:13.759", "%d-%m-%YT%S:%M:%H.%f")
        self.assertTrue(isinstance(parsed, datetime))
        self.assertEqual(parsed, self.reference_dt)

    def test_datetime_wrong_format(self):
        with self.assertRaises(ValueError):
            self.parse_input("19-07-2015T51:34:13.759")


class BaseCSVFieldTests(TestCase):
    class DecimalCSVField(BaseCSVField, forms.DecimalField):
        pass

    def test_clean(self):
        # Filter class sets required=False by default
        field = self.DecimalCSVField(required=False)

        self.assertEqual(field.clean(None), None)
        self.assertEqual(field.clean(""), [])
        self.assertEqual(field.clean(["1"]), [1])
        self.assertEqual(field.clean(["1", "2"]), [1, 2])
        self.assertEqual(field.clean(["1", "2", "3"]), [1, 2, 3])

    def test_validation_error(self):
        field = self.DecimalCSVField()

        msg = "Enter a number."
        with self.assertRaisesMessage(forms.ValidationError, msg):
            field.clean(["a", "b", "c"])

    def test_required_error(self):
        field = self.DecimalCSVField(required=True)

        msg = "This field is required."
        with self.assertRaisesMessage(forms.ValidationError, msg):
            field.clean(None)

        with self.assertRaisesMessage(forms.ValidationError, msg):
            field.clean([""])

    def test_derived_widget(self):
        with self.assertRaises(AssertionError) as excinfo:
            BaseCSVField(widget=RangeWidget())

        msg = str(excinfo.exception)
        self.assertIn("'BaseCSVField.widget' must be a widget class", msg)
        self.assertIn("RangeWidget", msg)

        widget = CSVWidget(attrs={"class": "class"})
        field = BaseCSVField(widget=widget)
        self.assertIsInstance(field.widget, CSVWidget)
        self.assertEqual(field.widget.attrs, {"class": "class"})

        field = BaseCSVField(widget=CSVWidget)
        self.assertIsInstance(field.widget, CSVWidget)

        field = BaseCSVField(widget=forms.Select)
        self.assertIsInstance(field.widget, forms.Select)
        self.assertIsInstance(field.widget, BaseCSVWidget)


class BaseRangeFieldTests(TestCase):
    class DecimalRangeField(BaseRangeField, forms.DecimalField):
        pass

    def test_clean(self):
        # Filter class sets required=False by default
        field = self.DecimalRangeField(required=False)

        self.assertEqual(field.clean(None), None)
        self.assertEqual(field.clean(""), [])
        self.assertEqual(field.clean([]), [])
        self.assertEqual(field.clean(["1", "2"]), [1, 2])

    def test_validation_error(self):
        field = self.DecimalRangeField()

        msg = "Range query expects two values."
        with self.assertRaisesMessage(forms.ValidationError, msg):
            field.clean(["1"])

        with self.assertRaisesMessage(forms.ValidationError, msg):
            field.clean(["1", "2", "3"])

    def test_required_error(self):
        field = self.DecimalRangeField(required=True)

        msg = "This field is required."
        with self.assertRaisesMessage(forms.ValidationError, msg):
            field.clean(None)

        with self.assertRaisesMessage(forms.ValidationError, msg):
            field.clean([""])
