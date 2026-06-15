import re
from typing import Any, Dict, Optional

from cluster_store import get_active
from tools import get_container_resources, patch_container_resources, set_java_opts
from .base import Skill, SkillResult
from .resolver import Resolver


PATCHABLE_KINDS = {"deployment", "statefulset"}


def normalize_cpu(value: Optional[str]) -> Optional[str]:
    """Normalize natural language CPU to Kubernetes format.
    '1 cpu' | '1 core' | '1'   → '1'
    '0.5 cpu' | '0.5 cores'    → '500m'
    '250m'                     → '250m' (passthrough)
    '256 millicores'           → '256m'
    """
    if not value:
        return None
    v = str(value).strip().lower()
    v = re.sub(r"\s*(cpu|core|cores|vcpu|vcpus)\s*", "", v).strip()
    v = re.sub(r"\s*(millicores?|mc)\s*$", "m", v).strip()
    if re.fullmatch(r"\d+m", v):
        return v
    try:
        num = float(v)
        if 0 < num < 1:
            return f"{int(round(num * 1000))}m"
        return str(int(num)) if num == int(num) else str(num)
    except ValueError:
        return str(value).strip()


def normalize_memory(value: Optional[str]) -> Optional[str]:
    """Normalize natural language memory to Kubernetes format.
    '2GB' | '2 GB' | '2G'  → '2Gi'
    '512MB' | '512 mb'     → '512Mi'
    '4Gi' | '512Mi'        → unchanged (already k8s format)
    """
    if not value:
        return None
    v = str(value).strip()
    # Already valid k8s binary units — normalize casing only
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti)", v, re.IGNORECASE)
    if m:
        unit_map = {"KI": "Ki", "MI": "Mi", "GI": "Gi", "TI": "Ti"}
        return m.group(1) + unit_map.get(m.group(2).upper(), m.group(2))
    # SI units: GB→Gi, MB→Mi, G→Gi, M→Mi
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(gb?|mb?|kb?|tb?)", v, re.IGNORECASE)
    if m:
        num, unit = m.group(1), m.group(2).upper()
        unit_map = {"G": "Gi", "GB": "Gi", "M": "Mi", "MB": "Mi",
                    "K": "Ki", "KB": "Ki", "T": "Ti", "TB": "Ti"}
        return num + unit_map.get(unit, unit)
    return v


def normalize_heap(value: Optional[str]) -> Optional[str]:
    """Normalize heap memory to JVM -Xmx format.
    '2048m' | '1g'       → unchanged (already JVM format)
    '2GB' | '2G' | '2g' → '2g'
    '2048MB' | '2048mb'  → '2048m'
    '2048'               → '2048m' (assume megabytes)
    """
    if not value:
        return None
    v = str(value).strip().lower()
    # GB/G → g (JVM gigabytes)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(gb?|g)", v)
    if m:
        return f"{m.group(1)}g"
    # MB/M → m (JVM megabytes)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(mb?|m)", v)
    if m:
        return f"{m.group(1)}m"
    # Plain number — assume megabytes
    if re.fullmatch(r"\d+", v):
        return f"{v}m"
    return v


