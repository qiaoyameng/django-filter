from django.test import TestCase

from django_filters.exceptions import FilterDependencyError
from django_filters.filters import CharFilter, NumberFilter
from django_filters.filterset import FilterSet

from .models import User


class RegionFilter(CharFilter):
    """Filter that exposes computed data to its dependents as context."""

    def get_context(self, qs, value):
        return {"region": value, "warehouses": ["%s-wh" % value]}


def region_stock_filterset(calls, stock_policy="skip"):
    """
    Build the inventory FilterSet: the region filter must run first, and the
    available-stock filter reads the context it computed. Note that ``stock``
    is deliberately declared *before* ``region``.
    """

    class RegionStockFilterSet(FilterSet):
        stock = CharFilter(
            method="filter_stock",
            depends_on=["region"],
            on_missing_dependency=stock_policy,
        )
        region = RegionFilter(method="filter_region")

        class Meta:
            model = User
            fields = []

        def filter_region(self, qs, name, value):
            calls.append("region")
            return qs

        def filter_stock(self, qs, name, value):
            context = self.filter_contexts["region"]
            calls.append("stock:%s" % context["region"])
            return qs

    return RegionStockFilterSet


def recording_filterset(declarations, calls):
    """Build a FilterSet whose filters record their execution in ``calls``."""
    attrs = {}
    for name, depends_on in declarations:
        attrs[name] = CharFilter(method="record", depends_on=depends_on)

    def record(self, qs, name, value):
        calls.append(name)
        return qs

    attrs["record"] = record
    attrs["Meta"] = type("Meta", (), {"model": User, "fields": []})
    return type("RecordingFilterSet", (FilterSet,), attrs)


class DependencyDeclarationTests(TestCase):
    def test_unknown_dependency_raises_at_class_creation(self):
        with self.assertRaisesMessage(
            ValueError, "Filter 'stock' depends on unknown filter 'region'."
        ):

            class Broken(FilterSet):
                stock = CharFilter(depends_on=["region"])

                class Meta:
                    model = User
                    fields = []

    def test_self_dependency_raises_at_class_creation(self):
        with self.assertRaisesMessage(
            ValueError, "Filter 'region' cannot depend on itself."
        ):

            class Broken(FilterSet):
                region = CharFilter(depends_on=["region"])

                class Meta:
                    model = User
                    fields = []

    def test_direct_cycle_raises_at_class_creation(self):
        with self.assertRaisesMessage(
            ValueError,
            "Circular filter dependencies detected involving: 'a', 'b'.",
        ):

            class Broken(FilterSet):
                a = CharFilter(depends_on=["b"])
                b = CharFilter(depends_on=["a"])

                class Meta:
                    model = User
                    fields = []

    def test_indirect_cycle_raises_at_class_creation(self):
        with self.assertRaisesMessage(
            ValueError, "Circular filter dependencies detected involving:"
        ):

            class Broken(FilterSet):
                a = CharFilter(depends_on=["c"])
                b = CharFilter(depends_on=["a"])
                c = CharFilter(depends_on=["b"])

                class Meta:
                    model = User
                    fields = []

    def test_invalid_on_missing_dependency_policy(self):
        with self.assertRaisesMessage(
            ValueError, "Invalid on_missing_dependency"
        ):
            CharFilter(on_missing_dependency="explode")


class ExecutionOrderTests(TestCase):
    def test_dependency_runs_first_regardless_of_declaration_order(self):
        calls = []
        F = region_stock_filterset(calls)
        f = F({"region": "north", "stock": "x"}, queryset=User.objects.none())

        f.qs

        self.assertEqual(calls, ["region", "stock:north"])
        self.assertEqual(
            f.filter_contexts["region"],
            {"region": "north", "warehouses": ["north-wh"]},
        )

    def test_declaration_order_kept_when_multiple_orders_valid(self):
        calls = []
        F = recording_filterset(
            [("x", ()), ("y", ()), ("z", ("x",))], calls
        )

        self.assertEqual(F._filter_execution_order, ["x", "y", "z"])

        F({"x": "1", "y": "1", "z": "1"}, queryset=User.objects.none()).qs
        self.assertEqual(calls, ["x", "y", "z"])

    def test_diamond_dependencies_run_each_filter_exactly_once(self):
        calls = []
        F = recording_filterset(
            [
                ("a", ()),
                ("b", ("a",)),
                ("c", ("a",)),
                ("d", ("b", "c")),
            ],
            calls,
        )

        self.assertEqual(F._filter_execution_order, ["a", "b", "c", "d"])

        F(
            {"a": "1", "b": "1", "c": "1", "d": "1"},
            queryset=User.objects.none(),
        ).qs
        self.assertEqual(calls, ["a", "b", "c", "d"])

    def test_legacy_filterset_without_dependencies_keeps_order(self):
        calls = []
        F = recording_filterset(
            [("first", ()), ("second", ()), ("third", ())], calls
        )

        self.assertEqual(
            F._filter_execution_order, ["first", "second", "third"]
        )

        F(
            {"first": "1", "second": "1", "third": "1"},
            queryset=User.objects.none(),
        ).qs
        self.assertEqual(calls, ["first", "second", "third"])

    def test_generated_filters_keep_their_order(self):
        class Legacy(FilterSet):
            class Meta:
                model = User
                fields = ["username", "status"]

        self.assertEqual(
            Legacy._filter_execution_order, list(Legacy.base_filters)
        )


