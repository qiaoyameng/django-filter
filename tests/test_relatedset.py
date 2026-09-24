from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.http import QueryDict
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from django_filters.filters import RelatedSetFilter
from django_filters.filterset import FilterSet

from .models import Order, OrderItem, Tag


class OrderItemFilterSet(FilterSet):
    class Meta:
        model = OrderItem
        fields = {
            "sku": ["exact"],
            "quantity": ["gte"],
            "out_of_stock": ["exact"],
            "shipped": ["exact"],
            "note": ["contains"],
        }


class TagFilterSet(FilterSet):
    class Meta:
        model = Tag
        fields = {"name": ["exact"]}


class OrderFilterSet(FilterSet):
    any_out_of_stock = RelatedSetFilter(
        OrderItemFilterSet, field_name="items", quantifier="any"
    )
    none_out_of_stock = RelatedSetFilter(
        OrderItemFilterSet, field_name="items", quantifier="none"
    )
    all_shipped = RelatedSetFilter(
        OrderItemFilterSet, field_name="items", quantifier="all"
    )
    all_shipped_non_empty = RelatedSetFilter(
        OrderItemFilterSet, field_name="items", quantifier="all",
        require_non_empty=True,
    )
    any_tag = RelatedSetFilter(TagFilterSet, field_name="tags", quantifier="any")

    class Meta:
        model = Order
        fields = []


class RelatedSetFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # order1: one out-of-stock item (qty 1, NULL note), one shipped item
        cls.order1 = Order.objects.create(reference="order-1")
        OrderItem.objects.create(
            order=cls.order1, sku="a", quantity=1, out_of_stock=True
        )
        OrderItem.objects.create(
            order=cls.order1, sku="b", quantity=5, shipped=True, note="fragile"
        )
        # order2: everything in stock and shipped
        cls.order2 = Order.objects.create(reference="order-2")
        OrderItem.objects.create(order=cls.order2, sku="c", quantity=2, shipped=True)
        OrderItem.objects.create(order=cls.order2, sku="d", quantity=3, shipped=True)
        # order3: no items at all
        cls.order3 = Order.objects.create(reference="order-3")
        # order4: two out-of-stock items, one shipped with a note
        cls.order4 = Order.objects.create(reference="order-4")
        OrderItem.objects.create(
            order=cls.order4, sku="e", quantity=10, out_of_stock=True,
            shipped=True, note="urgent",
        )
        OrderItem.objects.create(
            order=cls.order4, sku="f", quantity=2, out_of_stock=True
        )

        vip = Tag.objects.create(name="vip")
        bulk = Tag.objects.create(name="bulk")
        cls.order1.tags.add(vip)
        cls.order2.tags.add(vip, bulk)

    def filter(self, data, filterset_class=OrderFilterSet):
        return filterset_class(data=data, queryset=Order.objects.all()).qs

    def test_any(self):
        qs = self.filter({"any_out_of_stock__out_of_stock": "true"})
        # order4 has two matching items but must appear exactly once.
        self.assertEqual(list(qs), [self.order1, self.order4])

    def test_any_conditions_apply_to_the_same_record(self):
        # order1 has an out-of-stock item (qty 1) and a qty-5 item (in
        # stock), but no single item satisfying both conditions.
        qs = self.filter(
            {
                "any_out_of_stock__out_of_stock": "true",
                "any_out_of_stock__quantity__gte": "5",
            }
        )
        self.assertEqual(list(qs), [self.order4])

    def test_none(self):
        qs = self.filter({"none_out_of_stock__out_of_stock": "true"})
        # order3 has no items, so "none" holds for it.
        self.assertEqual(list(qs), [self.order2, self.order3])

    def test_all(self):
        qs = self.filter({"all_shipped__shipped": "true"})
        # order3 has no items: "all" is vacuously true.
        self.assertEqual(list(qs), [self.order2, self.order3])

    def test_all_require_non_empty(self):
        qs = self.filter({"all_shipped_non_empty__shipped": "true"})
        self.assertEqual(list(qs), [self.order2])

    def test_null_field_values(self):
        # order1's out-of-stock item has a NULL note; NULL rows simply do
        # not match the child condition instead of breaking the query.
        qs = self.filter({"any_out_of_stock__note__contains": "urgent"})
        self.assertEqual(list(qs), [self.order4])

        qs = self.filter({"none_out_of_stock__note__contains": "urgent"})
        self.assertEqual(list(qs), [self.order1, self.order2, self.order3])

    def test_many_to_many(self):
        qs = self.filter({"any_tag__name": "vip"})
        self.assertEqual(list(qs), [self.order1, self.order2])

    def test_many_to_many_none(self):
        class NoneTagFilterSet(OrderFilterSet):
            none_tag = RelatedSetFilter(
                TagFilterSet, field_name="tags", quantifier="none"
            )

        qs = self.filter({"none_tag__name": "vip"}, filterset_class=NoneTagFilterSet)
        self.assertEqual(list(qs), [self.order3, self.order4])

    def test_unbound_without_child_data(self):
        qs = self.filter({})
        self.assertEqual(
            list(qs), [self.order1, self.order2, self.order3, self.order4]
        )

    def test_accepts_querydict(self):
        qs = self.filter(QueryDict("any_out_of_stock__out_of_stock=true"))
        self.assertEqual(list(qs), [self.order1, self.order4])

    def test_form_prefix(self):
        filterset = OrderFilterSet(
            data={"pfx-any_out_of_stock__out_of_stock": "true"},
            queryset=Order.objects.all(),
            prefix="pfx",
        )
        self.assertEqual(list(filterset.qs), [self.order1, self.order4])

    def test_nested_relation_path(self):
        class OrderItemLevelFilterSet(FilterSet):
            any_tag = RelatedSetFilter(TagFilterSet, field_name="order__tags")

            class Meta:
                model = OrderItem
                fields = []

        qs = OrderItemLevelFilterSet(
            data={"any_tag__name": "bulk"}, queryset=OrderItem.objects.all()
        ).qs
        # only order2 carries the "bulk" tag
        self.assertEqual(list(qs), list(self.order2.items.all()))

    def test_sql_uses_correlated_subquery(self):
        qs = self.filter({"any_out_of_stock__out_of_stock": "true"})
        sql = str(qs.query)
        # no JOINs: the related table only appears inside EXISTS, correlated
        # to the outer query's table.
        self.assertNotIn("JOIN", sql)
        self.assertIn("EXISTS", sql)
        self.assertIn('"order_id" = ("tests_order"."id")', sql)

    def test_all_sql_negates_existence_of_non_matching(self):
        qs = self.filter({"all_shipped__shipped": "true"})
        sql = str(qs.query)
        self.assertIn("NOT EXISTS", sql)
        self.assertIn('"order_id" = ("tests_order"."id")', sql)

    def test_related_objects_are_not_loaded_into_python(self):
        qs = self.filter({"any_out_of_stock__out_of_stock": "true"})
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(list(qs), [self.order1, self.order4])
        self.assertEqual(len(ctx.captured_queries), 1)

    def test_child_errors_keep_field_position(self):
        filterset = OrderFilterSet(
            data={"any_out_of_stock__quantity__gte": "not-a-number"},
            queryset=Order.objects.all(),
        )
        self.assertFalse(filterset.is_valid())
        # the parent form error names the child field...
        errors = filterset.errors["any_out_of_stock"]
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("quantity__gte: "))
        # ...and structured errors are available keyed by child field.
        child_errors = filterset.filters["any_out_of_stock"].child_errors
        self.assertEqual(list(child_errors.keys()), ["quantity__gte"])

    def test_invalid_child_data_disables_the_filter(self):
        qs = self.filter({"any_out_of_stock__quantity__gte": "not-a-number"})
        self.assertEqual(
            list(qs), [self.order1, self.order2, self.order3, self.order4]
        )

    def test_invalid_quantifier(self):
        with self.assertRaises(ValueError):
            RelatedSetFilter(
                OrderItemFilterSet, field_name="items", quantifier="some"
            )

    def test_child_filterset_model_mismatch(self):
        class MisconfiguredFilterSet(FilterSet):
            broken = RelatedSetFilter(TagFilterSet, field_name="items")

            class Meta:
                model = Order
                fields = []

        filterset = MisconfiguredFilterSet(
            data={"broken__name": "vip"}, queryset=Order.objects.all()
        )
        self.assertTrue(filterset.is_valid())
        with self.assertRaises(ImproperlyConfigured):
            filterset.qs
