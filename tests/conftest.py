import json
import os
from unittest.mock import patch

import pytest

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name):
    path = os.path.join(FIXTURE_DIR, name)
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def pod_pending_unschedulable():
    return load_fixture("pod_pending_unschedulable.json")


@pytest.fixture
def pod_pending_imagepullbackoff():
    return load_fixture("pod_pending_imagepullbackoff.json")


@pytest.fixture
def pod_pending_pvc_unbound():
    return load_fixture("pod_pending_pvc_unbound.json")


@pytest.fixture
def pod_crashloopbackoff():
    return load_fixture("pod_crashloopbackoff.json")


@pytest.fixture
def pod_oomkilled():
    return load_fixture("pod_oomkilled.json")


@pytest.fixture
def pod_running_healthy():
    return load_fixture("pod_running_healthy.json")


@pytest.fixture
def pod_init_container_failing():
    return load_fixture("pod_init_container_failing.json")


@pytest.fixture
def pod_config_error():
    return load_fixture("pod_config_error.json")


@pytest.fixture
def events_failed_scheduling():
    return load_fixture("events_failed_scheduling.json")


@pytest.fixture
def events_failed_mount():
    return load_fixture("events_failed_mount.json")


@pytest.fixture
def events_back_off_pull():
    return load_fixture("events_back_off_pull.json")


@pytest.fixture
def deployment_unavailable():
    return load_fixture("deployment_unavailable.json")


@pytest.fixture
def pvc_pending_unbound():
    return load_fixture("pvc_pending_unbound.json")


@pytest.fixture
def mock_kubectl():
    """Mock tools.kubectl_cmd to return empty/default values.
    Individual tests should override specific calls as needed.
    """
    with patch("tools.kubectl_cmd") as mock:
        mock.return_value = json.dumps({"items": []})
        yield mock


@pytest.fixture
def mock_tools_run_command():
    """Mock tools.run_command."""
    with patch("tools.run_command") as mock:
        mock.return_value = ""
        yield mock


def all_fixtures():
    """Return all pod fixture names for parametrized tests."""
    return [
        "pod_pending_unschedulable",
        "pod_pending_imagepullbackoff",
        "pod_pending_pvc_unbound",
        "pod_crashloopbackoff",
        "pod_oomkilled",
        "pod_running_healthy",
        "pod_init_container_failing",
        "pod_config_error",
    ]
