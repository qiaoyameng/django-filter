from django import forms
from django.db.models import Q
from django.http import QueryDict
from django.test import TestCase

from django_filters.exceptions import ConditionTreeError
from django_filters.filters import (
    BaseInFilter,
    CharFilter,
    Filter,
    NumberFilter,
)
from django_filters.filterset import FilterSet

from .models import Book, User


class UserSearchFilterSet(FilterSet):
    username = CharFilter(lookup_expr="icontains")
    status = NumberFilter()
    username_in = BaseInFilter(field_name="username")
    books = CharFilter(field_name="favorite_books__title", lookup_expr="icontains")
    books_distinct = CharFilter(
        field_name="favorite_books__title",
        lookup_expr="icontains",
        distinct=True,
    )
    inactive_username = CharFilter(
        field_name="username", lookup_expr="icontains", exclude=True
    )

    class Meta:
        model = User
        fields = []


class ConditionTreeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alex = User.objects.create(username="alex", status=1)
        cls.aaron = User.objects.create(username="aaron", status=0)
        cls.jacob = User.objects.create(username="jacob", status=1)
        cls.carl = User.objects.create(username="carl", status=0)

    def test_leaf(self):
        fs = UserSearchFilterSet(
            queryset=User.objects.all(),
            conditions={"filter": "username", "value": "ale"},
        )
        self.assertQuerySetEqual(fs.qs, ["alex"], lambda u: u.username)

    def test_published_and_word_or_author_list(self):
        # (status = 1 AND username contains "al") OR username in [aaron]
        tree = {
            "or": [
                {
                    "and": [
                        {"filter": "status", "value": 1},
                        {"filter": "username", "value": "al"},
                    ]
                },
                {"filter": "username_in", "value": [self.aaron.username]},
            ]
        }
        fs = UserSearchFilterSet(queryset=User.objects.all(), conditions=tree)
        self.assertQuerySetEqual(
            fs.qs.order_by("pk"),
            ["alex", "aaron"],
            lambda u: u.username,
        )

    def test_nested_negation(self):
        users = User.objects.all()

        tree = {"not": {"not": {"filter": "username", "value": "ale"}}}
        fs = UserSearchFilterSet(queryset=users, conditions=tree)
        self.assertQuerySetEqual(fs.qs, ["alex"], lambda u: u.username)

        # NOT A AND NOT B == NOT (A OR B)
        left = {
            "and": [
                {"not": {"filter": "username", "value": "ale"}},
                {"not": {"filter": "username", "value": "jac"}},
            ]
        }
        right = {
            "not": {
                "or": [
                    {"filter": "username", "value": "ale"},
                    {"filter": "username", "value": "jac"},
                ]
            }
        }
        expected = ["aaron", "carl"]
        for tree in (left, right):
            fs = UserSearchFilterSet(queryset=User.objects.all(), conditions=tree)
            self.assertQuerySetEqual(
                fs.qs.order_by("username"), expected, lambda u: u.username
            )

        # Excluded filters are negated leaves: NOT (NOT username-icontains).
        fs = UserSearchFilterSet(
            queryset=User.objects.all(),
            conditions={"not": {"filter": "inactive_username", "value": "ale"}},
        )
        self.assertQuerySetEqual(fs.qs, ["alex"], lambda u: u.username)

    def test_empty_groups(self):
        all_users = {self.alex, self.aaron, self.jacob, self.carl}

        fs = UserSearchFilterSet(
            queryset=User.objects.all(), conditions={"and": []}
        )
        self.assertEqual(set(fs.qs), all_users)

        fs = UserSearchFilterSet(
            queryset=User.objects.all(), conditions={"or": []}
        )
        self.assertQuerySetEqual(fs.qs, [], lambda u: u.username)

        # Empty groups are neutral inside larger trees.
        fs = UserSearchFilterSet(
            queryset=User.objects.all(),
            conditions={
                "and": [
                    {"or": []},
                    {"filter": "username", "value": "ale"},
                ]
            },
        )
        self.assertQuerySetEqual(fs.qs, [], lambda u: u.username)

    def test_repeated_relation_produces_duplicates(self):
        django_book = Book.objects.create(
            title="Django", price="1.00", average_rating=3.0
        )
        unleashed = Book.objects.create(
            title="Django Unleashed", price="1.00", average_rating=3.0
        )
        other = Book.objects.create(
            title="Other", price="1.00", average_rating=3.0
        )
        self.alex.favorite_books.set([django_book, unleashed, other])

        tree = {"filter": "books", "value": "django"}
        fs = UserSearchFilterSet(queryset=User.objects.all(), conditions=tree)
        self.assertEqual(list(fs.qs.values_list("pk", flat=True)), [self.alex.pk] * 2)

        # A distinct=True filter makes the compiled tree apply DISTINCT.
        fs = UserSearchFilterSet(
            queryset=User.objects.all(),
            conditions={"filter": "books_distinct", "value": "django"},
        )
        self.assertQuerySetEqual(fs.qs, ["alex"], lambda u: u.username)

    def test_invalid_value_does_not_query(self):
        with self.assertNumQueries(0):
            with self.assertRaises(ConditionTreeError) as ctx:
                UserSearchFilterSet(
                    queryset=User.objects.all(),
                    conditions={"filter": "status", "value": "not-a-number"},
                )
        self.assertEqual(ctx.exception.path, ("value",))

    def test_error_locates_nested_position(self):
        tree = {
            "and": [
                {"filter": "username", "value": "x"},
                {"or": [{"filter": "nope", "value": 1}]},
            ]
        }
        with self.assertNumQueries(0):
            with self.assertRaises(ConditionTreeError) as ctx:
                UserSearchFilterSet(queryset=User.objects.all(), conditions=tree)
        self.assertEqual(ctx.exception.path, ("and", 1, "or", 0, "filter"))
        self.assertIn("and[1].or[0].filter", ctx.exception.messages[0])

    def test_method_filter_rejected_before_query(self):
        class MethodFilterSet(FilterSet):
            username = CharFilter(method="filter_username")

            class Meta:
                model = User
                fields = []

            def filter_username(self, queryset, name, value):
                return queryset.filter(username__icontains=value)

        with self.assertNumQueries(0):
            with self.assertRaises(ConditionTreeError) as ctx:
                MethodFilterSet(
                    queryset=User.objects.all(),
                    conditions={"filter": "username", "value": "ale"},
                )
        self.assertEqual(ctx.exception.path, ())
        self.assertIn("custom method", ctx.exception.messages[0])

        # The same filter still works through ordinary query parameters.
        fs = MethodFilterSet(
            data=QueryDict("username=ale"), queryset=User.objects.all()
        )
        self.assertQuerySetEqual(fs.qs, ["alex"], lambda u: u.username)

    def test_explicit_composable_method_filter(self):
        class IExactUsernameFilter(Filter):
            field_class = forms.CharField

            def get_q(self, value):
                return Q(username__iexact=value)

        class ComposableFilterSet(FilterSet):
            username = IExactUsernameFilter(field_name="username", method="noop")

            class Meta:
                model = User
                fields = []

            def noop(self, queryset, name, value):
                return queryset

        fs = ComposableFilterSet(
            queryset=User.objects.all(),
            conditions={"filter": "username", "value": "ALEX"},
        )
        self.assertQuerySetEqual(fs.qs, ["alex"], lambda u: u.username)

    def test_depth_limit(self):
        tree = {"filter": "username", "value": "x"}
        for _ in range(8):
            tree = {"not": tree}
        with self.assertRaises(ConditionTreeError) as ctx:
            UserSearchFilterSet(queryset=User.objects.all(), conditions=tree)
        self.assertIn("maximum depth", ctx.exception.messages[0])

    def test_node_limit(self):
        class LimitedFilterSet(UserSearchFilterSet):
            condition_tree_max_nodes = 4

        tree = {
            "and": [
                {"filter": "username", "value": "a"},
                {"filter": "username", "value": "b"},
                {"filter": "username", "value": "c"},
                {"filter": "username", "value": "d"},
            ]
        }
        with self.assertNumQueries(0):
            with self.assertRaises(ConditionTreeError) as ctx:
                LimitedFilterSet(queryset=User.objects.all(), conditions=tree)
        self.assertIn("maximum of 4 nodes", ctx.exception.messages[0])

    def test_plain_query_parameters_still_work(self):
        fs = UserSearchFilterSet(
            data=QueryDict("username=al"),
            queryset=User.objects.all(),
            conditions={"filter": "status", "value": 1},
        )
        self.assertTrue(fs.is_valid())
        self.assertQuerySetEqual(fs.qs, ["alex"], lambda u: u.username)

        # Conditions are optional.
        fs = UserSearchFilterSet(
            data=QueryDict("username=al"), queryset=User.objects.all()
        )
        self.assertQuerySetEqual(fs.qs, ["alex"], lambda u: u.username)

    def test_requests_are_independent(self):
        first = UserSearchFilterSet(
            queryset=User.objects.all(),
            conditions={"filter": "username", "value": "ale"},
        )
        second = UserSearchFilterSet(
            queryset=User.objects.all(),
            conditions={"filter": "username", "value": "jac"},
        )
        self.assertQuerySetEqual(first.qs, ["alex"], lambda u: u.username)
        self.assertQuerySetEqual(second.qs, ["jacob"], lambda u: u.username)
        self.assertFalse(hasattr(UserSearchFilterSet, "_conditions_q"))

    def test_json_string(self):
        fs = UserSearchFilterSet(
            queryset=User.objects.all(),
            conditions='{"filter": "username", "value": "ale"}',
        )
        self.assertQuerySetEqual(fs.qs, ["alex"], lambda u: u.username)

        with self.assertRaises(ConditionTreeError):
            UserSearchFilterSet(
                queryset=User.objects.all(), conditions="{not json"
            )

    def test_unbound_filterset_with_conditions(self):
        fs = UserSearchFilterSet(
            queryset=User.objects.all(),
            conditions={"filter": "username", "value": "ale"},
        )
        self.assertFalse(fs.is_bound)
        self.assertQuerySetEqual(fs.qs, ["alex"], lambda u: u.username)

    def test_malformed_tree(self):
        bad_trees = [
            ["filter", "username"],
            {"filter": "username", "value": "x", "bogus": 1},
            {"filter": 1, "value": "x"},
            {"filter": "username"},
            {"and": {"filter": "username", "value": "x"}},
            {"xor": []},
            {"and": [], "or": []},
        ]
        for tree in bad_trees:
            with self.subTest(tree=tree):
                with self.assertRaises(ConditionTreeError):
                    UserSearchFilterSet(queryset=User.objects.all(), conditions=tree)

    def test_empty_leaf_value_rejected(self):
        with self.assertRaises(ConditionTreeError) as ctx:
            UserSearchFilterSet(
                queryset=User.objects.all(),
                conditions={"filter": "username", "value": ""},
            )
        self.assertEqual(ctx.exception.path, ("value",))
