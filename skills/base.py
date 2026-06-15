"""
Base classes for the skill system.
Every skill is a deterministic playbook for one category of user intent.
"""

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

@dataclass
class SkillResult:
    """Standardized return format for every skill."""
    skill_name: str
    skill_version: str
    status: str  # "success" | "needs_confirmation" | "needs_approval" | "error" | "not_found"
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    suggestions: List[str] = field(default_factory=list)
    flow_log: List[str] = field(default_factory=list)
    requires_approval: bool = False
    approval_payload: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class Skill(ABC):
    """Base class for all skills."""

    name: str = "base"
    version: str = "1.0.0"
    triggers: List[str] = []
    description: str = ""

    def __init__(self):
        self._flow_log: List[str] = []

    # ---------------- flow logging ---------------- #

    def _log_step(self, step: str, **details):
        entry = {
            "ts": time.time(),
            "skill": self.name,
            "step": step,
            **details,
        }
        self._flow_log.append(json.dumps(entry))
        # Also log to file
        try:
            from logger import log_event  # your existing logger
            log_event(self.name, step, details)
        except Exception:
            pass

    def _reset_log(self):
        self._flow_log = []

    # ---------------- public entrypoint ---------------- #

    def run(self, args: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> SkillResult:
        """Public entrypoint — wraps execute() with logging + error handling."""
        self._reset_log()
        context = context or {}
        self._log_step("start", args=args)

        try:
            result = self.execute(args, context)
        except Exception as e:
            self._log_step("exception", error=str(e))
            return SkillResult(
                skill_name=self.name,
                skill_version=self.version,
                status="error",
                message=f"Skill '{self.name}' failed: {e}",
                flow_log=self._flow_log,
            )

        # Inject skill metadata + flow log
        result.skill_name = self.name
        result.skill_version = self.version
        result.flow_log = self._flow_log
        self._log_step("end", status=result.status)
        return result

    @abstractmethod
    def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> SkillResult:
        """Override this in each skill."""
        ...

class SkillRegistry:
    """Central registry for all skills."""

    def __init__(self):
        self._skills: Dict[str, Skill] = {}

    def register(self, skill: Skill):
        self._skills[skill.name] = skill

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def list_skills(self) -> List[Dict[str, str]]:
        return [
            {"name": s.name, "version": s.version, "description": s.description}
            for s in self._skills.values()
        ]

    def run(self, name: str, args: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> SkillResult:
        skill = self.get(name)
        if not skill:
            return SkillResult(
                skill_name=name,
                skill_version="n/a",
                status="error",
                message=f"Unknown skill '{name}'",
            )
        return skill.run(args, context)

SKILL_REGISTRY = SkillRegistry()