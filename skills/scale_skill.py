from typing import Any, Dict

from cluster_store import get_active
from tools import scale_resource
from .base import Skill, SkillResult
from .resolver import Resolver


SCALABLE_KINDS = {"deployment", "statefulset"}


class ScaleSkill(Skill):
    name = "scale"
    version = "1.0.0"
    triggers = ["scale", "scale up", "scale down", "set replicas", "replicas"]
    description = "Scale a deployment or statefulset to a given number of replicas. Fuzzy-matches name and auto-expands app prefixes."

    AUTO_PROCEED_SCORE = 0.90

    def _cluster_tag(self):
        c = get_active()
        return f" [cluster: {c}]" if c else ""

    def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> SkillResult:
        name = args.get("name")
        namespace = args.get("namespace")
        replicas = args.get("replicas")
        explicit_kind = args.get("kind")
        preferred_ns = context.get("last_namespace")
        tag = self._cluster_tag()

        if not get_active():
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error",
                message="No active cluster set. Use 'switch to <cluster-name>' first.",
            )

        if not name:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error",
                message="Please tell me what to scale, e.g. 'scale order to 2'.",
            )

        if replicas is None:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error",
                message=f"How many replicas should '{name}' run? e.g. 'scale {name} to 3'",
            )

        try:
            replicas = int(replicas)
            if replicas < 0:
                raise ValueError()
        except (ValueError, TypeError):
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error",
                message=f"'{replicas}' is not a valid replica count. Please use a number, e.g. 'scale {name} to 2'.",
            )

        # Resolve without requiring the user to know kind
        kind_hint = explicit_kind.lower() if explicit_kind and explicit_kind.lower() in SCALABLE_KINDS else None

        if kind_hint:
            self._log_step("resolve_by_kind", kind=kind_hint, name=name)
            res = Resolver.resolve(name, kind_hint, namespace, preferred_ns)
        else:
            self._log_step("resolve_workload", name=name)
            res = Resolver.resolve_workload(name, namespace, preferred_ns)

        if res["status"] == "not_found":
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="not_found",
                message=f"I couldn't find a deployment or statefulset matching '{name}'.{tag}\nTip: try the full name or 'list deployments' to see what's available.",
            )

        if res["status"] == "suggestions":
            best = res["best_guess"]
            if best.get("score", 0) < self.AUTO_PROCEED_SCORE:
                matches = res["matches"][:5]
                suggestions = [
                    f"{m['name']} ({m.get('kind', '?')}, ns={m['namespace']})"
                    for m in matches
                ]
                return SkillResult(
                    skill_name=self.name, skill_version=self.version,
                    status="needs_confirmation",
                    message=f"I found a few possible matches for '{name}'. Which one did you mean?{tag}",
                    suggestions=suggestions,
                    data={"matches": matches},
                )

        target = res["best_guess"]
        target_kind = target.get("kind", "deployment")
        target_name = target["name"]
        target_ns = target["namespace"]

        if target_kind.lower() not in SCALABLE_KINDS:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error",
                message=f"'{target_name}' is a {target_kind} — scaling is only supported for deployments and statefulsets.{tag}",
            )

        self._log_step("resolved", kind=target_kind, name=target_name, ns=target_ns, replicas=replicas)

        approval_payload = {
            "cluster": get_active(),
            "skill": self.name,
            "action": "scale",
            "kind": target_kind,
            "name": target_name,
            "namespace": target_ns,
            "replicas": replicas,
            "command": f"kubectl scale {target_kind.lower()} {target_name} --replicas={replicas} -n {target_ns}",
        }

        if context.get("approved"):
            return self._do_scale(target_kind, target_name, target_ns, replicas)

        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="needs_approval",
            message=(
                f"Ready to scale **{target_kind}** `{target_name}` to **{replicas}** replica(s) "
                f"in namespace `{target_ns}`.{tag}\nApprove to proceed."
            ),
            requires_approval=True,
            approval_payload=approval_payload,
            data=approval_payload,
        )

    def _do_scale(self, kind: str, name: str, namespace: str, replicas: int) -> SkillResult:
        tag = self._cluster_tag()
        self._log_step("execute_scale", kind=kind, name=name, ns=namespace, replicas=replicas)
        out = scale_resource(kind, name, namespace, replicas)
        if out.lower().startswith("error"):
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error",
                message=f"Scale failed: {out}{tag}",
                data={"raw": out},
            )
        verb = "up" if replicas > 0 else "down to zero"
        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="success",
            message=f"✅ Scaled {kind} `{name}` to **{replicas}** replica(s) in `{namespace}`.{tag}",
            data={
                "kind": kind,
                "name": name,
                "namespace": namespace,
                "replicas": replicas,
                "scale_output": out,
            },
        )
