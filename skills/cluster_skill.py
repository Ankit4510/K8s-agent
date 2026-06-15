import re
from typing import Any, Dict, Optional

from cluster_store import add_cluster, get_active, get_cluster, list_clusters, set_active
from tools import gcloud_get_credentials, kubectl_cluster_info, run_command
from .base import Skill, SkillResult
from .resolver import Resolver


def _parse_did_you_mean(stderr: str) -> Optional[Dict]:
    match = re.search(r"Did you mean \[([^\]]+)\] in \[([^\]]+)\]", stderr)
    if match:
        return {"name": match.group(1), "zone": match.group(2)}
    return None


class ClusterSwitchSkill(Skill):
    name = "cluster_switch"
    version = "1.0.0"
    triggers = ["switch cluster", "change cluster", "list clusters", "use cluster"]
    description = "Switch between Kubernetes clusters or list known clusters."

    def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> SkillResult:
        action = args.get("action", "switch")
        cluster_name = args.get("cluster_name")
        zone = args.get("zone")
        project = args.get("project")

        if action == "list":
            return self._list_clusters()

        if action == "switch":
            if not cluster_name:
                if project:
                    return SkillResult(
                        skill_name=self.name, skill_version=self.version,
                        status="needs_confirmation",
                        message=f"Got it — project is `{project}`. Which cluster would you like to switch to?",
                        data={"matches": [], "pending_project": project, "pending_zone": zone},
                        suggestions=[f"Reply with the cluster name, e.g.: 'switch to <cluster-name>'"],
                    )
                return SkillResult(
                    skill_name=self.name, skill_version=self.version,
                    status="error",
                    message="Missing cluster name. Usage: switch to <cluster-name> [in zone <zone>] [project <project>]",
                )
            return self._switch(cluster_name, zone, project)

        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="error",
            message=f"Unknown cluster action '{action}'. Use 'switch' or 'list'.",
        )

    # ---------------- list ---------------- #

    def _list_clusters(self) -> SkillResult:
        clusters = list_clusters()
        if not clusters:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="success",
                message="No clusters configured yet. Use 'switch to <cluster-name>' to add one.",
                data={"clusters": []},
            )

        lines = ["**Known clusters:**"]
        for c in clusters:
            marker = " (active)" if c["active"] else ""
            zone_str = f"zone={c['zone']}" if c.get("zone") else "zone=?"
            proj_str = f" project={c['project']}" if c.get("project") else ""
            verified = " verified" if c.get("verified") else " not verified"
            lines.append(
                f"  - {c['name']}{marker} ({zone_str}{proj_str}{verified})"
            )
        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="success",
            message="\n".join(lines),
            data={"clusters": clusters},
        )

    # ---------------- switch ---------------- #

    def _resolve_zone_project(self, cluster_name: str, user_zone: Optional[str], user_project: Optional[str]):
        stored = get_cluster(cluster_name)
        self._log_step("lookup_stored", found=bool(stored), stored_zone=stored.get("zone") if stored else None)

        if user_zone:
            zone = user_zone
            zone_source = "user"
        elif stored and stored.get("zone"):
            zone = stored["zone"]
            zone_source = "stored"
        else:
            zone = "us-central1-f"
            zone_source = "default"

        if user_project:
            project = user_project
            project_source = "user"
        elif stored and stored.get("project"):
            project = stored["project"]
            project_source = "stored"
        else:
            project = None
            project_source = "default"

        self._log_step("resolve_zone", final_zone=zone, source=zone_source)
        self._log_step("resolve_project", final_project=project, source=project_source)
        return zone, project, zone_source, stored

    def _switch(self, cluster_name: str, zone: str = None, project: str = None) -> SkillResult:
        active = get_active()
        if active and active.lower() == cluster_name.strip().lower():
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="success",
                message=f"Already on cluster '{cluster_name}'.",
            )

        zone, project, zone_source, stored = self._resolve_zone_project(cluster_name, zone, project)

        # 1. Try gcloud get-credentials
        self._log_step("gcloud_attempt", zone=zone, project=project)
        result = gcloud_get_credentials(cluster_name, zone, project)

        if result.startswith("Error"):
            gcloud_err = result

            # Check for "Did you mean" hint
            hint = _parse_did_you_mean(gcloud_err)
            if hint and hint["zone"] != zone:
                self._log_step("did_you_mean_retry", suggested_zone=hint["zone"])
                result = gcloud_get_credentials(cluster_name, hint["zone"], project)
                if not result.startswith("Error"):
                    zone = hint["zone"]
                    add_cluster(cluster_name, zone=zone, project=project, verified=False)
                    set_active(cluster_name)
                    Resolver.clear_cache()
                    return SkillResult(
                        skill_name=self.name, skill_version=self.version,
                        status="success",
                        message=f"Switched to cluster '{cluster_name}' (corrected zone to {zone}).",
                        data={"cluster": cluster_name, "zone": zone, "project": project, "verified": False},
                    )

            # 2. Fallback: try kubectl config use-context
            self._log_step("kubectl_use_context", cluster=cluster_name)
            kube_result = run_command(
                ["kubectl", "config", "use-context", cluster_name], timeout=10
            )
            if not kube_result.startswith("Error"):
                add_cluster(cluster_name, zone=zone, project=project, verified=False)
                set_active(cluster_name)
                Resolver.clear_cache()
                return SkillResult(
                    skill_name=self.name, skill_version=self.version,
                    status="success",
                    message=f"Context set to '{cluster_name}' via kubectl.{zone_project_hint(zone, project)}",
                    data={"cluster": cluster_name, "zone": zone, "project": project, "verified": False},
                )

            # Both failed — build helpful error
            return self._build_error(cluster_name, gcloud_err, kube_result, zone, project, zone_source, stored)

        # 3. Verify with kubectl cluster-info
        self._log_step("verify", cluster=cluster_name)
        info = kubectl_cluster_info()
        if info.startswith("Error"):
            add_cluster(cluster_name, zone=zone, project=project, verified=False)
            set_active(cluster_name)
            Resolver.clear_cache()
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="success",
                message=f"Context set to '{cluster_name}' but cluster-info failed. You can still try commands.",
                data={"cluster": cluster_name, "verified": False},
            )

        add_cluster(cluster_name, zone=zone, project=project, verified=True)
        set_active(cluster_name)
        Resolver.clear_cache()

        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="success",
            message=f"Switched to cluster '{cluster_name}'.",
            data={"cluster": cluster_name, "zone": zone, "project": project, "verified": True},
        )

    def _build_error(self, cluster_name, gcloud_err, kube_result, zone, project, zone_source, stored):
        if not project:
            msg = (
                f"❌ Could not connect to '{cluster_name}' — GCP project is not set.\n"
                f"Please provide the project: "
                f"'switch to {cluster_name} in zone {zone} project <your-project>'"
            )
        elif zone_source == "stored" and stored.get("verified"):
            msg = (
                f"❌ Could not connect to '{cluster_name}' (zone: {zone}, project: {project}).\n"
                f"This zone is saved as verified — likely an auth or network issue.\n"
                f"Try: gcloud auth login"
            )
        elif zone_source == "default":
            msg = (
                f"❌ Cluster '{cluster_name}' not found in default zone ({zone}).\n"
                f"Please specify the zone and project: "
                f"'switch to {cluster_name} in zone <zone> project <project>'"
            )
        else:
            msg = (
                f"❌ Could not switch to cluster '{cluster_name}'.\n"
                f"gcloud: {gcloud_err}\n"
                f"kubectl: {kube_result}"
            )
        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="error",
            message=msg,
        )


def zone_project_hint(zone, project):
    parts = []
    if zone:
        parts.append(f"zone={zone}")
    if project:
        parts.append(f"project={project}")
    return f" ({', '.join(parts)})" if parts else ""
