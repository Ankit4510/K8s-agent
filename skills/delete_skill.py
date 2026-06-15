"""
DeletePodSkill — always require approval, report replacement pod after deletion.
"""

import json
import time
from typing import Any, Dict

from cluster_store import get_active
from tools import kubectl_cmd, delete_pod
from .base import Skill, SkillResult
from .resolver import Resolver

class DeletePodSkill(Skill):
    name = "delete_pod"
    version = "1.0.0"
    triggers = ["delete pod", "kill pod", "remove pod"]
    description = "Delete a pod (with approval). Reports replacement pod if controller-owned."

    AUTO_PROCEED_SCORE = 0.90
    REPLACEMENT_WAIT = 20  # seconds

    def _cluster_tag(self):
        c = get_active()
        return f" [cluster: {c}]" if c else ""

    def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> SkillResult:
        name = args.get("name")
        namespace = args.get("namespace")
        preferred_ns = context.get("last_namespace")
        tag = self._cluster_tag()

        if not get_active():
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="needs_confirmation",
                message="No active cluster set. Use 'switch to <cluster-name>' first.",
            )

        if not name:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error", message="Missing pod name.",
            )

        # 1. Resolve pod
        self._log_step("resolve_pod", name=name)
        res = Resolver.resolve(name, "pod", namespace, preferred_ns)

        if res["status"] == "not_found":
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="not_found",
                message=f"Pod '{name}' not found in any namespace.{tag}",
            )

        if res["status"] == "suggestions":
            best = res["best_guess"]
            if best.get("score", 0) < self.AUTO_PROCEED_SCORE:
                suggestions = [
                    f"{m['name']} (ns={m['namespace']}, score={m.get('score', 0):.2f})"
                    for m in res["matches"][:5]
                ]
                return SkillResult(
                    skill_name=self.name, skill_version=self.version,
                    status="needs_confirmation",
                    message=f"Pod '{name}' not found exactly. Did you mean?{tag}",
                    suggestions=[s + tag for s in suggestions],
                    data={"matches": res["matches"][:5]},
                )
            pod = best
            self._log_step("auto_proceed_fuzzy", match=pod["name"], score=pod["score"])
        else:
            pod = res["best_guess"]

        # 2. Inspect pod for warnings
        owner_kind, owner_name = self._get_owner(pod["raw"])
        warning = (
            f"This pod is managed by {owner_kind}/{owner_name} — a replacement will be created.{tag}"
            if owner_kind
            else f"⚠️ This pod has NO controller — it will NOT be recreated.{tag}"
        )
        self._log_step("owner_check", kind=owner_kind, name=owner_name)

        active_cluster = get_active()
        approval_payload = {
            "cluster": active_cluster,
            "skill": self.name,
            "action": "delete_pod",
            "name": pod["name"],
            "namespace": pod["namespace"],
            "owner": f"{owner_kind}/{owner_name}" if owner_kind else None,
            "warning": warning,
            "command": f"kubectl delete pod {pod['name']} -n {pod['namespace']}",
        }

        if context.get("approved"):
            return self._do_delete(pod, owner_kind, owner_name)

        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="needs_approval",
            message=f"Ready to delete pod '{pod['name']}' in namespace '{pod['namespace']}'.{tag}\n{warning}",
            requires_approval=True,
            approval_payload=approval_payload,
            data=approval_payload,
        )

    # ---------------- helpers ---------------- #

    def _get_owner(self, pod_raw: Dict):
        owners = pod_raw.get("metadata", {}).get("ownerReferences", [])
        if not owners:
            return None, None
        return owners[0].get("kind"), owners[0].get("name")

    def _do_delete(self, pod: Dict, owner_kind, owner_name) -> SkillResult:
        tag = self._cluster_tag()
        self._log_step("execute_delete", pod=pod["name"], ns=pod["namespace"])
        out = delete_pod(pod["name"], pod["namespace"])
        if "Error" in out:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error",
                message=f"Delete failed: {out}{tag}",
                data={"raw": out},
            )

        replacement_info = {}
        if owner_kind:
            replacement_info = self._wait_for_replacement(
                owner_kind, owner_name, pod["namespace"], pod["name"]
            )

        msg = f"✅ Pod '{pod['name']}' deleted.{tag}"
        if replacement_info.get("new_pod"):
            msg += f"\n🔁 Replacement pod: {replacement_info['new_pod']} (status: {replacement_info['status']})"
        elif owner_kind:
            msg += "\n⚠️ Replacement pod not yet visible — check again in a moment."

        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="success",
            message=msg,
            data={"delete_output": out, "replacement": replacement_info},
        )

    def _wait_for_replacement(self, owner_kind, owner_name, namespace, old_pod_name) -> Dict:
        self._log_step("wait_replacement", owner=f"{owner_kind}/{owner_name}")
        deadline = time.time() + self.REPLACEMENT_WAIT
        # Invalidate cache by direct kubectl
        while time.time() < deadline:
            raw = kubectl_cmd(["get", "pods", "-n", namespace, "-o", "json"])
            try:
                items = json.loads(raw).get("items", [])
            except Exception:
                items = []
            for p in items:
                pname = p.get("metadata", {}).get("name", "")
                if pname == old_pod_name:
                    continue
                refs = p.get("metadata", {}).get("ownerReferences", [])
                for r in refs:
                    # Match either direct owner or RS owned by deployment
                    if r.get("name", "").startswith(owner_name) or r.get("name") == owner_name:
                        return {
                            "new_pod": pname,
                            "status": p.get("status", {}).get("phase", "Unknown"),
                        }
            time.sleep(2)
        return {}