from typing import Any, Dict

from cluster_store import get_active
from tools import get_owner_from_pod, restart_resource
from .base import Skill, SkillResult
from .resolver import Resolver


class RestartSkill(Skill):
    name = "restart"
    version = "1.0.0"
    triggers = ["restart", "rollout", "bounce", "recycle"]
    description = "Restart a workload (deployment / statefulset / daemonset). Fire-and-forget — no blocking verification."

    AUTO_PROCEED_SCORE = 0.90

    def _cluster_tag(self):
        c = get_active()
        return f" [cluster: {c}]" if c else ""

    def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> SkillResult:
        name = args.get("name")
        namespace = args.get("namespace")
        preferred_ns = context.get("last_namespace")
        tag = self._cluster_tag()
        explicit_kind = args.get("kind")
        explicit_pod = args.get("explicit_pod", False)

        if not get_active():
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="needs_confirmation",
                message="No active cluster set. Use 'switch to <cluster-name>' first.",
            )

        if not name:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error", message="Missing 'name' for restart.",
            )

        target_kind = None
        target_name = None
        target_ns = None

        # --- Resolution strategy ---
        if explicit_kind:
            self._log_step("resolve_by_kind", kind=explicit_kind, name=name)
            res = Resolver.resolve(name, explicit_kind, namespace, preferred_ns)
            target_kind, target_name, target_ns = self._use_resolve(res, name, explicit_kind, tag)
            if not target_name:
                return self._last_result

        elif explicit_pod:
            self._log_step("resolve_pod_explicit", name=name)
            res = Resolver.resolve(name, "pod", namespace, preferred_ns)
            if res["status"] == "exact":
                target_kind, target_name, target_ns = self._from_pod(res["best_guess"], tag)
            elif res["status"] == "suggestions" and res["best_guess"]["score"] >= self.AUTO_PROCEED_SCORE:
                target_kind, target_name, target_ns = self._from_pod(res["best_guess"], tag)
            elif res["status"] == "suggestions":
                return self._suggestions_result(name, res, tag)
            else:
                wl = Resolver.resolve_workload(name, namespace, preferred_ns)
                target_kind, target_name, target_ns = self._use_resolve(wl, name, None, tag)

        else:
            self._log_step("resolve_workload_first", name=name)
            wl = Resolver.resolve_workload(name, namespace, preferred_ns)
            target_kind, target_name, target_ns = self._use_resolve(wl, name, None, tag)
            if not target_name:
                self._log_step("resolve_pod_fallback", name=name)
                res = Resolver.resolve(name, "pod", namespace, preferred_ns)
                if res["status"] == "exact":
                    target_kind, target_name, target_ns = self._from_pod(res["best_guess"], tag)
                elif res["status"] == "suggestions" and res["best_guess"]["score"] >= self.AUTO_PROCEED_SCORE:
                    target_kind, target_name, target_ns = self._from_pod(res["best_guess"], tag)
                elif res["status"] == "suggestions":
                    return self._suggestions_result(name, res, tag)

        if not target_name:
            return self._last_result if hasattr(self, "_last_result") else SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="not_found",
                message=f"No pod or workload matching '{name}' found.{tag}",
            )

        self._log_step("resolved", kind=target_kind, name=target_name, ns=target_ns)
        active_cluster = get_active()
        approval_payload = {
            "cluster": active_cluster,
            "skill": self.name,
            "action": "rollout_restart",
            "kind": target_kind,
            "name": target_name,
            "namespace": target_ns,
            "command": f"kubectl rollout restart {target_kind.lower()} {target_name} -n {target_ns}",
        }

        if context.get("approved"):
            return self._do_restart(target_kind, target_name, target_ns)

        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="needs_approval",
            message=f"Ready to restart {target_kind} '{target_name}' in namespace '{target_ns}'.{tag}",
            requires_approval=True,
            approval_payload=approval_payload,
            data=approval_payload,
        )

    def _from_pod(self, pod, tag):
        kind, owner, ns = get_owner_from_pod(pod["name"], pod["namespace"])
        if not kind or not owner:
            self._last_result = SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error",
                message=f"Pod '{pod['name']}' has no owning controller — cannot rollout-restart.{tag}",
            )
            return None, None, None
        return kind, owner, ns

    def _use_resolve(self, res, name, kind_hint, tag):
        if res["status"] == "exact":
            w = res["best_guess"]
            return w["kind"], w["name"], w["namespace"]
        if res["status"] == "suggestions":
            best = res["best_guess"]
            if best.get("score", 0) >= self.AUTO_PROCEED_SCORE:
                return best["kind"], best["name"], best["namespace"]
            suggestions = [
                f"{m['name']} ({m['kind']}, ns={m['namespace']}, score={m.get('score', 0):.2f})"
                for m in res["matches"][:5]
            ]
            self._last_result = SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="needs_confirmation",
                message=f"No exact match for '{name}'. Did you mean?{tag}",
                suggestions=[s + tag for s in suggestions],
                data={"matches": res["matches"][:5]},
            )
            return None, None, None
        return None, None, None

    def _suggestions_result(self, name, res, tag):
        suggestions = [
            f"{m['name']} (ns={m['namespace']}, score={m.get('score', 0):.2f})"
            for m in res["matches"][:5]
        ]
        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="needs_confirmation",
            message=f"Pod '{name}' not found exactly. Did you mean one of these?{tag}",
            suggestions=[s + tag for s in suggestions],
            data={"matches": res["matches"][:5]},
        )

    def _do_restart(self, kind: str, name: str, namespace: str) -> SkillResult:
        tag = self._cluster_tag()
        self._log_step("execute_restart", kind=kind, name=name, ns=namespace)
        out = restart_resource(kind, name, namespace)
        if "Error" in out:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error",
                message=f"Restart failed: {out}{tag}",
                data={"raw": out},
            )

        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="success",
            message=f"🔄 Restart initiated for {kind} '{name}' in namespace '{namespace}'.{tag} Check back with 'is {name} up?' to verify.",
            data={
                "restart_output": out,
                "kind": kind,
                "name": name,
                "namespace": namespace,
                "restart_initiated": True,
            },
        )
