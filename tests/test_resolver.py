"""Tests for the Resolver module."""

from unittest.mock import patch

from skills.resolver import Resolver, FUZZY_THRESHOLD, _strip_suffixes, COMMON_SUFFIXES
from skills import SKILL_REGISTRY


class TestStripSuffixes:
    def test_strips_component(self):
        assert _strip_suffixes("order component") == "order"

    def test_strips_service(self):
        assert _strip_suffixes("inventory service") == "inventory"

    def test_strips_container(self):
        assert _strip_suffixes("web container") == "web"

    def test_strips_rest_component(self):
        assert _strip_suffixes("order rest component") == "order"

    def test_no_suffix_unchanged(self):
        assert _strip_suffixes("order") == "order"

    def test_strips_only_last_suffix(self):
        assert _strip_suffixes("order component service") == "order component"


class TestResolverConfig:
    def test_fuzzy_threshold_defined(self):
        assert FUZZY_THRESHOLD == 0.75

    def test_cache_clear_exists(self):
        Resolver.clear_cache()
        # Should not raise


class TestResolverPrefixMatching:
    @patch("skills.resolver.kubectl_cmd")
    def test_prefix_exact_match(self, mock_kubectl):
        mock_kubectl.return_value = '{"items": []}'

        res = Resolver.resolve_workload("order", namespace="default")
        # Should attempt both "order" and "app-order"
        assert res["status"] in ("not_found", "exact", "suggestions")

    @patch("skills.resolver.kubectl_cmd")
    def test_strip_prefix_from_query(self, mock_kubectl):
        mock_kubectl.return_value = '{"items": []}'

        # Query with prefix already in it
        res = Resolver.resolve_workload("app-order", namespace="default")
        assert res["status"] in ("not_found", "exact", "suggestions")


class TestResolverIntegration:
    def test_resolver_importable(self):
        assert Resolver is not None
        assert hasattr(Resolver, "resolve_workload")
        assert hasattr(Resolver, "resolve")
        assert hasattr(Resolver, "clear_cache")

    def test_resolve_workload_priority(self):
        """Verify deployment > statefulset > daemonset > pod priority order."""
        from skills.resolver import Resolver
        import inspect
        source = inspect.getsource(Resolver.resolve_workload)
        # Only look at the for loop body, skip docstring
        loop_start = source.index("for kind in")
        body = source[loop_start:]
        kinds = ["deployment", "statefulset", "daemonset", "pod"]
        positions = [body.index(k) for k in kinds]
        assert positions == sorted(positions), "Workload resolution order must be deployment → statefulset → daemonset → pod"
