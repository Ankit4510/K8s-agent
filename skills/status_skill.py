"""
StatusSkill — cluster/namespace health overview, suggests one-click remediations.
"""

import json
from typing import Any, Dict, List

from tools import kubectl_cmd, analyze_deployments
from .base import Skill, SkillResult
from .resolver import Resolver

class StatusSkill(Skill):
    name = "status"
    version = "1.0.0"
    triggers = ["status", "health", "is everything ok", "what's broken", "any issues"]
    description = "Cluster / namespace health summary with suggested remediations."

    RESTART_THRESHOLD = 10

    def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> SkillResult:
        scope = args.get("scope", "cluster")  # cluster | namespace | resource
        namespace = args.get("namespace")
        target = args.get("name")

        self._log_step("scan_start", scope=scope, namespace=namespace, target=target)

        findings = {
            "unhealthy_pods": self._scan_pods(namespace),
            "unhealthy_deployments": self._scan_deployments(namespace),
            "pending_pvcs": self._scan_pvcs(namespace),
        }

        # Build remediation suggestions
        remediations = self._build_remediations(findings)

        total_issues = (
                len(findings["unhealthy_pods"])
                + len(findings["unhealthy_deployments"])
                + len(findings["pending_pvcs"])
        )

        if total_issues == 0:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="success",
                message="✅ Cluster looks healthy — no issues detected.",
                data=findings,
            )

        lines = [f"⚠️ Found {total_issues} issue(s)."]
        if findings["unhealthy_pods"]:
            pods = findings["unhealthy_pods"]
            parts = [f"  • {len(pods)} pod(s) with high restarts or crashlooping:"]
            for p in pods[:5]:
                details = f"crashloop" if p["crashloop"] else f"restarts={p['restarts']}"
                parts.append(f"    - {p['name']} (ns={p['namespace']}, {details})")
            if len(pods) > 5:
                parts.append(f"    ... and {len(pods) - 5} more")
            lines.append("\n".join(parts))
        if findings["unhealthy_deployments"]:
            deps = findings["unhealthy_deployments"]
            parts = [f"  • {len(deps)} deployment(s) with unavailable replicas:"]
            for d in deps[:5]:
                parts.append(f"    - {d['name']} (ns={d['namespace']}, {d['available']}/{d['desired']} available)")
            if len(deps) > 5:
                parts.append(f"    ... and {len(deps) - 5} more")
            lines.append("\n".join(parts))
        if findings["pending_pvcs"]:
            pvcs = findings["pending_pvcs"]
            parts = [f"  • {len(pvcs)} pending PVC(s):"]
            for p in pvcs[:5]:
                parts.append(f"    - {p['name']} (ns={p['namespace']}, phase={p['phase']})")
            if len(pvcs) > 5:
                parts.append(f"    ... and {len(pvcs) - 5} more")
            lines.append("\n".join(parts))

        if remediations:
            lines.append("\nSuggested one-click remediations:")
            for r in remediations[:3]:
                lines.append(f"  • `{r['one_click']}`")

        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="success",
            message="\n".join(lines),
            data={
                "findings": findings,
                "remediations": remediations,
            },
        )

    # ---------------- scanners ---------------- #

    def _scan_pods(self, namespace) -> List[Dict]:
        pods = Resolver.list_resources("pod", namespace)
        bad = []
        for p in pods:
            raw = p["raw"]
            phase = raw.get("status", {}).get("phase", "")
            statuses = raw.get("status", {}).get("containerStatuses", []) or []
            max_restarts = max([s.get("restartCount", 0) for s in statuses], default=0)
            crashloop = any(
                s.get("state", {}).get("waiting", {}).get("reason") == "CrashLoopBackOff"
                for s in statuses
            )
            if (
                    phase not in ("Running", "Succeeded")
                    or max_restarts >= self.RESTART_THRESHOLD
                    or crashloop
            ):
                bad.append({
                    "name": p["name"],
                    "namespace": p["namespace"],
                    "phase": phase,
                    "restarts": max_restarts,
                    "crashloop": crashloop,
                })
        self._log_step("scan_pods_done", count=len(bad))
        return bad

    def _scan_deployments(self, namespace) -> List[Dict]:
        deps = Resolver.list_resources("deployment", namespace)
        bad = []
        for d in deps:
            raw = d["raw"]
            desired = raw.get("spec", {}).get("replicas", 1)
            available = raw.get("status", {}).get("availableReplicas", 0)
            if desired > 0 and available < desired:
                bad.append({
                    "name": d["name"],
                    "namespace": d["namespace"],
                    "desired": desired,
                    "available": available,
                })
        self._log_step("scan_deployments_done", count=len(bad))
        return bad

    def _scan_pvcs(self, namespace) -> List[Dict]:
        pvcs = Resolver.list_resources("pvc", namespace)
        bad = []
        for pvc in pvcs:
            raw = pvc["raw"]
            phase = raw.get("status", {}).get("phase", "")
            if phase != "Bound":
                bad.append({
                    "name": pvc["name"],
                    "namespace": pvc["namespace"],
                    "phase": phase,
                })
        self._log_step("scan_pvcs_done", count=len(bad))
        return bad

    # ---------------- remediations ---------------- #

    def _build_remediations(self, findings: Dict) -> List[Dict]:
        remediations = []
        for pod in findings["unhealthy_pods"]:
            if pod["crashloop"] or pod["restarts"] >= self.RESTART_THRESHOLD:
                remediations.append({
                    "issue": f"Pod {pod['name']} (ns={pod['namespace']}) crashlooping/restarts={pod['restarts']}",
                    "action": "restart",
                    "skill": "restart",
                    "args": {"name": pod["name"], "namespace": pod["namespace"]},
                    "one_click": f"restart {pod['name']}",
                })
        for dep in findings["unhealthy_deployments"]:
            remediations.append({
                "issue": f"Deployment {dep['name']} (ns={dep['namespace']}) {dep['available']}/{dep['desired']} available",
                "action": "restart",
                "skill": "restart",
                "args": {"name": dep["name"], "namespace": dep["namespace"]},
                "one_click": f"restart {dep['name']}",
            })
        return remediations