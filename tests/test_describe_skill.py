from unittest.mock import patch

from skills import SKILL_REGISTRY, DescribeSkill


class TestDescribeSkillRegistration:
    def test_skill_registered(self):
        skill = SKILL_REGISTRY.get("describe")
        assert skill is not None
        assert isinstance(skill, DescribeSkill)


class TestDescribeSkillFieldExtraction:
    def test_valid_fields_defined(self):
        from skills.describe_skill import VALID_FIELDS, DIAGNOSTIC_FIELD_WORDS
        assert "image" in VALID_FIELDS
        assert "replicas" in VALID_FIELDS
        assert "status" in VALID_FIELDS
        assert "health" in VALID_FIELDS
        assert "node" in VALID_FIELDS
        assert "labels" in VALID_FIELDS
        assert "why" in DIAGNOSTIC_FIELD_WORDS
        assert "error" in DIAGNOSTIC_FIELD_WORDS

    @patch("skills.describe_skill.kubectl_cmd")
    def test_field_not_recognized_returns_summary(self, mock_kubectl, pod_running_healthy):
        mock_kubectl.return_value = '{"items": []}'
        # For resolve_workload, mock the call
        from skills.resolver import Resolver

        skill = DescribeSkill()
        raw = pod_running_healthy

        result = skill._extract_field("nonexistent_field", raw, "pod", "happy", "default")
        assert result.status == "success"
        assert "not recognized" in result.message

    @patch("skills.describe_skill.kubectl_cmd")
    def test_extract_images(self, mock_kubectl, pod_running_healthy):
        skill = DescribeSkill()
        images = skill._extract_images(pod_running_healthy, "pod")
        assert len(images) == 1
        assert images[0]["container"] == "nginx"
        assert images[0]["image"] == "nginx:latest"


class TestDescribeSkillHealthMessage:
    def test_format_health_up(self):
        skill = DescribeSkill()
        status_info = {
            "replicas": {"ready": 3, "desired": 3, "available": 3}
        }
        msg = skill._format_health_message(status_info, "deployment", "web")
        assert "✅" in msg
        assert "up" in msg
        assert "3/3" in msg

    def test_format_health_down(self):
        skill = DescribeSkill()
        status_info = {
            "replicas": {"ready": 0, "desired": 2, "available": 0}
        }
        msg = skill._format_health_message(status_info, "deployment", "web")
        assert "❌" in msg
        assert "down" in msg
        assert "0/2" in msg

    def test_format_health_no_replica_info(self):
        skill = DescribeSkill()
        status_info = {
            "replicas": {}
        }
        msg = skill._format_health_message(status_info, "pod", "foo")
        assert "status:" in msg
