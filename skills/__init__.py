from .base import Skill, SkillResult, SkillRegistry, SKILL_REGISTRY
from .resolver import Resolver
from .restart_skill import RestartSkill
from .delete_skill import DeletePodSkill
from .logs_skill import LogsSkill
from .describe_skill import DescribeSkill
from .status_skill import StatusSkill
from .cluster_skill import ClusterSwitchSkill
from .list_skill import ListSkill
from .diagnose_skill import DiagnoseSkill
from .scale_skill import ScaleSkill
from .resources_skill import ResourcesSkill

# Auto-register all skills on import
SKILL_REGISTRY.register(RestartSkill())
SKILL_REGISTRY.register(DeletePodSkill())
SKILL_REGISTRY.register(LogsSkill())
SKILL_REGISTRY.register(DescribeSkill())
SKILL_REGISTRY.register(StatusSkill())
SKILL_REGISTRY.register(ClusterSwitchSkill())
SKILL_REGISTRY.register(ListSkill())
SKILL_REGISTRY.register(DiagnoseSkill())
SKILL_REGISTRY.register(ScaleSkill())
SKILL_REGISTRY.register(ResourcesSkill())

__all__ = [
    "Skill",
    "SkillResult",
    "SkillRegistry",
    "SKILL_REGISTRY",
    "Resolver",
    "RestartSkill",
    "DeletePodSkill",
    "LogsSkill",
    "DescribeSkill",
    "StatusSkill",
    "ClusterSwitchSkill",
    "ListSkill",
    "DiagnoseSkill",
    "ScaleSkill",
    "ResourcesSkill",
]