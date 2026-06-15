"""
LogsSkill — auto-pick main container, fall back to describe if no logs,
check --previous for crashlooping pods, filter errors in Python.
"""

import re
from typing import Any, Dict, List

from tools import kubectl_cmd, get_logs
from .base import Skill, SkillResult
from .resolver import Resolver

class LogsSkill(Skill):
    name = "logs"
    version = "1.0.0"
    triggers = ["logs", "log", "errors", "what's wrong", "show me logs"]
    description = "Fetch and filter logs. Falls back to describe if pod is pending or has no logs."

    DEFAULT_TAIL = 500
    AUTO_PROCEED_SCORE = 0.90
    ERROR_PATTERNS = re.compile(
        r"(error|exception|traceback|fail|fatal|panic|warn)",
        re.IGNORECASE,
    )

    def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> SkillResult:
        name = args.get("name")
        namespace = args.get("namespace")
        container = args.get("container")
        tail = args.get("tail", self.DEFAULT_TAIL)
        errors_only = args.get("errors_only", False)
        preferred_ns = context.get("last_namespace")

        if not name:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error", message="Missing pod name.",
            )

        # 1. Resolve pod — first try direct pod match, then fall back to workload→pod
        self._log_step("resolve_pod", name=name)
        res = Resolver.resolve(name, "pod", namespace, preferred_ns)

        # If no pod found or low confidence, try resolving as a workload and pick its pod
        if res["status"] == "not_found" or (
            res["status"] == "suggestions" and res["best_guess"].get("score", 0) < self.AUTO_PROCEED_SCORE
        ):
            self._log_step("try_workload_fallback", name=name)
            workload_res = Resolver.resolve_workload(name, namespace, preferred_ns)
            if workload_res["status"] in ("exact", "suggestions") and workload_res.get("best_guess"):
                wl = workload_res["best_guess"]
                wl_name = wl["name"]
                wl_ns = wl["namespace"]
                # Find a running pod for this workload
                pod_res = Resolver.resolve(wl_name, "pod", wl_ns, preferred_ns)
                if pod_res["status"] in ("exact", "suggestions") and pod_res.get("best_guess"):
                    self._log_step("resolved_via_workload", workload=wl_name, pod=pod_res["best_guess"]["name"])
                    res = pod_res

        if res["status"] == "not_found":
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="not_found",
                message=f"No pod found for '{name}'. Try 'list pods' to see available pods.",
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
                    message=f"Did you mean one of these pods?",
                    suggestions=suggestions,
                    data={"matches": res["matches"][:5]},
                )
            pod = best
        else:
            pod = res["best_guess"]

        pod_name = pod["name"]
        pod_ns = pod["namespace"]
        pod_raw = pod["raw"]
        phase = pod_raw.get("status", {}).get("phase", "Unknown")

        # 2. If pod is Pending → use describe (no logs available)
        if phase == "Pending":
            self._log_step("pod_pending_fallback_describe")
            return self._fallback_to_describe(pod_name, pod_ns, "Pod is in Pending state — no logs available.")

        # 3. Auto-pick main container
        if not container:
            container = self._pick_main_container(pod_raw, pod_name)
            self._log_step("auto_picked_container", container=container)

        # 4. Fetch logs
        self._log_step("fetch_logs", container=container, tail=tail)
        log_output = self._get_logs(pod_name, pod_ns, container, tail, previous=False)

        # 5. If logs empty/error and pod has restarted → try --previous
        restart_count = self._get_restart_count(pod_raw, container)
        if (not log_output.strip() or "Error" in log_output) and restart_count > 0:
            self._log_step("try_previous_logs", restarts=restart_count)
            prev_output = self._get_logs(pod_name, pod_ns, container, tail, previous=True)
            if prev_output.strip() and "Error" not in prev_output:
                log_output = f"[Previous container logs — pod has restarted {restart_count} times]\n\n{prev_output}"

        # 6. If still no logs → fallback to describe
        if not log_output.strip() or "Error" in log_output:
            self._log_step("no_logs_fallback_describe")
            return self._fallback_to_describe(
                pod_name, pod_ns,
                f"No logs available for pod '{pod_name}'. Showing describe output instead."
            )

        # 7. Filter for errors if requested
        if errors_only:
            filtered = [
                line for line in log_output.splitlines()
                if self.ERROR_PATTERNS.search(line)
            ]
            self._log_step("filter_errors", matched=len(filtered))
            log_output = "\n".join(filtered) if filtered else "No error lines found in logs."

        # 8. Truncate if too long
        lines = log_output.splitlines()
        if len(lines) > tail:
            log_output = "\n".join(lines[-tail:])

        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="success",
            message=f"Logs for '{pod_name}' (container={container}, ns={pod_ns}):",
            data={
                "pod": pod_name,
                "namespace": pod_ns,
                "container": container,
                "logs": log_output,
                "lines": len(log_output.splitlines()),
            },
        )

    # ---------------- helpers ---------------- #

    def _pick_main_container(self, pod_raw: Dict, pod_name: str) -> str:
        containers = pod_raw.get("spec", {}).get("containers", [])
        if not containers:
            return ""
        if len(containers) == 1:
            return containers[0]["name"]
        # Heuristic: container whose name is a prefix of the pod name
        prefix = pod_name.split("-")[0]
        for c in containers:
            if c["name"].startswith(prefix) or prefix.startswith(c["name"]):
                return c["name"]
        # Fallback: first container
        return containers[0]["name"]

    def _get_restart_count(self, pod_raw: Dict, container: str) -> int:
        statuses = pod_raw.get("status", {}).get("containerStatuses", [])
        for s in statuses:
            if s.get("name") == container:
                return s.get("restartCount", 0)
        return 0

    def _get_logs(self, pod, ns, container, tail, previous=False) -> str:
        cmd = ["logs", pod, "-c", container, "--tail", str(tail), "-n", ns]
        if previous:
            cmd.append("--previous")
        return kubectl_cmd(cmd, timeout=30)

    def _fallback_to_describe(self, pod_name, namespace, reason) -> SkillResult:
        # Composable: call DescribeSkill
        from .base import SKILL_REGISTRY
        describe = SKILL_REGISTRY.get("describe")
        if not describe:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error",
                message=reason + " (describe skill unavailable)",
            )
        result = describe.run(
            {"name": pod_name, "namespace": namespace, "kind": "pod"},
            context={"chained_from": self.name},
        )
        result.message = f"{reason}\n\n{result.message}"
        return result