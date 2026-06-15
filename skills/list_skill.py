"""
ListSkill — lists Kubernetes resources with intelligent matching.

Key features:
  - Auto-detect kind when kind=null (iterates pod -> deployment -> statefulset -> daemonset -> pvc -> service)
  - Prefix-aware name matching: strips/prepends com-manh-cp- and all-com-manh-cp- prefixes
  - First-char guard: fuzzy matching only when query[0] == name[0]
  - REST keyword detection: "rest", "rest component", "rest container", "rest stereotype"
  - Running-pods filter: only Running/Pending pods shown
  - Suggestion footer: pod lists include prefix/REST keyword suggestions
  - Single-pass best-kind search with early exit on exact match (score >= 1.0)
  - Cross-kind fallback with scaling suggestion when nothing found
  - Returns compact markdown tables, never raw kubectl JSON
"""

import time
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from cluster_store import get_active
from tools import kubectl_cmd
from .base import Skill, SkillResult
from .resolver import Resolver

SUPPORTED_KINDS = {"deployment", "statefulset", "daemonset", "pod", "pvc", "service"}
WORKLOAD_KINDS = ["deployment", "statefulset", "daemonset"]
ALL_KINDS_PRIORITY = ["pod", "deployment", "statefulset", "daemonset", "pvc", "service"]
FUZZY_THRESHOLD = 0.40

PREFIXES = ["all-com-manh-cp-", "com-manh-cp-"]
RUNNING_PHASES = {"Running", "Pending"}
_REST_PATTERN = re.compile(r'\brest\b(?:\s+(?:component|container|stereotype))?', re.IGNORECASE)

KIND_FIELDS = {
    "deployment": ["name", "namespace", "ready", "age"],
    "statefulset": ["name", "namespace", "ready", "age"],
    "daemonset": ["name", "namespace", "ready", "age"],
    "pod": ["name", "namespace", "status", "restarts", "age"],
    "pvc": ["name", "namespace", "status", "capacity"],
    "service": ["name", "namespace", "type", "cluster_ip"],
}


def _age(ts: Optional[str]) -> str:
    if not ts:
        return "?"
    try:
        from datetime import datetime, timezone
        created = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        delta = time.time() - created.timestamp()
        if delta < 60:
            return f"{int(delta)}s"
        if delta < 3600:
            return f"{int(delta / 60)}m"
        if delta < 86400:
            return f"{int(delta / 3600)}h"
        return f"{int(delta / 86400)}d"
    except Exception:
        return "?"


def _strip_prefix(s: str):
    s_lower = s.lower()
    for p in PREFIXES:
        if s_lower.startswith(p):
            return s[len(p):], True, p
    return s, False, None


def _extract_rest_component(query: str):
    match = _REST_PATTERN.search(query)
    if match:
        remainder = _REST_PATTERN.sub("", query).strip()
        remainder = re.sub(r"\s+", " ", remainder).strip()
        return remainder, True
    return query, False


def _uses_prefix_or_rest(query: str) -> bool:
    if not query:
        return False
    q_lower = query.lower()
    for p in PREFIXES:
        if p in q_lower:
            return True
    return bool(_REST_PATTERN.search(query))


def _match_score(name: str, query: str) -> float:
    name_l = name.lower()
    query_l = query.lower()

    q_stripped, q_had_prefix, q_prefix = _strip_prefix(query_l)
    n_stripped, n_has_prefix, n_prefix = _strip_prefix(name_l)

    # ---- Query had a prefix (e.g. "com-manh-cp-order") ---- #
    # Only match names that also have the same prefix, preventing
    # "com-manh-cp-order" from matching "qp-…-order-…"
    if q_had_prefix:
        if not n_has_prefix:
            return 0.0
        if n_stripped == q_stripped:
            return 1.0
        if q_stripped in n_stripped:
            return 0.9
        return 0.0

    # ---- No prefix in query ---- #

    # 1. Direct exact match
    if name_l == query_l:
        return 1.0

    # 2. Direct substring match
    if query_l in name_l:
        base = len(query_l) / len(name_l)
        return min(0.95, 0.5 + base * 0.45)

    # 3. Name has prefix → match query against stripped name
    #    e.g. query="order", name="com-manh-cp-order" → stripped="order" → exact
    if n_has_prefix:
        if n_stripped == query_l:
            return 1.0
        if query_l in n_stripped:
            base = len(query_l) / len(n_stripped)
            return max(0.8, min(0.95, 0.5 + base * 0.45))
        return 0.0

    # 4. Try prepending known prefixes to query and matching full name
    #    e.g. query="order-api", name="com-manh-cp-order-api" → prefixed="com-manh-cp-order-api" == name
    for p in PREFIXES:
        prefixed = p + query_l
        if name_l == prefixed:
            return 1.0
        if prefixed in name_l:
            return 0.9

    # 5. Normal fuzzy — only when first character of both matches
    if query_l and name_l and query_l[0] == name_l[0]:
        return SequenceMatcher(None, query_l, name_l).ratio()
    return 0.0


