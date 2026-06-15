import json
from unittest.mock import patch

from tools import (
    get_pod_events,
    get_container_waiting_reasons,
    get_pod_pvcs,
    categorize_failure,
    get_resource_yaml,
    get_deployment_pods,
)


class TestGetContainerWaitingReasons:
    def test_crashloopbackoff(self, pod_crashloopbackoff):
        reasons = get_container_waiting_reasons(pod_crashloopbackoff)
        assert len(reasons["containers"]) == 1
        c = reasons["containers"][0]
        assert c["name"] == "app"
        assert c["reason"] == "CrashLoopBackOff"
        assert c["restartCount"] == 15
        assert c["ready"] is False

    def test_healthy_pod(self, pod_running_healthy):
        reasons = get_container_waiting_reasons(pod_running_healthy)
        assert len(reasons["containers"]) == 1
        c = reasons["containers"][0]
        assert c["name"] == "nginx"
        assert c["reason"] == "Running"
        assert c["ready"] is True
        assert c["restartCount"] == 0

    def test_imagepullbackoff(self, pod_pending_imagepullbackoff):
        reasons = get_container_waiting_reasons(pod_pending_imagepullbackoff)
        assert len(reasons["containers"]) == 1
        c = reasons["containers"][0]
        assert c["reason"] == "ImagePullBackOff"
        assert "Back-off pulling image" in c.get("message", "")

    def test_oomkilled_detected_via_last_state(self, pod_oomkilled):
        reasons = get_container_waiting_reasons(pod_oomkilled)
        assert len(reasons["containers"]) == 1
        c = reasons["containers"][0]
        assert c["reason"] == "CrashLoopBackOff"  # current state is CrashLoop, but lastState was OOM

    def test_config_error(self, pod_config_error):
        reasons = get_container_waiting_reasons(pod_config_error)
        assert len(reasons["containers"]) == 1
        c = reasons["containers"][0]
        assert c["reason"] == "CreateContainerConfigError"
        assert "configmap" in c.get("message", "")

    def test_init_container_failing(self, pod_init_container_failing):
        reasons = get_container_waiting_reasons(pod_init_container_failing)
        assert len(reasons["initContainers"]) == 1
        ic = reasons["initContainers"][0]
        assert ic["name"] == "init-db"
        assert ic["reason"] == "CrashLoopBackOff"
        assert len(reasons["containers"]) == 1
        c = reasons["containers"][0]
        assert c["reason"] == "PodInitializing"


class TestGetPodPvcs:
    def test_pod_with_pvc(self, pod_pending_pvc_unbound):
        pvcs = get_pod_pvcs(pod_pending_pvc_unbound)
        assert len(pvcs) == 1
        assert pvcs[0]["claimName"] == "my-data-pvc"
        assert pvcs[0]["volumeName"] == "data"

    def test_pod_without_pvc(self, pod_running_healthy):
        pvcs = get_pod_pvcs(pod_running_healthy)
        assert pvcs == []


class TestCategorizeFailureReadiness:
    def test_running_but_not_ready_is_readiness(self):
        """Container is Running but not ready should be Readiness category."""
        pod = {
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {
                        "name": "app",
                        "ready": False,
                        "restartCount": 0,
                        "state": {"running": {"startedAt": "2026-05-18T12:00:00Z"}},
                    }
                ],
            },
            "spec": {"containers": [{"name": "app", "image": "myapp"}]},
        }
        cat = categorize_failure(pod)
        assert cat == "Readiness"

    def test_running_and_ready_is_unknown(self):
        """Container is Running AND ready should be Unknown (healthy)."""
        pod = {
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {
                        "name": "app",
                        "ready": True,
                        "restartCount": 0,
                        "state": {"running": {"startedAt": "2026-05-18T12:00:00Z"}},
                    }
                ],
            },
            "spec": {"containers": [{"name": "app", "image": "myapp"}]},
        }
        cat = categorize_failure(pod)
        assert cat == "Unknown"


class TestCategorizeFailure:
    def test_unschedulable_is_scheduling(self, pod_pending_unschedulable):
        cat = categorize_failure(pod_pending_unschedulable)
        assert cat == "Scheduling"

    def test_imagepullbackoff_is_imagepull(self, pod_pending_imagepullbackoff):
        cat = categorize_failure(pod_pending_imagepullbackoff)
        assert cat == "ImagePull"

    def test_pvc_unbound_is_volume(self, pod_pending_pvc_unbound):
        cat = categorize_failure(pod_pending_pvc_unbound)
        assert cat == "Volume"

    def test_crashloopbackoff_is_crashloop(self, pod_crashloopbackoff):
        cat = categorize_failure(pod_crashloopbackoff)
        assert cat == "CrashLoop"

    def test_oomkilled_is_oom(self, pod_oomkilled):
        cat = categorize_failure(pod_oomkilled)
        assert cat == "OOM"

    def test_healthy_pod_is_unknown(self, pod_running_healthy):
        cat = categorize_failure(pod_running_healthy)
        assert cat == "Unknown"

    def test_init_failing_is_init(self, pod_init_container_failing):
        cat = categorize_failure(pod_init_container_failing)
        assert cat == "Init"

    def test_config_error_is_config(self, pod_config_error):
        cat = categorize_failure(pod_config_error)
        assert cat == "Config"

    def test_pending_with_no_clear_cause_is_unknown(self):
        pod = {
            "status": {
                "phase": "Pending",
                "conditions": [{"type": "PodScheduled", "status": "True"}],
                "containerStatuses": [
                    {
                        "name": "app",
                        "ready": False,
                        "restartCount": 0,
                        "state": {"waiting": {"reason": "ContainerCreating"}},
                    }
                ],
            },
            "spec": {"containers": [{"name": "app", "image": "nginx"}]},
        }
        cat = categorize_failure(pod)
        assert cat == "Unknown"


class TestGetPodEvents:
    @patch("tools.kubectl_cmd")
    def test_returns_events(self, mock_kubectl, events_failed_scheduling):
        mock_kubectl.return_value = json.dumps(events_failed_scheduling)
        result = get_pod_events("alertmanager-0", "default")
        assert len(result["items"]) == 2
        assert result["items"][0]["reason"] == "FailedScheduling"

    @patch("tools.kubectl_cmd")
    def test_returns_empty_on_error(self, mock_kubectl):
        mock_kubectl.return_value = "Error: timeout"
        result = get_pod_events("nonexistent")
        assert result == {"items": []}


class TestGetResourceYaml:
    @patch("tools.kubectl_cmd")
    def test_returns_parsed_json(self, mock_kubectl, pod_running_healthy):
        mock_kubectl.return_value = json.dumps(pod_running_healthy)
        result = get_resource_yaml("pod", "happy", "default")
        assert result is not None
        assert result["metadata"]["name"] == "happy"

    @patch("tools.kubectl_cmd")
    def test_returns_none_on_error(self, mock_kubectl):
        mock_kubectl.return_value = "Error: not found"
        result = get_resource_yaml("pod", "nonexistent")
        assert result is None


class TestGetDeploymentPods:
    @patch("tools.kubectl_cmd")
    def test_returns_pods(self, mock_kubectl, pod_running_healthy):
        mock_kubectl.return_value = json.dumps({"items": [pod_running_healthy]})
        result = get_deployment_pods("web", "default")
        assert len(result["items"]) == 1
        assert result["items"][0]["metadata"]["name"] == "happy"
