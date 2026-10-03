import datetime as dt
import decimal
import unittest
from datetime import datetime, time, timedelta, tzinfo

import django
import pytz
from django import forms
from django.test import TestCase, override_settings
from django.utils import timezone

from django_filters.fields import (
    BaseCSVField,
    BaseRangeField,
    ChoiceField,
    DateIntervalField,
    DateRangeField,
    DateTimeRangeField,
    Interval,
    IntervalEndpoint,
    IntervalField,
    IsoDateTimeField,
    IsoDateTimeRangeField,
    Lookup,
    LookupChoiceField,
    RangeField,
    TimeRangeField,
)
from django_filters.widgets import (
    BaseCSVWidget,
    BoundaryWidget,
    CSVWidget,
    IntervalWidget,
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


class IntervalEndpointTests(TestCase):
    def test_defaults_to_closed(self):
        endpoint = IntervalEndpoint(to_d(10))
        self.assertEqual(endpoint.value, to_d(10))
        self.assertFalse(endpoint.open)
        self.assertTrue(endpoint.included)

    def test_open(self):
        endpoint = IntervalEndpoint(to_d(10), open=True)
        self.assertTrue(endpoint.open)
        self.assertFalse(endpoint.included)

    def test_open_is_coerced_to_bool(self):
        self.assertFalse(IntervalEndpoint(to_d(10), open="").open)
        self.assertTrue(IntervalEndpoint(to_d(10), open="open").open)


class IntervalTests(TestCase):
    def endpoint(self, value, open=False):
        if value is None:
            return None
        return IntervalEndpoint(to_d(value), open=open)

    def interval(self, start, start_open=False, stop=None, stop_open=False):
        return Interval(
            self.endpoint(start, start_open), self.endpoint(stop, stop_open)
        )

    def test_unbounded(self):
        self.assertTrue(self.interval(None, stop=None).is_unbounded)
        self.assertFalse(self.interval(10).is_unbounded)
        self.assertFalse(self.interval(None, stop=20).is_unbounded)

    def test_not_empty_for_proper_interval(self):
        self.assertFalse(self.interval(10, stop=20).is_empty)
        self.assertFalse(self.interval(10, stop=10).is_empty)
        self.assertFalse(self.interval(None, stop=10).is_empty)

    def test_empty_when_equal_and_any_boundary_open(self):
        self.assertTrue(self.interval(10, start_open=True, stop=10).is_empty)
        self.assertTrue(self.interval(10, stop=10, stop_open=True).is_empty)
        self.assertTrue(
            self.interval(10, True, 10, True).is_empty
        )


class IntervalFieldTests(TestCase):
    def setUp(self):
        self.f = IntervalField(required=False)

    def test_field(self):
        # two endpoint fields plus two boundary flags
        self.assertEqual(len(self.f.fields), 4)
        self.assertIsInstance(self.f.widget, IntervalWidget)

    def test_reusable_with_custom_endpoint_fields(self):
        f = IntervalField(
            fields=(forms.IntegerField(), forms.IntegerField()), required=False
        )
        interval = f.clean(["10", "open", "20", ""])
        self.assertIsInstance(interval.start.value, int)
        self.assertEqual(interval.start.value, 10)
        self.assertTrue(interval.start.open)
        self.assertEqual(interval.stop.value, 20)

    def test_not_provided_returns_none(self):
        self.assertIsNone(self.f.clean([]))
        self.assertIsNone(self.f.clean(["", "", "", ""]))

    def test_closed_interval(self):
        interval = self.f.clean(["10", "", "20", ""])
        self.assertEqual(
            interval,
            Interval(IntervalEndpoint(to_d(10)), IntervalEndpoint(to_d(20))),
        )

    def test_open_boundaries(self):
        interval = self.f.clean(["10", "open", "20", "open"])
        self.assertTrue(interval.start.open)
        self.assertTrue(interval.stop.open)

    def test_missing_boundary_select_defaults_to_closed(self):
        # only the endpoint values are submitted, as with a hand-built form
        interval = self.f.clean(["10", None, "20", None])
        self.assertFalse(interval.start.open)
        self.assertFalse(interval.stop.open)

    def test_unbounded_start(self):
        interval = self.f.clean(["", "", "20", "open"])
        self.assertIsNone(interval.start)
        self.assertEqual(interval.stop, IntervalEndpoint(to_d(20), open=True))

    def test_unbounded_stop(self):
        interval = self.f.clean(["10", "open", "", ""])
        self.assertEqual(interval.start, IntervalEndpoint(to_d(10), open=True))
        self.assertIsNone(interval.stop)

    def test_boundary_only_submission_is_no_interval(self):
        # boundary flags without endpoint values do not form an interval
        self.assertIsNone(self.f.clean(["", "open", "", "open"]))

    def test_reversed_boundaries_raises(self):
        with self.assertRaises(forms.ValidationError) as ctx:
            self.f.clean(["20", "", "10", ""])
        self.assertEqual(ctx.exception.code, "invalid_order")

    def test_equal_closed_is_valid(self):
        interval = self.f.clean(["10", "", "10", ""])
        self.assertEqual(interval.start.value, interval.stop.value)
        self.assertFalse(interval.is_empty)

    def test_equal_open_is_empty_and_invalid(self):
        for flags in (("open", ""), ("", "open"), ("open", "open")):
            with self.subTest(flags=flags):
                with self.assertRaises(forms.ValidationError) as ctx:
                    self.f.clean(["10", flags[0], "10", flags[1]])
                self.assertEqual(ctx.exception.code, "empty")

    def test_invalid_endpoint_value(self):
        with self.assertRaises(forms.ValidationError):
            self.f.clean(["abc", "", "20", ""])

    def test_widget_value_from_datadict(self):
        data = {
            "price_after": "10",
            "price_after_open": "open",
            "price_before": "20",
            "price_before_open": "",
        }
        self.assertEqual(
            self.f.widget.value_from_datadict(data, None, "price"),
            ["10", "open", "20", ""],
        )
        # the boundary select normalizes values itself
        boundary = self.f.widget.widgets[1]
        self.assertTrue(boundary.value_from_datadict(data, None, "price_after_open"))
        self.assertFalse(boundary.value_from_datadict(data, None, "price_before_open"))
        self.assertIsNone(
            boundary.value_from_datadict({}, None, "price_after_open")
        )

    def test_widget_decompress_roundtrip(self):
        interval = self.f.clean(["10", "open", "20", ""])
        self.assertEqual(
            self.f.widget.decompress(interval),
            [to_d(10), BoundaryWidget.OPEN, to_d(20), BoundaryWidget.CLOSED],
        )


class DateIntervalFieldTests(TestCase):
    def setUp(self):
        self.f = DateIntervalField(required=False)

    def test_field(self):
        self.assertIsInstance(self.f.fields[0], forms.DateField)
        self.assertIsInstance(self.f.fields[2], forms.DateField)

    def test_closed_uses_full_boundary_days(self):
        interval = self.f.clean(["2020-01-01", "", "2020-01-31", ""])
        self.assertEqual(
            timezone.make_aware(datetime(2020, 1, 1, 0, 0)),
            interval.start.value,
        )
        self.assertEqual(
            timezone.make_aware(datetime(2020, 1, 31, 23, 59, 59, 999999)),
            interval.stop.value,
        )

    def test_open_keeps_plain_dates(self):
        interval = self.f.clean(["2020-01-01", "open", "2020-01-31", "open"])
        self.assertEqual(interval.start.value, dt.date(2020, 1, 1))
        self.assertEqual(interval.stop.value, dt.date(2020, 1, 31))

    def test_reversed_dates_raise(self):
        with self.assertRaises(forms.ValidationError) as ctx:
            self.f.clean(["2020-01-31", "", "2020-01-01", ""])
        self.assertEqual(ctx.exception.code, "invalid_order")

    def test_equal_open_dates_raise(self):
        with self.assertRaises(forms.ValidationError) as ctx:
            self.f.clean(["2020-01-01", "open", "2020-01-01", ""])
        self.assertEqual(ctx.exception.code, "empty")

    def test_not_provided(self):
        self.assertIsNone(self.f.clean([]))
        self.assertIsNone(self.f.clean(["", "", "", ""]))


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
