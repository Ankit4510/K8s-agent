"""
DescribeSkill — answers field-specific questions (image, replicas, status),
auto-includes events for unhealthy resources, chains into LogsSkill on CrashLoopBackOff.
All field responses include formatted data inline in the message text (not just in data dict).

Health queries ("is X down?", "is X running?"):
  - When field="status" and kind=null, uses resolve_workload (workload-first, not pod-default)
  - Workloads get a simple up/down answer via _format_health_message:
      ✅ deployment 'name' is up. 2/2 ready.
      ❌ deployment 'name' is down. 0/1 ready.
  - Pods return phase directly (Running, Pending, etc.)
"""

import json
from typing import Any, Dict, List, Optional

from tools import kubectl_cmd
from .base import Skill, SkillResult
from .resolver import Resolver

VALID_FIELDS = {"image", "replicas", "desired", "desired_state", "scale", "status", "phase", "health", "node", "nodename", "labels"}
DIAGNOSTIC_FIELD_WORDS = {"why", "error", "issue", "problem", "crash", "fail", "broken", "diagnose", "root cause"}

class DescribeSkill(Skill):
    name = "describe"
    version = "1.0.0"
    triggers = ["describe", "info", "details", "image", "replicas", "desired", "state"]
    description = "Describe a resource. Supports field-specific queries (image, replicas, etc.)."

    AUTO_PROCEED_SCORE = 0.90
    UNHEALTHY_PHASES = {"Pending", "Failed", "Unknown"}

    def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> SkillResult:
        name = args.get("name")
        namespace = args.get("namespace")
        kind_arg = args.get("kind")
        field = args.get("field")
        preferred_ns = context.get("last_namespace")

        if not name:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error", message="Missing resource name.",
            )

        # 1. Resolve — workload-first when no explicit kind
        if not kind_arg:
            self._log_step("resolve_workload", name=name)
            res = Resolver.resolve_workload(name, namespace, preferred_ns)
            kind = res["best_guess"]["kind"] if res.get("best_guess") else "pod"
        else:
            kind = kind_arg.lower()
            self._log_step("resolve", kind=kind, name=name)
            res = Resolver.resolve(name, kind, namespace, preferred_ns)
            if res["status"] == "not_found":
                wl = Resolver.resolve_workload(name, namespace, preferred_ns)
                if wl["status"] in ("exact", "suggestions"):
                    res = wl
                    kind = res["best_guess"]["kind"] if res.get("best_guess") else kind

        if res["status"] == "not_found":
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="not_found",
                message=f"No {kind} matching '{name}' found.",
            )

        if res["status"] == "suggestions":
            best = res["best_guess"]
            if best.get("score", 0) < self.AUTO_PROCEED_SCORE:
                suggestions = [
                    f"{m['name']} ({m.get('kind', kind)}, ns={m['namespace']})"
                    for m in res["matches"][:5]
                ]
                return SkillResult(
                    skill_name=self.name, skill_version=self.version,
                    status="needs_confirmation",
                    message=f"Did you mean one of these?",
                    suggestions=suggestions,
                    data={"matches": res["matches"][:5]},
                )
            target = best
        else:
            target = res["best_guess"]

        target_name = target["name"]
        target_ns = target["namespace"]
        target_kind = target.get("kind", kind)
        raw = target["raw"]

        # 2. Field-specific queries
        if field:
            return self._extract_field(field, raw, target_kind, target_name, target_ns)

        # 3. Full describe summary
        summary = self._build_summary(raw, target_kind)
        is_unhealthy = self._is_unhealthy(raw, target_kind)

        if is_unhealthy:
            self._log_step("unhealthy_include_events")
            summary["events"] = self._get_events(target_name, target_ns)

        # 4. CrashLoopBackOff → chain into logs
        if self._has_crashloop(raw):
            self._log_step("crashloop_chain_logs")
            from .base import SKILL_REGISTRY
            logs_skill = SKILL_REGISTRY.get("logs")
            if logs_skill:
                logs_result = logs_skill.run(
                    {"name": target_name, "namespace": target_ns, "errors_only": True},
                    context={"chained_from": self.name},
                )
                summary["recent_errors"] = logs_result.data.get("logs", "")

        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="success",
            message=f"Description of {target_kind} '{target_name}' (ns={target_ns}):",
            data=summary,
        )

    # ---------------- field extraction ---------------- #

    def _extract_field(self, field: str, raw: Dict, kind: str, name: str, ns: str) -> SkillResult:
        field = field.lower()
        self._log_step("extract_field", field=field)

        # Check if field is a diagnostic word → chain to DiagnoseSkill
        if any(dw in field for dw in DIAGNOSTIC_FIELD_WORDS):
            self._log_step("chain_to_diagnose", field=field)
            from .base import SKILL_REGISTRY
            diagnose_skill = SKILL_REGISTRY.get("diagnose")
            if diagnose_skill:
                return diagnose_skill.run(
                    {"name": name, "kind": kind, "namespace": ns},
                    context={"chained_from": self.name},
                )

        if field == "image":
            images = self._extract_images(raw, kind)
            lines = [f"{kind} '{name}' images:"]
            for img in images:
                lines.append(f"  {img['type']} container {img['container']}: {img['image']}")
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="success",
                message="\n".join(lines),
                data={"images": images},
            )

        if field in ("replicas", "desired", "desired_state", "scale"):
            replicas = self._extract_replicas(raw, kind)
            r = replicas
            if r.get("desired") is not None:
                msg = f"{kind} '{name}': {r['ready']}/{r['desired']} ready, {r['available']}/{r['desired']} available"
            else:
                msg = f"{kind} '{name}': no replica information available"
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="success",
                message=msg,
                data=replicas,
            )

        if field in ("status", "phase", "health"):
            status_info = self._extract_status(raw, kind)
            if kind in ("deployment", "statefulset", "daemonset"):
                msg = self._format_health_message(status_info, kind, name)
            else:
                msg = self._format_status_message(status_info, kind, name)
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="success",
                message=msg,
                data=status_info,
            )

        if field in ("node", "nodename"):
            node = raw.get("spec", {}).get("nodeName") if kind == "pod" else None
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="success",
                message=f"Node for {kind} '{name}':",
                data={"node": node or "N/A"},
            )

        # Unknown field — fall back to full summary
        if field not in VALID_FIELDS:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="success",
                message=f"Field '{field}' not recognized; showing full summary.",
                data=self._build_summary(raw, kind),
            )

        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="success",
            message=f"Field '{field}' not specifically handled; showing full summary.",
            data=self._build_summary(raw, kind),
        )

    def _extract_images(self, raw: Dict, kind: str) -> List[Dict]:
        if kind == "pod":
            spec = raw.get("spec", {})
        else:
            spec = raw.get("spec", {}).get("template", {}).get("spec", {})
        images = []
        for c in spec.get("containers", []):
            images.append({"container": c.get("name"), "image": c.get("image"), "type": "main"})
        for c in spec.get("initContainers", []):
            images.append({"container": c.get("name"), "image": c.get("image"), "type": "init"})
        return images

    def _extract_replicas(self, raw: Dict, kind: str) -> Dict:
        spec = raw.get("spec", {})
        status = raw.get("status", {})
        return {
            "desired": spec.get("replicas"),
            "available": status.get("availableReplicas", 0),
            "ready": status.get("readyReplicas", 0),
            "updated": status.get("updatedReplicas", 0),
            "unavailable": status.get("unavailableReplicas", 0),
        }

    def _extract_status(self, raw: Dict, kind: str) -> Dict:
        if kind == "pod":
            status = raw.get("status", {})
            return {
                "phase": status.get("phase"),
                "reason": status.get("reason"),
                "message": status.get("message"),
                "containerStatuses": [
                    {
                        "name": cs.get("name"),
                        "ready": cs.get("ready"),
                        "restartCount": cs.get("restartCount"),
                        "state": list(cs.get("state", {}).keys()),
                    }
                    for cs in status.get("containerStatuses", [])
                ],
            }
        # Workload
        return {
            "replicas": self._extract_replicas(raw, kind),
            "conditions": [
                {"type": c.get("type"), "status": c.get("status"), "reason": c.get("reason")}
                for c in raw.get("status", {}).get("conditions", [])
            ],
        }

    def _format_status_message(self, status_info: Dict, kind: str, name: str) -> str:
        if kind == "pod":
            phase = status_info.get("phase", "Unknown")
            parts = [f"Pod '{name}' status: {phase}"]
            for cs in status_info.get("containerStatuses", []):
                state = ", ".join(cs.get("state", [])) or "unknown"
                ready = "✅ ready" if cs.get("ready") else "❌ not ready"
                parts.append(f"  container {cs['name']}: {state}, {ready}, restarts={cs['restartCount']}")
            if status_info.get("reason"):
                parts.append(f"  reason: {status_info['reason']}")
            if status_info.get("message"):
                parts.append(f"  message: {status_info['message']}")
            return "\n".join(parts)

        # Workload (deployment / statefulset / daemonset)
        reps = status_info.get("replicas", {})
        parts = [f"{kind} '{name}' status:"]
        if reps.get("desired") is not None:
            ready = reps.get("ready", 0)
            avail = reps.get("available", 0)
            desired = reps.get("desired", 0)
            if ready >= desired and avail >= desired:
                parts.append(f"  ✅ {ready}/{desired} replicas ready and available")
            else:
                parts.append(f"  ⚠️ {ready}/{desired} ready, {avail}/{desired} available")
        for c in status_info.get("conditions", []):
            if c.get("status") != "True":
                parts.append(f"  ⚠️ condition {c['type']}: {c['status']} ({c.get('reason', '')})")
        if not parts[1:]:
            parts.append("  No status information available.")
        return "\n".join(parts)

    def _format_health_message(self, status_info: Dict, kind: str, name: str) -> str:
        reps = status_info.get("replicas", {})
        ready = reps.get("ready", 0)
        desired = reps.get("desired")
        if desired is not None and ready > 0:
            return f"✅ {kind} '{name}' is up. {ready}/{desired} ready."
        elif desired is not None:
            return f"❌ {kind} '{name}' is down. 0/{desired} ready."
        return self._format_status_message(status_info, kind, name)

    # ---------------- summary + health ---------------- #

    def _build_summary(self, raw: Dict, kind: str) -> Dict:
        meta = raw.get("metadata", {})
        summary = {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "kind": kind,
            "labels": meta.get("labels", {}),
            "creationTimestamp": meta.get("creationTimestamp"),
        }
        if kind == "pod":
            summary["status"] = self._extract_status(raw, kind)
            summary["images"] = self._extract_images(raw, kind)
            summary["node"] = raw.get("spec", {}).get("nodeName")
        else:
            summary["replicas"] = self._extract_replicas(raw, kind)
            summary["images"] = self._extract_images(raw, kind)
            summary["status"] = self._extract_status(raw, kind)
        return summary

    def _is_unhealthy(self, raw: Dict, kind: str) -> bool:
        if kind == "pod":
            phase = raw.get("status", {}).get("phase", "")
            if phase in self.UNHEALTHY_PHASES:
                return True
            for cs in raw.get("status", {}).get("containerStatuses", []):
                if not cs.get("ready", True):
                    return True
                if cs.get("restartCount", 0) > 0:
                    return True
            return False
        # Workload
        spec_replicas = raw.get("spec", {}).get("replicas", 1)
        avail = raw.get("status", {}).get("availableReplicas", 0)
        return avail < spec_replicas

    def _has_crashloop(self, raw: Dict) -> bool:
        for cs in raw.get("status", {}).get("containerStatuses", []):
            waiting = cs.get("state", {}).get("waiting", {})
            if waiting.get("reason") == "CrashLoopBackOff":
                return True
        return False

    def _get_events(self, name: str, namespace: str) -> List[str]:
        out = kubectl_cmd([
            "get", "events", "-n", namespace,
            "--field-selector", f"involvedObject.name={name}",
            "--sort-by=.lastTimestamp",
            "-o", "json",
        ])
        try:
            data = json.loads(out)
            events = data.get("items", [])[-5:]
            return [
                f"[{e.get('type')}] {e.get('reason')}: {e.get('message')}"
                for e in events
            ]
        except Exception:
            return []