def _filter_items(items: List[Dict], query: str) -> List[Dict]:
    scored = []
    for it in items:
        score = _match_score(it["name"], query)
        if score >= FUZZY_THRESHOLD:
            scored.append({**it, "score": round(score, 3)})
    scored.sort(key=lambda x: -x["score"])
    return scored


def _filter_running_pods(items: List[Dict]) -> List[Dict]:
    return [it for it in items if it.get("status") in RUNNING_PHASES]


def _find_best_kind(query: str, namespace: Optional[str]):
    best = None
    best_score = 0.0
    all_results = []
    for kind in ALL_KINDS_PRIORITY:
        items = Resolver.list_resources(kind, namespace)
        if kind == "pod":
            items = _filter_running_pods(items)
        filtered = _filter_items(items, query)
        if filtered:
            top_score = filtered[0]["score"]
            if top_score >= 1.0:
                return {"kind": kind, "items": filtered, "count": len(filtered)}, all_results
            if top_score > best_score:
                best_score = top_score
                best = {"kind": kind, "items": filtered, "count": len(filtered)}
            all_results.append({"kind": kind, "items": filtered, "count": len(filtered)})
    return best, all_results


def _match_across_kinds(query: str, namespace: Optional[str]) -> List[Dict]:
    results = []
    for kind in ALL_KINDS_PRIORITY:
        items = Resolver.list_resources(kind, namespace)
        if kind == "pod":
            items = _filter_running_pods(items)
        filtered = _filter_items(items, query)
        if filtered:
            results.append({"kind": kind, "items": filtered, "count": len(filtered)})
    return results


def _list_prefixed_resources(namespace: Optional[str]) -> List[Dict]:
    results = []
    for kind in ALL_KINDS_PRIORITY:
        items = Resolver.list_resources(kind, namespace)
        if kind == "pod":
            items = _filter_running_pods(items)
        matched = []
        for it in items:
            _, has_prefix, _ = _strip_prefix(it["name"])
            if has_prefix:
                matched.append(it)
        if matched:
            results.append({"kind": kind, "items": matched, "count": len(matched)})
    return results


def _pod_suggestion_footer() -> str:
    return (
        "\n---\n"
        "Not what you're looking for? Try:\n"
        "  \u2022 `list com-manh-cp-<name>` \u2014 exact prefix match for REST components\n"
        "  \u2022 `list <name> rest component` \u2014 REST stereotype keyword search"
    )


