import json

from tools import (
    kubectl_cmd, get_pod_events, get_container_waiting_reasons,
    categorize_failure, get_pod_pvcs, get_pvc_status,
    get_resource_yaml, get_deployment_pods, get_logs,
)
import re
from .base import Skill, SkillResult
from .resolver import Resolver

CATEGORY_PRIORITY = [
    "ImagePull", "Config", "CrashLoop", "OOM", "Readiness", "Scheduling", "Volume", "Init", "Unknown",
]

SUGGESTIONS_BY_CATEGORY = {
    "ImagePull": [
        {"type": "command", "label": "Check image name", "command": "kubectl describe pod {name} -n {ns}"},
        {"type": "command", "label": "Verify registry credentials", "command": "kubectl get secret -n {ns}"},
        {"type": "skill", "label": "Check pod logs", "skill": "logs", "args": {"name": "{name}", "namespace": "{ns}"}},
    ],
    "CrashLoop": [
        {"type": "skill", "label": "View recent logs", "skill": "logs", "args": {"name": "{name}", "namespace": "{ns}", "errors_only": True}},
        {"type": "skill", "label": "Restart the pod", "skill": "restart", "args": {"name": "{name}", "namespace": "{ns}"}},
    ],
    "Scheduling": [
        {"type": "command", "label": "Check node resources", "command": "kubectl top nodes"},
        {"type": "command", "label": "Describe node", "command": "kubectl describe node {node}"},
        {"type": "command", "label": "Check taints", "command": "kubectl get nodes -o json | jq '.items[] | {{name: .metadata.name, taints: .spec.taints}}'"},
    ],
    "Volume": [
        {"type": "command", "label": "Check PVC status", "command": "kubectl get pvc {pvc} -n {ns}"},
        {"type": "command", "label": "Describe PVC", "command": "kubectl describe pvc {pvc} -n {ns}"},
        {"type": "command", "label": "List StorageClasses", "command": "kubectl get storageclass"},
    ],
    "Config": [
        {"type": "command", "label": "Check ConfigMaps", "command": "kubectl get configmap -n {ns}"},
        {"type": "command", "label": "Check Secrets", "command": "kubectl get secret -n {ns}"},
    ],
    "OOM": [
        {"type": "command", "label": "Check resource limits", "command": "kubectl describe pod {name} -n {ns} | grep -A 2 Limits"},
        {"type": "command", "label": "View resource usage", "command": "kubectl top pod {name} -n {ns}"},
    ],
    "Readiness": [
        {"type": "command", "label": "Describe pod for probe details", "command": "kubectl describe pod {name} -n {ns}"},
        {"type": "command", "label": "Check pod logs", "command": "kubectl logs {name} -n {ns}"},
        {"type": "command", "label": "Check endpoints", "command": "kubectl get endpoints -n {ns}"},
    ],
    "Init": [
        {"type": "command", "label": "Check init container logs", "command": "kubectl logs {name} -c ICNAME -n {ns}"},
        {"type": "command", "label": "Describe pod", "command": "kubectl describe pod {name} -n {ns}"},
    ],
    "Unknown": [
        {"type": "command", "label": "Describe pod for details", "command": "kubectl describe pod {name} -n {ns}"},
        {"type": "command", "label": "Check pod logs", "command": "kubectl logs {name} -n {ns}"},
    ],
}


