from django.test import TestCase

from django_filters import FilterSet
from django_filters.filters import RelatedSetFilter
from django_filters.utils import get_reverse_relation_path

from .models import Order, OrderItem, Tag


class OrderItemFilter(FilterSet):
    class Meta:
        model = OrderItem
        fields = {"status": ["exact"], "quantity": ["gte"]}


class TagFilter(FilterSet):
    class Meta:
        model = Tag
        fields = {"name": ["exact", "icontains"]}


class OrderFilter(FilterSet):
    any_items = RelatedSetFilter(OrderItemFilter, field_name="items", quantifier="any")
    none_items = RelatedSetFilter(
        OrderItemFilter, field_name="items", quantifier="none"
    )
    all_items = RelatedSetFilter(OrderItemFilter, field_name="items", quantifier="all")
    all_items_nonempty = RelatedSetFilter(
        OrderItemFilter,
        field_name="items",
        quantifier="all",
        require_non_empty=True,
    )
    any_tags = RelatedSetFilter(TagFilter, field_name="tags", quantifier="any")

    class Meta:
        model = Order
        fields = []


class RelatedSetFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # two backordered items: exercises duplicate-suppression
        cls.order_dup = Order.objects.create(reference="dup")
        OrderItem.objects.create(
            order=cls.order_dup, sku="a", status="backordered", quantity=1
        )
        OrderItem.objects.create(
            order=cls.order_dup, sku="b", status="backordered", quantity=3
        )

        # conditions split across two records: must NOT match as a pair
        cls.order_split = Order.objects.create(reference="split")
        OrderItem.objects.create(
            order=cls.order_split, sku="c", status="backordered", quantity=1
        )
        OrderItem.objects.create(
            order=cls.order_split, sku="d", status="shipped", quantity=10
        )

        # one shipped item, one item with NULL status
        cls.order_null = Order.objects.create(reference="null")
        OrderItem.objects.create(
            order=cls.order_null, sku="e", status="shipped", quantity=5
        )
        OrderItem.objects.create(
            order=cls.order_null, sku="f", status=None, quantity=2
        )

        # all items shipped
        cls.order_shipped = Order.objects.create(reference="shipped")
        OrderItem.objects.create(
            order=cls.order_shipped, sku="g", status="shipped", quantity=1
        )

        # no items at all
        cls.order_empty = Order.objects.create(reference="empty")

    def filter_refs(self, data):
        f = OrderFilter(data, queryset=Order.objects.all())
        assert f.is_valid(), f.errors
        return sorted(f.qs.values_list("reference", flat=True))

    def test_any_quantifier(self):
        refs = self.filter_refs({"any_items__status": "backordered"})
        self.assertEqual(refs, ["dup", "split"])

    def test_none_quantifier(self):
        refs = self.filter_refs({"none_items__status": "backordered"})
        # empty collection: no record matches, so "none" holds
        self.assertEqual(refs, ["empty", "null", "shipped"])

    def test_all_quantifier(self):
        refs = self.filter_refs({"all_items__status": "shipped"})
        # "empty" is vacuously true; "null" has a NULL-status violator
        self.assertEqual(refs, ["empty", "shipped"])

    def test_all_quantifier_require_non_empty(self):
        refs = self.filter_refs({"all_items_nonempty__status": "shipped"})
        self.assertEqual(refs, ["shipped"])

    def test_conditions_match_same_record(self):
        # "split" has a backordered item and a separate item with
        # quantity 10, but no single record satisfying both conditions
        refs = self.filter_refs(
            {"any_items__status": "backordered", "any_items__quantity__gte": 10}
        )
        self.assertEqual(refs, [])

        # "dup" has a single record satisfying both
        refs = self.filter_refs(
            {"any_items__status": "backordered", "any_items__quantity__gte": 3}
        )
        self.assertEqual(refs, ["dup"])

    def test_no_duplicate_results(self):
        f = OrderFilter(
            {"any_items__status": "backordered"}, queryset=Order.objects.all()
        )
        qs = f.qs
        self.assertEqual(list(qs), [self.order_dup, self.order_split])
        self.assertEqual(qs.count(), 2)

    def test_related_objects_not_loaded(self):
        f = OrderFilter(
            {"any_items__status": "backordered"}, queryset=Order.objects.all()
        )
        with self.assertNumQueries(1):
            list(f.qs)

    def test_sql_is_correlated_subquery(self):
        f = OrderFilter(
            {"any_items__status": "backordered"}, queryset=Order.objects.all()
        )
        sql = str(f.qs.query)
        self.assertIn("EXISTS", sql)
        # the subquery filters the related table by the outer table's pk
        self.assertRegex(sql, r'EXISTS\(SELECT.*"tests_order"\."id"')
        # the outer query itself is not joined against the relation
        self.assertNotIn("JOIN", sql.split(" WHERE ")[0])

    def test_all_sql_negated_correlated_subquery(self):
        f = OrderFilter(
            {"all_items__status": "shipped"}, queryset=Order.objects.all()
        )
        sql = str(f.qs.query)
        self.assertIn("NOT (EXISTS", sql)
        self.assertRegex(sql, r'EXISTS\(SELECT.*"tests_order"\."id"')
        self.assertNotIn("JOIN", sql.split(" WHERE ")[0])

    def test_empty_sub_data_is_noop(self):
        f = OrderFilter({}, queryset=Order.objects.all())
        self.assertEqual(f.qs.count(), 5)

        f = OrderFilter({"any_items__status": ""}, queryset=Order.objects.all())
        self.assertEqual(f.qs.count(), 5)

    def test_unknown_child_param_is_noop(self):
        # the relation path and available conditions are server-configured;
        # unknown params in the request data are simply ignored
        f = OrderFilter({"any_items__nonexistent": "1"}, queryset=Order.objects.all())
        self.assertTrue(f.is_valid())
        self.assertEqual(f.qs.count(), 5)

    def test_child_errors_preserve_field_location(self):
        f = OrderFilter(
            {"any_items__quantity__gte": "not-a-number"},
            queryset=Order.objects.all(),
        )
        self.assertFalse(f.is_valid())
        self.assertIn("any_items__quantity__gte", f.errors)

    def test_invalid_quantifier_raises(self):
        with self.assertRaises(ValueError):
            RelatedSetFilter(OrderItemFilter, field_name="items", quantifier="some")


class RelatedSetFilterManyToManyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tag_priority = Tag.objects.create(name="priority")
        cls.tag_fragile = Tag.objects.create(name="fragile")

        cls.order_tagged = Order.objects.create(reference="tagged")
        cls.order_tagged.tags.add(cls.tag_priority, cls.tag_fragile)

        cls.order_other = Order.objects.create(reference="other")
        cls.order_other.tags.add(cls.tag_fragile)

        cls.order_untagged = Order.objects.create(reference="untagged")

    def test_forward_many_to_many(self):
        f = OrderFilter({"any_tags__name": "priority"}, queryset=Order.objects.all())
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(list(f.qs), [self.order_tagged])

    def test_many_to_many_no_duplicates(self):
        # both tags match icontains "a"; the order must appear only once
        f = OrderFilter(
            {"any_tags__name__icontains": "a"}, queryset=Order.objects.all()
        )
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(list(f.qs), [self.order_tagged, self.order_other])

    def test_many_to_many_sql_correlated(self):
        f = OrderFilter({"any_tags__name": "priority"}, queryset=Order.objects.all())
        sql = str(f.qs.query)
        self.assertIn("EXISTS", sql)
        self.assertRegex(sql, r'EXISTS\(SELECT.*"tests_order"\."id"')
        # the m2m join happens inside the subquery, not on the outer query
        self.assertNotIn("JOIN", sql.split(" WHERE ")[0])

    def test_reverse_many_to_many(self):
        class OrderNoteFilter(FilterSet):
            class Meta:
                model = Order
                fields = {"reference": ["exact"]}

        class TagSetFilter(FilterSet):
            orders = RelatedSetFilter(
                OrderNoteFilter, field_name="orders", quantifier="any"
            )

            class Meta:
                model = Tag
                fields = []

        f = TagSetFilter({"orders__reference": "tagged"}, queryset=Tag.objects.all())
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(
            sorted(f.qs.values_list("name", flat=True)), ["fragile", "priority"]
        )


class ReverseRelationPathTests(TestCase):
    def test_reverse_foreign_key(self):
        self.assertEqual(get_reverse_relation_path(Order, "items"), "order")

    def test_forward_many_to_many(self):
        self.assertEqual(get_reverse_relation_path(Order, "tags"), "orders")

    def test_reverse_many_to_many(self):
        self.assertEqual(get_reverse_relation_path(Tag, "orders"), "tags")

    def test_forward_foreign_key(self):
        self.assertEqual(get_reverse_relation_path(OrderItem, "order"), "items")

    def test_multi_hop(self):
        self.assertEqual(
            get_reverse_relation_path(Order, "items__order"), "order__items"
        )