class ResourcesSkill(Skill):
    name = "resources"
    version = "1.0.0"
    triggers = ["cpu", "memory", "heap", "xmx", "ram", "resources", "set cpu", "set memory", "set heap"]
    description = (
        "Adjust CPU/memory requests+limits or JVM heap (-Xmx) for a deployment or statefulset. "
        "Reads current values first, then patches only what the user specified."
    )

    AUTO_PROCEED_SCORE = 0.90

    def _cluster_tag(self):
        c = get_active()
        return f" [cluster: {c}]" if c else ""

    def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> SkillResult:
        name = args.get("name")
        namespace = args.get("namespace")
        explicit_kind = args.get("kind")
        preferred_ns = context.get("last_namespace")
        tag = self._cluster_tag()

        # Normalize user input to k8s / JVM formats
        cpu_request = normalize_cpu(args.get("cpu_request"))
        cpu_limit   = normalize_cpu(args.get("cpu_limit"))
        mem_request = normalize_memory(args.get("mem_request"))
        mem_limit   = normalize_memory(args.get("mem_limit"))
        heap        = normalize_heap(args.get("heap"))

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
                message="Please tell me which deployment to update, e.g. 'set cpu of order to 512m'.",
            )

        if not any([cpu_request, cpu_limit, mem_request, mem_limit, heap]):
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error",
                message=(
                    f"What would you like to change for '{name}'?\n"
                    "Examples:\n"
                    "• `set cpu of {name} to 512m`\n"
                    "• `set memory of {name} to 4Gi`\n"
                    "• `set heap of {name} to 2048m`"
                ),
            )

        # Resolve workload — user doesn't need to know deployment vs statefulset
        kind_hint = explicit_kind.lower() if explicit_kind and explicit_kind.lower() in PATCHABLE_KINDS else None
        if kind_hint:
            res = Resolver.resolve(name, kind_hint, namespace, preferred_ns)
        else:
            res = Resolver.resolve_workload(name, namespace, preferred_ns)

        if res["status"] == "not_found":
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="not_found",
                message=f"No deployment or statefulset matching '{name}' found.{tag}\nTip: 'list deployments' to see what's available.",
            )

        if res["status"] == "suggestions":
            best = res["best_guess"]
            if best.get("score", 0) < self.AUTO_PROCEED_SCORE:
                matches = res["matches"][:5]
                return SkillResult(
                    skill_name=self.name, skill_version=self.version,
                    status="needs_confirmation",
                    message=f"I found a few possible matches for '{name}'. Which one did you mean?{tag}",
                    suggestions=[f"{m['name']} ({m.get('kind','?')}, ns={m['namespace']})" for m in matches],
                    data={"matches": matches},
                )

        target = res["best_guess"]
        target_kind = target.get("kind", "deployment")
        target_name = target["name"]
        target_ns = target["namespace"]

        if target_kind.lower() not in PATCHABLE_KINDS:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error",
                message=f"Resource adjustment is only supported for deployments and statefulsets, not '{target_kind}'.{tag}",
            )

        # Read current values to show user what will change
        current, err = get_container_resources(target_kind, target_name, target_ns)
        if not current:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error",
                message=f"Could not read current resources for '{target_name}': {err}{tag}",
            )

        self._log_step("resolved", kind=target_kind, name=target_name, ns=target_ns)

        # Build human-readable summary of current vs proposed changes
        changes = []
        if cpu_request:
            changes.append(f"CPU request: `{current['requests'].get('cpu','?')}` → `{cpu_request}`")
        if cpu_limit:
            changes.append(f"CPU limit: `{current['limits'].get('cpu','?')}` → `{cpu_limit}`")
        if mem_request:
            changes.append(f"Memory request: `{current['requests'].get('memory','?')}` → `{mem_request}`")
        if mem_limit:
            changes.append(f"Memory limit: `{current['limits'].get('memory','?')}` → `{mem_limit}`")
        if heap:
            old_xmx = "not set"
            if current.get("java_opts"):
                m = re.search(r"-Xmx(\S+)", current["java_opts"])
                old_xmx = m.group(1) if m else "not set"
            changes.append(f"Heap (-Xmx): `{old_xmx}` → `{heap}`")

        changes_str = "\n".join(f"  • {c}" for c in changes)

        approval_payload = {
            "cluster": get_active(),
            "skill": self.name,
            "action": "patch_resources",
            "kind": target_kind,
            "name": target_name,
            "namespace": target_ns,
            "container_name": current.get("container_name"),
            "cpu_request": cpu_request,
            "cpu_limit": cpu_limit,
            "mem_request": mem_request,
            "mem_limit": mem_limit,
            "heap": heap,
        }

        if context.get("approved"):
            return self._do_patch(approval_payload, tag)

        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="needs_approval",
            message=(
                f"Ready to update resources for **{target_kind}** `{target_name}` "
                f"in namespace `{target_ns}`.{tag}\n\n"
                f"**Changes:**\n{changes_str}\n\n"
                f"Note: CPU/memory changes trigger a rolling restart. Heap change updates `JAVA_OPTS` env var."
            ),
            requires_approval=True,
            approval_payload=approval_payload,
            data=approval_payload,
        )

    def _do_patch(self, payload: Dict[str, Any], tag: str) -> SkillResult:
        kind = payload["kind"]
        name = payload["name"]
        ns = payload["namespace"]
        container_name = payload.get("container_name")
        results = []
        errors = []

        # Patch CPU/memory if any were specified
        if any([payload.get("cpu_request"), payload.get("cpu_limit"),
                payload.get("mem_request"), payload.get("mem_limit")]):
            self._log_step("patch_resources", kind=kind, name=name, ns=ns)
            out = patch_container_resources(
                kind, name, ns, container_name,
                cpu_request=payload.get("cpu_request"),
                cpu_limit=payload.get("cpu_limit"),
                mem_request=payload.get("mem_request"),
                mem_limit=payload.get("mem_limit"),
            )
            if out.lower().startswith("error") or "error" in out.lower():
                errors.append(f"CPU/memory patch failed: {out}")
            else:
                results.append("✅ CPU/memory resources patched (rolling restart triggered)")

        # Update heap if specified
        if payload.get("heap"):
            self._log_step("set_java_opts", kind=kind, name=name, ns=ns, heap=payload["heap"])
            out, updated_opts = set_java_opts(kind, name, ns, payload["heap"])
            if out is None or (isinstance(out, str) and out.lower().startswith("error")):
                errors.append(f"Heap update failed: {out}")
            else:
                results.append(f"✅ Heap updated: `-Xmx{payload['heap']}` applied via `JAVA_OPTS`")

        if errors and not results:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error",
                message="\n".join(errors) + tag,
            )

        msg = "\n".join(results)
        if errors:
            msg += "\n\n⚠️ Partial failure:\n" + "\n".join(errors)
        msg += f"\n\nUse `is {name} up?` to verify the rollout.{tag}"

        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="success",
            message=msg,
            data={"kind": kind, "name": name, "namespace": ns},
        )