class DiagnoseSkill(Skill):
    name = "diagnose"
    version = "1.0.0"
    triggers = ["diagnose", "why", "root cause", "issue", "error", "problem", "broken"]
    description = "Root-cause diagnosis for unhealthy pods and workloads."

    AUTO_PROCEED_SCORE = 0.90
    RUNNING_PHASES = {"Running", "Pending"}

    def execute(self, args, context):
        name = args.get("name")
        namespace = args.get("namespace")
        kind_arg = args.get("kind")
        preferred_ns = context.get("last_namespace")

        if not name:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error", message="Missing resource name for diagnosis.",
            )

        self._log_step("resolve", name=name, kind=kind_arg)

        if kind_arg and kind_arg.lower() != "pod":
            res = Resolver.resolve(name, kind_arg, namespace, preferred_ns)
            if res["status"] == "not_found":
                wl = Resolver.resolve_workload(name, namespace, preferred_ns)
                if wl["status"] in ("exact", "suggestions"):
                    res = wl
            if res["status"] == "not_found":
                return SkillResult(
                    skill_name=self.name, skill_version=self.version,
                    status="not_found",
                    message=f"No {kind_arg} matching '{name}' found to diagnose.",
                )
            if res["status"] == "suggestions":
                best = res["best_guess"]
                if best.get("score", 0) < self.AUTO_PROCEED_SCORE:
                    suggestions = [
                        f"{m['name']} ({m.get('kind', kind_arg)}, ns={m['namespace']})"
                        for m in res["matches"][:5]
                    ]
                    return SkillResult(
                        skill_name=self.name, skill_version=self.version,
                        status="needs_confirmation",
                        message="Which resource did you mean?",
                        suggestions=suggestions,
                        data={"matches": res["matches"][:5]},
                    )
                target = best
            else:
                target = res["best_guess"]

            target_name = target["name"]
            target_ns = target["namespace"]
            target_kind = target.get("kind", kind_arg)

            if target_kind.lower() != "pod":
                pods = self._get_workload_pods(target_kind, target_name, target_ns)
                if not pods:
                    return SkillResult(
                        skill_name=self.name, skill_version=self.version,
                        status="success",
                        message=f"No pods found for {target_kind} '{target_name}' (ns={target_ns}). The workload may be scaled to 0 or not yet created.",
                    )
                return self._diagnose_pods(pods, target_kind, target_name, target_ns)
            else:
                return self._diagnose_pod(target_name, target_ns, raw=target.get("raw"))

        res = Resolver.resolve_workload(name, namespace, preferred_ns)
        if res["status"] == "not_found":
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="not_found",
                message=f"No resource matching '{name}' found to diagnose.",
            )

        if res["status"] == "suggestions":
            best = res["best_guess"]
            if best.get("score", 0) < self.AUTO_PROCEED_SCORE:
                suggestions = [
                    f"{m['name']} ({m.get('kind', 'pod')}, ns={m['namespace']})"
                    for m in res["matches"][:5]
                ]
                return SkillResult(
                    skill_name=self.name, skill_version=self.version,
                    status="needs_confirmation",
                    message="Which resource did you mean?",
                    suggestions=suggestions,
                    data={"matches": res["matches"][:5]},
                )
            target = best
        else:
            target = res["best_guess"]

        target_name = target["name"]
        target_ns = target["namespace"]
        target_kind = target.get("kind", "pod")

        if target_kind.lower() != "pod":
            pods = self._get_workload_pods(target_kind, target_name, target_ns)
            if not pods:
                return SkillResult(
                    skill_name=self.name, skill_version=self.version,
                    status="success",
                    message=f"No pods found for {target_kind} '{target_name}' (ns={target_ns}). The workload may be scaled to 0 or not yet created.",
                )
            return self._diagnose_pods(pods, target_kind, target_name, target_ns)
        else:
            return self._diagnose_pod(target_name, target_ns, raw=target.get("raw"))

    def _get_workload_pods(self, kind, name, namespace):
        if kind.lower() in ("deployment", "statefulset", "daemonset"):
            raw = get_resource_yaml(kind, name, namespace)
            if not raw:
                return []
            selector = raw.get("spec", {}).get("selector", {}).get("matchLabels", {})
            if not selector:
                return []
            label_selector = ",".join(f"{k}={v}" for k, v in selector.items())
            data = kubectl_cmd([
                "get", "pods", "-l", label_selector,
                "-n", namespace, "-o", "json",
            ])
            try:
                pods = json.loads(data)
                return pods.get("items", [])
            except:
                return []

    def _diagnose_pods(self, pods, kind, name, namespace):
        results = []
        for pod in pods:
            pod_name = pod.get("metadata", {}).get("name", "")
            d = self._analyze_pod(pod, pod_name, namespace)
            results.append(d)

        primary = None
        for cat in CATEGORY_PRIORITY:
            for r in results:
                if r["category"] == cat:
                    primary = r
                    break
            if primary:
                break

        if not primary:
            primary = results[0] if results else {"category": "Unknown", "pod_name": name, "namespace": namespace}

        report = self._build_report(primary, results, kind, name)
        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="success",
            message=report,
            data={
                "category": primary["category"],
                "pod_name": primary["pod_name"],
                "kind": kind,
                "name": name,
                "namespace": namespace,
                "all_pods": results,
            },
        )

    def _diagnose_pod(self, pod_name, namespace, raw=None):
        if not raw:
            raw = get_resource_yaml("pod", pod_name, namespace)
        if not raw:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="not_found",
                message=f"Pod '{pod_name}' not found (ns={namespace}).",
            )

        analysis = self._analyze_pod(raw, pod_name, namespace)
        report = self._build_report(analysis, [], "pod", pod_name)
        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="success",
            message=report,
            data={
                "category": analysis["category"],
                "pod_name": pod_name,
                "kind": "pod",
                "name": pod_name,
                "namespace": namespace,
                "all_pods": [analysis],
            },
        )

    def _fetch_pod_logs(self, pod_json, pod_name, namespace):
        container = ""
        containers = pod_json.get("spec", {}).get("containers", [])
        if containers:
            container = containers[0].get("name", "")
        raw = get_logs(pod_name, tail=200, namespace=namespace)
        if raw.startswith("Error"):
            return {"logs": "", "error_count": 0, "error_lines": [], "container": container}
        lines = raw.split("\n")
        noise_patterns = re.compile(
            r"(?i)(SafeHasSuperTypeMatcher|NoSuchTypeException|DEBUG\s|TRACE\s)"
        )
        error_keywords = re.compile(
            r"(?i)( ERROR | WARN | FATAL |exception|traceback|fail|fatal|panic|crash|oomkilled|timeout|refused|not found|permission denied)"
        )
        error_lines = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if noise_patterns.search(stripped):
                continue
            if error_keywords.search(stripped):
                error_lines.append({"number": i + 1, "text": stripped[:200]})
        return {
            "logs": raw[-3000:] if len(raw) > 3000 else raw,
            "error_count": len(error_lines),
            "error_lines": error_lines[:10],
            "container": container,
        }

    def _analyze_pod(self, pod_json, pod_name, namespace):
        category = categorize_failure(pod_json)
        phase = pod_json.get("status", {}).get("phase", "Unknown")
        log_data = self._fetch_pod_logs(pod_json, pod_name, namespace)
        events = get_pod_events(pod_name, namespace).get("items", [])
        container_reasons = get_container_waiting_reasons(pod_json)
        pvcs = get_pod_pvcs(pod_json)

        details = {
            "pod_name": pod_name,
            "namespace": namespace,
            "phase": phase,
            "category": category,
            "logs": log_data,
            "events": events[-5:],
            "container_reasons": container_reasons,
            "pvcs": pvcs,
            "node": pod_json.get("spec", {}).get("nodeName", "N/A"),
        }

        if pvcs:
            pvc_statuses = []
            for pvc_info in pvcs:
                pvc_raw = get_pvc_status(pvc_info["claimName"], namespace)
                if pvc_raw:
                    pvc_statuses.append({
                        "name": pvc_info["claimName"],
                        "phase": pvc_raw.get("status", {}).get("phase", "Unknown"),
                    })
            details["pvc_statuses"] = pvc_statuses

        return details

    def _build_conclusion(self, category, primary, pod_name, namespace):
        """Return a one-line human-readable conclusion."""
        phase = primary.get("phase", "")
        container_reasons = primary.get("container_reasons", {})
        not_ready = [c["name"] for c in container_reasons.get("containers", []) if not c.get("ready")]
        restarts = sum(c.get("restartCount", 0) for c in container_reasons.get("containers", []))
        init_issues = [c["name"] for c in container_reasons.get("initContainers", [])
                       if c.get("reason") not in ("Running", "Completed")]

        if category == "ImagePull":
            return f"Container image pull failed — pod '{pod_name}' cannot start because the container image could not be pulled."
        if category == "CrashLoop":
            return f"Pod '{pod_name}' is crash-looping ({restarts} restarts) — the application keeps starting then failing."
        if category == "OOM":
            return f"Pod '{pod_name}' was killed due to out-of-memory (OOM) — container exceeded its memory limit."
        if category == "Scheduling":
            return f"Pod '{pod_name}' cannot be scheduled — no node has enough resources or tolerates its taints."
        if category == "Volume":
            return f"Pod '{pod_name}' is waiting for a PersistentVolume — the PVC may be unbound or the storage class unavailable."
        if category == "Config":
            return f"Pod '{pod_name}' has a configuration error — a required ConfigMap or Secret may be missing."
        if category == "Init":
            return f"Pod '{pod_name}' has init container issues ({', '.join(init_issues)}) — the pod cannot start until these complete."
        if category == "Readiness" and not_ready:
            container_list = ", ".join(f"'{c}'" for c in not_ready)
            if restarts > 0:
                return f"Pod '{pod_name}' is Running but {container_list} not ready ({restarts} restarts) — the application may be starting slowly or failing probes."
            return f"Pod '{pod_name}' is Running but {container_list} not ready — the application may be failing readiness probes or still initializing."
        if phase == "Pending":
            return f"Pod '{pod_name}' is in Pending state — waiting to be scheduled or initialized."
        return f"Pod '{pod_name}' is in {phase} phase — needs investigation."

    def _build_report(self, primary, all_pods, kind, name):
        cat = primary["category"]
        ns = primary.get("namespace", "default")
        pod_name = primary.get("pod_name", name)

        if cat == "Unknown" and primary.get("phase") == "Running":
            cat = "Readiness"

        container_reasons = primary.get("container_reasons", {})
        all_ready = all(c.get("ready") for c in container_reasons.get("containers", [])) if container_reasons.get("containers") else False

        # If everything is ready & running, short-circuit with a simple message
        if all_ready and primary.get("phase") == "Running":
            lines = [f"✅ Pod **'{pod_name}'** is up and running (ns={ns})."]
            if container_reasons.get("initContainers"):
                lines.append(f"   Init containers completed successfully.")
            return "\n".join(lines)

        # Build human-readable conclusion
        lines = [
            f"🔍 Diagnosis for {kind} **'{name}'** (ns={ns})",
            f"━━━━━━━━━━━━━━━━━━━━━━",
        ]

        conclusion = self._build_conclusion(cat, primary, pod_name, ns)
        if conclusion:
            lines.append(f"**Conclusion:** {conclusion}")
            lines.append("")

        lines.append(f"**Pod:** `{pod_name}` | **Phase:** {primary.get('phase', 'Unknown')} | **Node:** {primary.get('node', 'N/A')}")

        if cat != "Readiness":
            lines.append(f"**Root Cause:** `{cat}`")

        lines.append("")

        log_data = primary.get("logs", {})
        error_lines = log_data.get("error_lines", [])
        if error_lines:
            lines.append(f"**📋 Log Errors:**")
            for err in error_lines[:5]:
                lines.append(f"  ❌ `{err['text'][:150]}`")
            lines.append("")

        events = primary.get("events", [])
        if events:
            warning_events = [e for e in events if e.get("type") == "Warning"]
            if warning_events:
                lines.append("**🔴 Relevant Events:**")
                for e in warning_events[-3:]:
                    reason = e.get("reason", "")
                    msg = e.get("message", "").split("\n")[0][:200]
                    lines.append(f"  ⚠️ [{reason}] {msg}")
                lines.append("")

        if container_reasons.get("containers"):
            lines.append("**📦 Container Status:**")
            for c in container_reasons["containers"]:
                reason = c.get("reason", "Running")
                rcount = c.get("restartCount", 0)
                ready = "✅" if c.get("ready") else "❌"
                lines.append(f"  {ready} `{c['name']}`: {reason} (restarts: {rcount})")
            lines.append("")

        if container_reasons.get("initContainers"):
            has_issues = any(c.get("reason") not in ("Running", "Completed") for c in container_reasons["initContainers"])
            if has_issues:
                lines.append("**🔧 Init Containers (issues):**")
                for c in container_reasons["initContainers"]:
                    reason = c.get("reason", "Running")
                    rcount = c.get("restartCount", 0)
                    lines.append(f"  ⚙️ `{c['name']}`: {reason} (restarts: {rcount})")
                lines.append("")

        pvc_statuses = primary.get("pvc_statuses", [])
        if pvc_statuses:
            lines.append("**💾 PVC Status:**")
            for p in pvc_statuses:
                icon = "✅" if p["phase"] == "Bound" else "❌"
                lines.append(f"  {icon} `{p['name']}`: {p['phase']}")
            lines.append("")

        lines.append("**💡 Suggested Actions:**")
        suggestions = [
            {"type": "command", "label": "View full pod logs",
             "command": f"kubectl logs {pod_name} -n {ns} --tail=500"},
        ] + SUGGESTIONS_BY_CATEGORY.get(cat, SUGGESTIONS_BY_CATEGORY["Unknown"])
        for s in suggestions:
            label = s["label"].replace("{name}", name).replace("{ns}", ns)
            if s["type"] == "command":
                cmd = s["command"]
                cmd = cmd.replace("{name}", pod_name)
                cmd = cmd.replace("{ns}", ns)
                cmd = cmd.replace("{node}", primary.get("node", ""))
                first_pvc = primary.get("pvcs", [{}])[0].get("claimName", "") if primary.get("pvcs") else ""
                cmd = cmd.replace("{pvc}", first_pvc)
                init_container_name = ""
                cr = primary.get("container_reasons", {})
                if cr.get("initContainers"):
                    init_container_name = cr["initContainers"][0].get("name", "")
                cmd = cmd.replace("ICNAME", init_container_name)
                lines.append(f"  • {label}: `{cmd}`")
            elif s["type"] == "skill":
                lines.append(f"  • {label}")
        lines.append("")

        total = len(all_pods)
        healthy = sum(1 for p in all_pods if p["category"] == "Unknown" and p.get("phase") == "Running")
        if total > 1:
            lines.append(f"📊 Summary: {healthy}/{total} pods healthy, {total - healthy} pods affected.")

        return "\n".join(lines)