class ListSkill(Skill):
    name = "list"
    version = "1.0.0"
    triggers = ["list", "show all", "get all", "show me", "what are the"]
    description = "List Kubernetes resources with fuzzy name matching and cross-kind fallback."

    def _cluster_tag(self):
        c = get_active()
        return f" [cluster: {c}]" if c else ""

    def execute(self, args: Dict[str, Any], context: Dict[str, Any]) -> SkillResult:
        kind = args.get("kind")
        namespace = args.get("namespace")
        name_filter = args.get("name_filter")
        tag = self._cluster_tag()

        ns = namespace if namespace and namespace != "__all__" else None

        # ---- Detect REST stereotype query — extract component name ---- #
        if name_filter:
            comp_name, is_rest = _extract_rest_component(name_filter)
            if is_rest:
                name_filter = comp_name or None
                if not name_filter:
                    return self._list_prefixed(ns, tag)
                if not kind:
                    return self._auto_detect(name_filter or "", ns, tag, rest_query=True)
        else:
            is_rest = False

        # ---- No kind given → auto-detect best kind ---- #
        if not kind:
            if name_filter:
                return self._auto_detect(name_filter, ns, tag, rest_query=is_rest)
            return self._list_all_kinds(ns, tag)

        kind = kind.lower()
        if kind not in SUPPORTED_KINDS:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="error",
                message=f"Unsupported kind '{kind}'. Supported: {', '.join(sorted(SUPPORTED_KINDS))}.{tag}",
            )

        self._log_step("list_resources", kind=kind, namespace=ns or "all")
        items = Resolver.list_resources(kind, ns)

        if kind == "pod":
            items = _filter_running_pods(items)

        if name_filter:
            items = _filter_items(items, name_filter)

        if not items:
            return self._not_found_with_fallback(kind, name_filter, ns, tag)

        msg = self._build_response(kind, items, ns, name_filter, tag)
        if kind == "pod" and not _uses_prefix_or_rest(name_filter or ""):
            msg += _pod_suggestion_footer()
        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="success", message=msg,
            data={"kind": kind, "namespace": ns, "count": len(items), "items": items},
        )

    # ---------------- auto-detect (no kind given) ---------------- #

    def _auto_detect(self, name_filter: str, ns: Optional[str], tag: str, rest_query: bool = False) -> SkillResult:
        self._log_step("auto_detect", query=name_filter)
        best, all_results = _find_best_kind(name_filter, ns)
        if best:
            msg = self._build_response(best["kind"], best["items"], ns, name_filter, tag)
            if best["kind"] == "pod" and not _uses_prefix_or_rest(name_filter):
                msg += _pod_suggestion_footer()
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="success", message=msg,
                data={"kind": best["kind"], "namespace": ns, "count": best["count"], "items": best["items"]},
            )
        if all_results:
            return self._cross_kind_summary(all_results, name_filter, ns, tag)
        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="not_found",
            message=(
                f"Nothing found matching '{name_filter}'.{tag}\n"
                f"Please check the spelling. No pods or workloads with a similar name exist.\n"
                f"If this is a new service, use `scale up` to create it."
            ),
            data={"kind": None, "namespace": ns, "count": 0, "items": []},
        )

    def _list_all_kinds(self, ns: Optional[str], tag: str) -> SkillResult:
        all_results = []
        for kind in ALL_KINDS_PRIORITY:
            items = Resolver.list_resources(kind, ns)
            if kind == "pod":
                items = _filter_running_pods(items)
            if items:
                all_results.append({"kind": kind, "items": items, "count": len(items)})
        if not all_results:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="not_found",
                message=f"No resources found in any kind.{tag}",
                data={"kind": None, "namespace": ns, "count": 0, "items": []},
            )
        lines = [f"**Resources in cluster**{tag}"]
        for r in all_results:
            lines.append(f"\n{r['kind']}s ({r['count']}):")
            rows = [self._format_row(r["kind"], it) for it in r["items"][:10]]
            table = self._build_table(r["kind"], rows)
            lines.append(table)
            if r["count"] > 10:
                lines.append(f"  ... and {r['count'] - 10} more")
        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="success",
            message="\n".join(lines),
            data={"kind": None, "namespace": ns, "count": sum(r["count"] for r in all_results), "items": []},
        )

    # ---------------- list all prefixed resources (rest stereotype alone) ---------------- #

    def _list_prefixed(self, ns: Optional[str], tag: str) -> SkillResult:
        results = _list_prefixed_resources(ns)
        if not results:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="not_found",
                message=f"No REST stereotype resources found (matching any prefix).{tag}",
                data={"kind": None, "namespace": ns, "count": 0, "items": []},
            )
        total = sum(r["count"] for r in results)
        lines = [f"**REST stereotype resources ({total})**{tag}"]
        for r in results:
            lines.append(f"\n{r['kind']}s ({r['count']}):")
            rows = [self._format_row(r["kind"], it) for it in r["items"][:10]]
            lines.append(self._build_table(r["kind"], rows))
            if r["count"] > 10:
                lines.append(f"  ... and {r['count'] - 10} more")
        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="success",
            message="\n".join(lines),
            data={"kind": None, "namespace": ns, "count": total, "items": []},
        )

    # ---------------- fallback when nothing found ---------------- #

    def _not_found_with_fallback(self, kind: str, name_filter: Optional[str], ns: Optional[str], tag: str) -> SkillResult:
        ns_msg = " in any namespace" if ns is None else f" in namespace '{ns}'"
        if name_filter:
            ns_msg += f" matching '{name_filter}'"

        fallback = _match_across_kinds(name_filter or "", ns) if name_filter else []
        fallback = [r for r in fallback if r["kind"] != kind]
        if fallback:
            lines = [f"No {kind}s found{ns_msg}.{tag}"]
            for r in fallback[:2]:
                lines.append(f"\nFound {r['count']} {r['kind']}(s) with similar name:")
                rows = [self._format_row(r["kind"], it) for it in r["items"][:5]]
                lines.append(self._build_table(r["kind"], rows))
            if kind == "pod":
                lines.append("\nNo pods running. Use `scale up` or `restart` a deployment to create pods.")
                lines.append("If you meant to check pod health (restarting, unhealthy, crashlooping), use `status` instead.")
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="not_found",
                message="\n".join(lines),
                data={"kind": kind, "namespace": ns, "count": 0, "items": []},
            )

        if kind in WORKLOAD_KINDS:
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="not_found",
                message=f"No {kind}s found{ns_msg}.{tag} Nothing with a similar name exists.",
            )

        if kind == "pod":
            return SkillResult(
                skill_name=self.name, skill_version=self.version,
                status="not_found",
                message=f"No pods found{ns_msg}.{tag} No pods running. Use `scale up` or `restart` a deployment to create pods.\nIf you meant to check pod health (restarting, unhealthy, crashlooping), use `status` instead.",
            )

        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="not_found",
            message=f"No {kind}s found{ns_msg}.{tag}",
            data={"kind": kind, "namespace": ns, "count": 0, "items": []},
        )

    # ---------------- cross-kind summary ---------------- #

    def _cross_kind_summary(self, results: List[Dict], name_filter: str, ns: Optional[str], tag: str) -> SkillResult:
        lines = [f"**Results matching '{name_filter}' across kinds**{tag}"]
        total = 0
        for r in results:
            total += r["count"]
            rows = [self._format_row(r["kind"], it) for it in r["items"][:5]]
            lines.append(f"\n{r['kind']}s ({r['count']}):")
            lines.append(self._build_table(r["kind"], rows))
            if r["count"] > 5:
                lines.append(f"  ... and {r['count'] - 5} more")
        return SkillResult(
            skill_name=self.name, skill_version=self.version,
            status="success",
            message="\n".join(lines),
            data={"kind": None, "namespace": ns, "count": total, "items": []},
        )

    # ---------------- build response ---------------- #

    def _build_response(self, kind: str, items: List[Dict], ns: Optional[str], name_filter: Optional[str], tag: str) -> str:
        rows = [self._format_row(kind, it) for it in items]

        header = f"**{len(items)} {kind}(s) found**{tag}"
        if name_filter:
            header += f" matching '{name_filter}'"

        table = self._build_table(kind, rows)

        return f"{header}\n{table}"

    # ---------------- row / table formatting ---------------- #

    def _format_row(self, kind: str, item: Dict) -> Dict:
        raw = item.get("raw", {})
        status = item.get("status", "Unknown")
        meta = raw.get("metadata", {})

        row = {
            "name": item["name"],
            "namespace": item["namespace"],
            "kind": kind,
        }

        if kind in ("deployment", "statefulset", "daemonset"):
            spec = raw.get("spec", {})
            desired = spec.get("replicas", 0)
            ready = raw.get("status", {}).get("readyReplicas", 0)
            if "availableReplicas" in raw.get("status", {}):
                ready = raw["status"].get("availableReplicas", ready)
            row["ready"] = f"{ready}/{desired}"
            row["age"] = _age(meta.get("creationTimestamp"))

        elif kind == "pod":
            ctrs = raw.get("status", {}).get("containerStatuses", [])
            restarts = sum(c.get("restartCount", 0) for c in ctrs)
            ready_count = sum(1 for c in ctrs if c.get("ready"))
            total_count = len(ctrs) or 1
            row["status"] = f"{status} ({ready_count}/{total_count})"
            row["restarts"] = str(restarts)
            row["age"] = _age(meta.get("creationTimestamp"))

        elif kind == "pvc":
            cap = raw.get("status", {}).get("capacity", {}).get("storage", "?")
            row["status"] = status
            row["capacity"] = cap

        elif kind == "service":
            spec = raw.get("spec", {})
            row["type"] = spec.get("type", "ClusterIP")
            row["cluster_ip"] = spec.get("clusterIP", "None")

        return row

    def _build_table(self, kind: str, rows: List[Dict]) -> str:
        fields = KIND_FIELDS.get(kind, ["name", "namespace"])
        header = " | ".join(fields)
        sep = "-|-".join("---" for _ in fields)

        lines = []
        for r in rows:
            vals = []
            for f in fields:
                v = r.get(f, "?")
                vals.append(str(v))
            lines.append(" | ".join(vals))

        return f"| {header} |\n| {sep} |\n| " + " |\n| ".join(lines) + " |"