class InheritanceTests(TestCase):
    def test_multilevel_inheritance_and_override(self):
        calls = []

        class Base(FilterSet):
            stock = CharFilter(method="record", depends_on=["region"])
            region = CharFilter(method="record")

            class Meta:
                model = User
                fields = []

            def record(self, qs, name, value):
                calls.append(name)
                return qs

        class Child(Base):
            # Overriding a filter must not break the dependency validation
            # of the final filter set.
            region = RegionFilter(method="record")

        class GrandChild(Child):
            audit = CharFilter(method="record", depends_on=["stock"])

        self.assertEqual(
            GrandChild._filter_execution_order, ["region", "stock", "audit"]
        )

        GrandChild(
            {"region": "r", "stock": "1", "audit": "1"},
            queryset=User.objects.none(),
        ).qs
        self.assertEqual(calls, ["region", "stock", "audit"])

    def test_override_may_drop_a_dependency(self):
        class Base(FilterSet):
            stock = CharFilter(method="record", depends_on=["region"])
            region = CharFilter(method="record")

            class Meta:
                model = User
                fields = []

            def record(self, qs, name, value):
                return qs

        class Child(Base):
            stock = CharFilter(method="record")

        # Without dependencies the declaration order is preserved.
        self.assertEqual(Child._filter_execution_order, ["stock", "region"])

    def test_subclass_dependency_validated_against_final_filters(self):
        class Base(FilterSet):
            region = CharFilter()

            class Meta:
                model = User
                fields = []

        with self.assertRaisesMessage(
            ValueError, "Filter 'stock' depends on unknown filter 'warehouse'."
        ):

            class Broken(Base):
                stock = CharFilter(depends_on=["warehouse"])


class MissingDependencyPolicyTests(TestCase):
    def test_missing_prerequisite_skips_dependent_by_default(self):
        calls = []
        F = region_stock_filterset(calls)
        f = F({"stock": "x"}, queryset=User.objects.none())

        f.qs

        # The dependent never sees an empty context: it is skipped entirely.
        self.assertEqual(calls, [])
        self.assertEqual(f.filter_contexts, {})

    def test_invalid_prerequisite_skips_dependent(self):
        calls = []

        class F(FilterSet):
            region = NumberFilter(method="filter_region")
            stock = CharFilter(method="filter_stock", depends_on=["region"])

            class Meta:
                model = User
                fields = []

            def filter_region(self, qs, name, value):
                calls.append("region")
                return qs

            def filter_stock(self, qs, name, value):
                calls.append("stock")
                return qs

        f = F({"region": "not-a-number", "stock": "x"},
              queryset=User.objects.none())
        f.qs

        self.assertFalse(f.is_valid())
        self.assertEqual(calls, [])
        self.assertEqual(f.filter_contexts, {})

    def test_error_policy_raises_readable_error(self):
        calls = []
        F = region_stock_filterset(calls, stock_policy="error")
        f = F({"stock": "x"}, queryset=User.objects.none())

        with self.assertRaisesMessage(
            FilterDependencyError,
            "Filter 'stock' cannot be applied because its dependencies were "
            "not provided or failed validation: 'region'.",
        ):
            f.qs

        self.assertEqual(calls, [])


class SingleEvaluationTests(TestCase):
    def test_each_filter_runs_at_most_once_per_evaluation(self):
        calls = []
        F = region_stock_filterset(calls)
        f = F({"region": "north", "stock": "x"}, queryset=User.objects.none())

        f.qs
        f.qs

        self.assertEqual(calls, ["region", "stock:north"])

    def test_two_instances_used_in_parallel(self):
        calls = []
        F = region_stock_filterset(calls)
        f1 = F({"region": "north", "stock": "x"}, queryset=User.objects.none())
        f2 = F({"region": "south", "stock": "y"}, queryset=User.objects.none())

        f1.qs
        f2.qs
        f1.qs
        f2.qs

        self.assertEqual(
            calls, ["region", "stock:north", "region", "stock:south"]
        )
        self.assertIsNot(f1.filter_contexts, f2.filter_contexts)
        self.assertEqual(f1.filter_contexts["region"]["region"], "north")
        self.assertEqual(f2.filter_contexts["region"]["region"], "south")
