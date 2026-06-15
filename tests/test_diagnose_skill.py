import json
from unittest.mock import patch

from skills import SKILL_REGISTRY, DiagnoseSkill


class TestDiagnoseSkillRegistration:
    def test_skill_registered(self):
        skill = SKILL_REGISTRY.get("diagnose")
        assert skill is not None
        assert isinstance(skill, DiagnoseSkill)
        assert skill.name == "diagnose"
        assert skill.version == "1.0.0"


def _mock_resolve_workload(pod_fixture):
    """Create a mock resolver that returns exact match with given pod data."""
    return {
        "status": "exact",
        "best_guess": {
            "name": pod_fixture["metadata"]["name"],
            "namespace": pod_fixture["metadata"].get("namespace", "default"),
            "kind": "pod",
            "raw": pod_fixture,
        },
    }


class TestDiagnoseSkillCategorization:
    @patch("skills.diagnose_skill.Resolver.resolve_workload")
    @patch("skills.diagnose_skill.get_pod_events")
    def test_imagepullbackoff(self, mock_get_events, mock_resolve,
                              pod_pending_imagepullbackoff, events_back_off_pull):
        mock_resolve.return_value = _mock_resolve_workload(pod_pending_imagepullbackoff)
        mock_get_events.return_value = events_back_off_pull

        skill = DiagnoseSkill()
        result = skill.run({"name": "broken-pod", "namespace": "default"}, {})

        assert result.status == "success"
        assert result.data["category"] == "ImagePull"
        assert "broken-pod" in result.message

    @patch("skills.diagnose_skill.Resolver.resolve_workload")
    @patch("skills.diagnose_skill.get_pod_events")
    def test_crashloopbackoff(self, mock_get_events, mock_resolve,
                              pod_crashloopbackoff):
        mock_resolve.return_value = _mock_resolve_workload(pod_crashloopbackoff)
        mock_get_events.return_value = {"items": []}

        skill = DiagnoseSkill()
        result = skill.run({"name": "crashy", "namespace": "default"}, {})

        assert result.status == "success"
        assert result.data["category"] == "CrashLoop"

    @patch("skills.diagnose_skill.Resolver.resolve_workload")
    @patch("skills.diagnose_skill.get_pod_events")
    def test_oomkilled(self, mock_get_events, mock_resolve, pod_oomkilled):
        mock_resolve.return_value = _mock_resolve_workload(pod_oomkilled)
        mock_get_events.return_value = {"items": []}

        skill = DiagnoseSkill()
        result = skill.run({"name": "hungry", "namespace": "default"}, {})

        assert result.status == "success"
        assert result.data["category"] == "OOM"

    @patch("skills.diagnose_skill.Resolver.resolve_workload")
    @patch("skills.diagnose_skill.get_pod_events")
    def test_scheduling_failure(self, mock_get_events, mock_resolve,
                                pod_pending_unschedulable, events_failed_scheduling):
        mock_resolve.return_value = _mock_resolve_workload(pod_pending_unschedulable)
        mock_get_events.return_value = events_failed_scheduling

        skill = DiagnoseSkill()
        result = skill.run({"name": "alertmanager-0", "namespace": "default"}, {})

        assert result.status == "success"
        assert result.data["category"] == "Scheduling"

    @patch("skills.diagnose_skill.Resolver.resolve_workload")
    @patch("skills.diagnose_skill.get_pod_events")
    def test_volume_issue(self, mock_get_events, mock_resolve,
                          pod_pending_pvc_unbound, events_failed_mount):
        mock_resolve.return_value = _mock_resolve_workload(pod_pending_pvc_unbound)
        mock_get_events.return_value = events_failed_mount

        skill = DiagnoseSkill()
        result = skill.run({"name": "stuck", "namespace": "default"}, {})

        assert result.status == "success"
        assert result.data["category"] == "Volume"

    @patch("skills.diagnose_skill.Resolver.resolve_workload")
    @patch("skills.diagnose_skill.get_pod_events")
    def test_config_error(self, mock_get_events, mock_resolve, pod_config_error):
        mock_resolve.return_value = _mock_resolve_workload(pod_config_error)
        mock_get_events.return_value = {"items": []}

        skill = DiagnoseSkill()
        result = skill.run({"name": "misconfigured", "namespace": "default"}, {})

        assert result.status == "success"
        assert result.data["category"] == "Config"

    @patch("skills.diagnose_skill.Resolver.resolve_workload")
    @patch("skills.diagnose_skill.get_pod_events")
    def test_init_container_failure(self, mock_get_events, mock_resolve,
                                    pod_init_container_failing):
        mock_resolve.return_value = _mock_resolve_workload(pod_init_container_failing)
        mock_get_events.return_value = {"items": []}

        skill = DiagnoseSkill()
        result = skill.run({"name": "app", "namespace": "default"}, {})

        assert result.status == "success"
        assert result.data["category"] == "Init"

    @patch("skills.diagnose_skill.Resolver.resolve_workload")
    @patch("skills.diagnose_skill.get_pod_events")
    def test_healthy_pod_returns_unknown(self, mock_get_events, mock_resolve,
                                         pod_running_healthy):
        mock_resolve.return_value = _mock_resolve_workload(pod_running_healthy)
        mock_get_events.return_value = {"items": []}

        skill = DiagnoseSkill()
        result = skill.run({"name": "happy", "namespace": "default"}, {})

        assert result.status == "success"
        assert result.data["category"] == "Unknown"

    def test_missing_name_returns_error(self):
        skill = DiagnoseSkill()
        result = skill.run({}, {})
        assert result.status == "error"
        assert "Missing resource name" in result.message

    @patch("skills.diagnose_skill.Resolver.resolve_workload")
    @patch("skills.diagnose_skill.get_pod_events")
    @patch("skills.diagnose_skill.get_pvc_status")
    def test_pvc_status_included(self, mock_get_pvc, mock_get_events, mock_resolve,
                                 pod_pending_pvc_unbound):
        mock_resolve.return_value = _mock_resolve_workload(pod_pending_pvc_unbound)
        mock_get_events.return_value = {"items": []}
        mock_get_pvc.return_value = {
            "status": {"phase": "Pending"},
            "metadata": {"name": "my-data-pvc"},
        }

        skill = DiagnoseSkill()
        result = skill.run({"name": "stuck", "namespace": "default"}, {})

        assert result.status == "success"
        assert result.data["category"] == "Volume"
        pods_data = result.data.get("all_pods", [])
        assert len(pods_data) > 0
        assert len(pods_data[0].get("pvc_statuses", [])) > 0


class TestDiagnoseSkillDirectPod:
    @patch("skills.diagnose_skill.Resolver.resolve_workload")
    @patch("skills.diagnose_skill.get_pod_events")
    def test_direct_pod_with_kind(self, mock_get_events, mock_resolve,
                                  pod_crashloopbackoff):
        mock_resolve.return_value = {
            "status": "exact",
            "best_guess": {
                "name": "crashy",
                "namespace": "default",
                "kind": "pod",
                "raw": pod_crashloopbackoff,
            },
        }
        mock_get_events.return_value = {"items": []}

        skill = DiagnoseSkill()
        result = skill.run({"name": "crashy", "kind": "pod", "namespace": "default"}, {})

        assert result.status == "success"
        assert result.data["category"] == "CrashLoop"
