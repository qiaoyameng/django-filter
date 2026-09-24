import copy

from django.test import TestCase

from django_filters import And, FilterSet, Leaf, Not, Or
from django_filters.filters import (
    BaseInFilter,
    BooleanFilter,
    CharFilter,
    Filter,
    NumberFilter,
)

from .models import Book, Comment, User


class CharInFilter(BaseInFilter, CharFilter):
    pass


class UserFilter(FilterSet):
    name = CharFilter(field_name="username")
    username = CharFilter(field_name="username", lookup_expr="icontains")
    is_active = BooleanFilter()
    status = NumberFilter()

    class Meta:
        model = User
        fields = []


class ConditionTreeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alex = User.objects.create(username="alex", status=1, is_active=True)
        cls.bob = User.objects.create(username="bobby", status=2, is_active=False)
        cls.carol = User.objects.create(username="carol", status=0, is_active=True)

    def usernames(self, qs):
        return sorted(qs.values_list("username", flat=True))

    def test_or_of_and_group(self):
        # "active and username contains 'a', or status is 2"
        tree = {
            "or": [
                {
                    "and": [
                        {"filter": "is_active", "value": "true"},
                        {"filter": "username", "value": "a"},
                    ]
                },
                {"filter": "status", "value": "2"},
            ]
        }
        f = UserFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertTrue(f.is_valid(), f.condition_tree_errors)
        self.assertEqual(self.usernames(f.qs), ["alex", "bobby", "carol"])

    def test_or_of_and_group_narrowed(self):
        tree = {
            "or": [
                {
                    "and": [
                        {"filter": "is_active", "value": "true"},
                        {"filter": "username", "value": "alex"},
                    ]
                },
                {"filter": "status", "value": "2"},
            ]
        }
        f = UserFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertEqual(self.usernames(f.qs), ["alex", "bobby"])

    def test_nested_negation(self):
        tree = {"not": {"not": {"filter": "name", "value": "alex"}}}
        f = UserFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertTrue(f.is_valid(), f.condition_tree_errors)
        self.assertEqual(self.usernames(f.qs), ["alex"])

    def test_single_negation(self):
        tree = {"not": {"filter": "name", "value": "alex"}}
        f = UserFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertEqual(self.usernames(f.qs), ["bobby", "carol"])

    def test_negation_of_group(self):
        tree = {
            "not": {
                "or": [
                    {"filter": "name", "value": "alex"},
                    {"filter": "name", "value": "bobby"},
                ]
            }
        }
        f = UserFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertEqual(self.usernames(f.qs), ["carol"])

    def test_empty_groups_are_noops(self):
        for tree in (
            {"and": []},
            {"or": []},
            {"not": {"and": []}},
            {"and": [{"or": []}, {"and": []}]},
        ):
            f = UserFilter(condition_tree=tree, queryset=User.objects.all())
            self.assertTrue(f.is_valid(), f.condition_tree_errors)
            self.assertEqual(self.usernames(f.qs), ["alex", "bobby", "carol"])

    def test_empty_group_inside_group(self):
        tree = {"and": [{"or": []}, {"filter": "name", "value": "alex"}]}
        f = UserFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertEqual(self.usernames(f.qs), ["alex"])

    def test_empty_leaf_value_is_noop(self):
        tree = {"and": [{"filter": "username", "value": ""}]}
        f = UserFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertTrue(f.is_valid(), f.condition_tree_errors)
        self.assertEqual(self.usernames(f.qs), ["alex", "bobby", "carol"])

    def test_node_objects(self):
        tree = Or(
            And(Leaf("is_active", "true"), Leaf("username", "alex")),
            Not(Leaf("is_active", "true")),
        )
        f = UserFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertTrue(f.is_valid(), f.condition_tree_errors)
        self.assertEqual(self.usernames(f.qs), ["alex", "bobby"])

    def test_values_are_cleaned_by_form_fields(self):
        # "false" and "1" are strings; the form fields must coerce them.
        tree = {
            "and": [
                {"filter": "is_active", "value": "false"},
                {"filter": "status", "value": "2"},
            ]
        }
        f = UserFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertTrue(f.is_valid(), f.condition_tree_errors)
        self.assertEqual(self.usernames(f.qs), ["bobby"])

    def test_normal_query_params_still_work(self):
        tree = {"filter": "is_active", "value": "true"}
        f = UserFilter(
            data={"username": "a"}, condition_tree=tree, queryset=User.objects.all()
        )
        self.assertTrue(f.is_valid(), f.condition_tree_errors)
        self.assertEqual(self.usernames(f.qs), ["alex", "carol"])

    def test_unknown_filter_rejected(self):
        tree = {"or": [{"filter": "missing", "value": "x"}]}
        f = UserFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertFalse(f.is_valid())
        self.assertEqual(len(f.condition_tree_errors), 1)
        path, message = f.condition_tree_errors[0]
        self.assertEqual(path, "$.or[0].filter")
        self.assertIn("missing", message)

    def test_user_supplied_lookup_rejected(self):
        tree = {"filter": "status", "value": "1", "lookup_expr": "gt"}
        f = UserFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertFalse(f.is_valid())
        path, message = f.condition_tree_errors[0]
        self.assertEqual(path, "$")
        self.assertIn("lookup_expr", message)

    def test_invalid_value_issues_no_query(self):
        tree = {
            "and": [
                {"filter": "name", "value": "alex"},
                {"filter": "status", "value": "not-a-number"},
            ]
        }
        f = UserFilter(condition_tree=tree, queryset=User.objects.all())
        with self.assertNumQueries(0):
            self.assertFalse(f.is_valid())
            qs = f.qs
        path, message = f.condition_tree_errors[0]
        self.assertEqual(path, "$.and[1].value")
        # The invalid tree is not applied at all.
        self.assertEqual(qs.count(), 3)

    def test_invalid_structure(self):
        for tree in (
            "not a tree",
            {"xor": []},
            {"and": "not-a-list"},
            {"filter": "name"},
            {"value": "alex"},
            {"and": [], "extra": 1},
        ):
            f = UserFilter(condition_tree=tree, queryset=User.objects.all())
            self.assertFalse(f.is_valid(), tree)
            self.assertTrue(f.condition_tree_errors, tree)

    def test_max_depth_enforced(self):
        tree = {"filter": "name", "value": "alex"}
        for _ in range(6):
            tree = {"not": tree}
        f = UserFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertFalse(f.is_valid())
        path, message = f.condition_tree_errors[0]
        self.assertIn("maximum depth", message)

    def test_max_nodes_enforced(self):
        tree = {"and": [{"filter": "name", "value": "alex"}] * 60}
        f = UserFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertFalse(f.is_valid())
        path, message = f.condition_tree_errors[0]
        self.assertIn("maximum of 50 nodes", message)

    def test_limits_are_configurable(self):
        class LimitedFilter(UserFilter):
            condition_tree_max_depth = 2
            condition_tree_max_nodes = 3

        tree = {"and": [{"filter": "name", "value": "alex"}] * 4}
        f = LimitedFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertFalse(f.is_valid())
        self.assertIn("maximum of 3 nodes", f.condition_tree_errors[0][1])

        tree = {"not": {"not": {"filter": "name", "value": "alex"}}}
        f = LimitedFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertFalse(f.is_valid())
        self.assertIn("maximum depth", f.condition_tree_errors[0][1])

    def test_method_filter_rejected_before_query(self):
        class MethodFilter(UserFilter):
            custom = Filter(method="filter_custom")

            def filter_custom(self, qs, value):
                return qs.filter(username=value)

        tree = {"filter": "custom", "value": "alex"}
        f = MethodFilter(condition_tree=tree, queryset=User.objects.all())
        with self.assertNumQueries(0):
            self.assertFalse(f.is_valid())
            f.qs
        path, message = f.condition_tree_errors[0]
        self.assertEqual(path, "$.filter")
        self.assertIn("get_q", message)

    def test_composable_custom_filter(self):
        class LowercaseUsernameFilter(Filter):
            def get_q(self, value):
                from django.db.models import Q

                return Q(username__iexact=value)

        class CustomFilter(UserFilter):
            lower_name = LowercaseUsernameFilter(field_name="username")

        tree = {"filter": "lower_name", "value": "ALEX"}
        f = CustomFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertTrue(f.is_valid(), f.condition_tree_errors)
        self.assertEqual(self.usernames(f.qs), ["alex"])

    def test_non_composable_filter_rejected(self):
        from django_filters.filters import DateRangeFilter

        class OrderedFilter(UserFilter):
            created = DateRangeFilter(field_name="date_joined")

        tree = {"filter": "created", "value": "today"}
        f = OrderedFilter(condition_tree=tree, queryset=User.objects.all())
        with self.assertNumQueries(0):
            self.assertFalse(f.is_valid())
        self.assertIn("cannot be used in a condition tree", f.condition_tree_errors[0][1])

    def test_requests_are_independent(self):
        tree = {"and": [{"filter": "name", "value": "alex"}]}
        original = copy.deepcopy(tree)
        f1 = UserFilter(condition_tree=tree, queryset=User.objects.all())
        f2 = UserFilter(
            condition_tree={"filter": "name", "value": "bobby"},
            queryset=User.objects.all(),
        )
        f3 = UserFilter(queryset=User.objects.all())
        self.assertEqual(self.usernames(f1.qs), ["alex"])
        self.assertEqual(self.usernames(f2.qs), ["bobby"])
        self.assertEqual(self.usernames(f3.qs), ["alex", "bobby", "carol"])
        # The caller's tree is not mutated by compilation.
        self.assertEqual(tree, original)


class DuplicateRelationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alex = User.objects.create(username="alex", is_active=True)
        cls.bob = User.objects.create(username="bob", is_active=True)
        book_a = Book.objects.create(title="Book A", price=1, average_rating=4)
        book_b = Book.objects.create(title="Book B", price=1, average_rating=4)
        cls.alex.favorite_books.add(book_a, book_b)
        cls.bob.favorite_books.add(book_a)

    def test_duplicate_rows_from_to_many_relation(self):
        class BookLoverFilter(FilterSet):
            book = CharFilter(field_name="favorite_books__title")

            class Meta:
                model = User
                fields = []

        tree = {
            "or": [
                {"filter": "book", "value": "Book A"},
                {"filter": "book", "value": "Book B"},
            ]
        }
        f = BookLoverFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertTrue(f.is_valid(), f.condition_tree_errors)
        # alex matches via both books and appears twice.
        self.assertEqual(list(f.qs.values_list("username", flat=True)), ["alex", "alex", "bob"])

    def test_distinct_leaf_deduplicates(self):
        class BookLoverFilter(FilterSet):
            book = CharFilter(field_name="favorite_books__title", distinct=True)

            class Meta:
                model = User
                fields = []

        tree = {
            "or": [
                {"filter": "book", "value": "Book A"},
                {"filter": "book", "value": "Book B"},
            ]
        }
        f = BookLoverFilter(condition_tree=tree, queryset=User.objects.all())
        self.assertTrue(f.is_valid(), f.condition_tree_errors)
        self.assertEqual(sorted(f.qs.values_list("username", flat=True)), ["alex", "bob"])


class AuthorListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import datetime

        cls.alex = User.objects.create(username="alex")
        cls.bob = User.objects.create(username="bob")
        cls.carol = User.objects.create(username="carol")
        date = datetime.date(2026, 1, 1)
        time = datetime.time(12, 0)
        Comment.objects.create(text="hello", author=cls.alex, date=date, time=time)
        Comment.objects.create(text="world", author=cls.bob, date=date, time=time)
        Comment.objects.create(text="!", author=cls.carol, date=date, time=time)

    def test_author_in_list(self):
        class CommentFilter(FilterSet):
            author = CharInFilter(field_name="author__username")

            class Meta:
                model = Comment
                fields = []

        tree = {"filter": "author", "value": ["alex", "bob"]}
        f = CommentFilter(condition_tree=tree, queryset=Comment.objects.all())
        self.assertTrue(f.is_valid(), f.condition_tree_errors)
        self.assertEqual(
            sorted(f.qs.values_list("author__username", flat=True)), ["alex", "bob"]
        )

    def test_author_in_list_invalid_value(self):
        class CommentFilter(FilterSet):
            author = CharInFilter(field_name="author__username")

            class Meta:
                model = Comment
                fields = []

        tree = {"filter": "author", "value": "not-a-list-but-ok-string"}
        f = CommentFilter(condition_tree=tree, queryset=Comment.objects.all())
        # A plain string is not a valid list of values for the CSV field...
        # it is cleaned as a single value, which is fine; the point here is
        # that cleaning, not string interpolation, produces the lookup.
        self.assertTrue(f.is_valid(), f.condition_tree_errors)
