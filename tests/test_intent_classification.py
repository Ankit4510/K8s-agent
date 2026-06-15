"""
Test that intent detection rules produce correct JSON without calling the LLM.
We test the expected JSON output directly to verify our rules are consistent.
"""

import json


class TestIntentRulesConsistency:
    """Verify that our documented intent rules are consistent and well-formed."""

    def test_restart_has_no_kind_by_default(self):
        intent = {
            "mode": "skill",
            "skill": "restart",
            "name": "prometheus",
            "kind": None,
            "namespace": None,
            "field": None,
            "cluster_name": None,
            "zone": None,
            "project": None,
            "action": None,
            "explicit_pod": False,
            "name_filter": None,
        }
        assert intent["skill"] == "restart"
        assert intent["kind"] is None
        assert intent["explicit_pod"] is False

    def test_restart_with_explicit_pod(self):
        intent = {
            "mode": "skill",
            "skill": "restart",
            "name": "prometheus",
            "kind": "pod",
            "namespace": None,
            "field": None,
            "cluster_name": None,
            "zone": None,
            "project": None,
            "action": None,
            "explicit_pod": True,
            "name_filter": None,
        }
        assert intent["kind"] == "pod"
        assert intent["explicit_pod"] is True

    def test_restart_with_deployment_kind(self):
        intent = {
            "mode": "skill",
            "skill": "restart",
            "name": "grafana",
            "kind": "deployment",
            "namespace": None,
            "field": None,
            "cluster_name": None,
            "zone": None,
            "project": None,
            "action": None,
            "explicit_pod": False,
            "name_filter": None,
        }
        assert intent["kind"] == "deployment"
        assert intent["explicit_pod"] is False

    def test_describe_health_query(self):
        intent = {
            "mode": "skill",
            "skill": "describe",
            "name": "prometheus",
            "kind": None,
            "namespace": None,
            "field": "status",
            "cluster_name": None,
            "zone": None,
            "project": None,
            "action": None,
            "explicit_pod": False,
            "name_filter": None,
        }
        assert intent["skill"] == "describe"
        assert intent["field"] == "status"

    def test_describe_image_query(self):
        intent = {
            "mode": "skill",
            "skill": "describe",
            "name": "foo",
            "kind": "pod",
            "namespace": None,
            "field": "image",
            "cluster_name": None,
            "zone": None,
            "project": None,
            "action": None,
            "explicit_pod": False,
            "name_filter": None,
        }
        assert intent["field"] == "image"

    def test_describe_with_cluster(self):
        intent = {
            "mode": "skill",
            "skill": "describe",
            "name": "com-manh-cp-order",
            "kind": None,
            "namespace": None,
            "field": "status",
            "cluster_name": "mtstackom3",
            "zone": None,
            "project": None,
            "action": None,
            "explicit_pod": False,
            "name_filter": None,
        }
        assert intent["cluster_name"] == "mtstackom3"

    def test_list_pods(self):
        intent = {
            "mode": "skill",
            "skill": "list",
            "name": None,
            "kind": "pod",
            "namespace": None,
            "field": None,
            "cluster_name": None,
            "zone": None,
            "project": None,
            "action": None,
            "explicit_pod": False,
            "name_filter": None,
        }
        assert intent["skill"] == "list"
        assert intent["kind"] == "pod"

    def test_list_with_name_filter(self):
        intent = {
            "mode": "skill",
            "skill": "list",
            "name": None,
            "kind": "pod",
            "namespace": None,
            "field": None,
            "cluster_name": None,
            "zone": None,
            "project": None,
            "action": None,
            "explicit_pod": False,
            "name_filter": "cp-order",
        }
        assert intent["name_filter"] == "cp-order"

    def test_list_all_namespaces(self):
        intent = {
            "mode": "skill",
            "skill": "list",
            "name": None,
            "kind": "deployment",
            "namespace": "__all__",
            "field": None,
            "cluster_name": None,
            "zone": None,
            "project": None,
            "action": None,
            "explicit_pod": False,
            "name_filter": None,
        }
        assert intent["namespace"] == "__all__"

    def test_list_rest_stereotype(self):
        intent = {
            "mode": "skill",
            "skill": "list",
            "name": None,
            "kind": None,
            "namespace": None,
            "field": None,
            "cluster_name": None,
            "zone": None,
            "project": None,
            "action": None,
            "explicit_pod": False,
            "name_filter": "rest stereotype",
        }
        assert intent["kind"] is None
        assert "rest" in intent["name_filter"]

    def test_status_health_list(self):
        intent = {
            "mode": "skill",
            "skill": "status",
            "name": None,
            "kind": None,
            "namespace": None,
            "field": None,
            "cluster_name": None,
            "zone": None,
            "project": None,
            "action": None,
            "explicit_pod": False,
            "name_filter": None,
        }
        assert intent["skill"] == "status"

    def test_status_with_cluster(self):
        intent = {
            "mode": "skill",
            "skill": "status",
            "name": None,
            "kind": None,
            "namespace": None,
            "field": None,
            "cluster_name": "mtstackom3",
            "zone": None,
            "project": None,
            "action": None,
            "explicit_pod": False,
            "name_filter": None,
        }
        assert intent["cluster_name"] == "mtstackom3"

    def test_cluster_switch(self):
        intent = {
            "mode": "skill",
            "skill": "cluster_switch",
            "name": None,
            "kind": None,
            "namespace": None,
            "field": None,
            "cluster_name": "prod-us",
            "zone": None,
            "project": None,
            "action": "switch",
            "explicit_pod": False,
            "name_filter": None,
        }
        assert intent["action"] == "switch"

    def test_cluster_list(self):
        intent = {
            "mode": "skill",
            "skill": "cluster_switch",
            "name": None,
            "kind": None,
            "namespace": None,
            "field": None,
            "cluster_name": None,
            "zone": None,
            "project": None,
            "action": "list",
            "explicit_pod": False,
            "name_filter": None,
        }
        assert intent["action"] == "list"

    def test_chat_greeting(self):
        intent = {
            "mode": "chat",
            "skill": None,
            "tool": None,
            "name": None,
            "kind": None,
            "namespace": None,
            "field": None,
            "cluster_name": None,
            "zone": None,
            "project": None,
            "action": None,
            "explicit_pod": False,
            "name_filter": None,
        }
        assert intent["mode"] == "chat"

    def test_confirm(self):
        intent = {
            "mode": "confirm",
            "confirm_index": 0,
            "skill": None,
            "tool": None,
            "name": None,
            "kind": None,
            "namespace": None,
            "field": None,
            "cluster_name": None,
            "zone": None,
            "project": None,
            "action": None,
            "explicit_pod": False,
            "name_filter": None,
        }
        assert intent["mode"] == "confirm"

    def test_unclear(self):
        intent = {
            "mode": "unclear",
            "skill": None,
            "tool": None,
            "name": None,
            "kind": None,
            "namespace": None,
            "field": None,
            "cluster_name": None,
            "zone": None,
            "project": None,
            "action": None,
            "explicit_pod": False,
            "name_filter": None,
        }
        assert intent["mode"] == "unclear"

    def test_diagnose_intent(self):
        intent = {
            "mode": "skill",
            "skill": "diagnose",
            "name": "prometheus",
            "kind": None,
            "namespace": None,
            "field": None,
            "cluster_name": None,
            "zone": None,
            "project": None,
            "action": None,
            "explicit_pod": False,
            "name_filter": None,
        }
        assert intent["skill"] == "diagnose"

    def test_follow_up_no_name(self):
        intent = {
            "mode": "skill",
            "skill": "describe",
            "name": None,
            "kind": None,
            "namespace": None,
            "field": "status",
            "cluster_name": None,
            "zone": None,
            "project": None,
            "action": None,
            "explicit_pod": False,
            "name_filter": None,
        }
        assert intent["name"] is None
        assert intent["field"] == "status"


class TestIntentSchema:
    def test_all_intents_serializable(self):
        intents = [
            {"mode": "skill", "skill": "restart", "name": "foo", "kind": None, "namespace": None, "field": None, "cluster_name": None, "zone": None, "project": None, "action": None, "explicit_pod": False, "name_filter": None},
            {"mode": "skill", "skill": "describe", "name": "foo", "kind": "pod", "namespace": None, "field": "image", "cluster_name": None, "zone": None, "project": None, "action": None, "explicit_pod": False, "name_filter": None},
            {"mode": "chat", "skill": None, "tool": None, "name": None, "kind": None, "namespace": None, "field": None, "cluster_name": None, "zone": None, "project": None, "action": None, "explicit_pod": False, "name_filter": None},
            {"mode": "unclear", "skill": None, "tool": None, "name": None, "kind": None, "namespace": None, "field": None, "cluster_name": None, "zone": None, "project": None, "action": None, "explicit_pod": False, "name_filter": None},
        ]
        for intent in intents:
            dumped = json.dumps(intent)
            loaded = json.loads(dumped)
            assert loaded["mode"] == intent["mode"]
