"""
Shared fuzzy-matching + resolution engine.
Used by every skill to find resources before acting on them.

Resolution order (when no explicit kind is given):
  deployment → statefulset → daemonset → pod
Users typically mean workloads, not individual pods.
"""

import json
import time
from difflib import get_close_matches, SequenceMatcher
from typing import Dict, List, Optional, Tuple

from tools import kubectl_cmd

# Module-level cache (use Redis in production via memory.py)
_CACHE: Dict[str, Tuple[float, list]] = {}
_CACHE_TTL = 30  # seconds
FUZZY_THRESHOLD = 0.75

COMMON_SUFFIXES = [" rest component", " rest container", " component", " container", " service", " rest"]
PREFIXES = ["all-com-manh-cp-", "com-manh-cp-"]
MANH_PREFIX_ALIASES = ["component-", "comp-", "manh-"]


def _strip_suffixes(name: str) -> str:
    lowered = name.lower()
    for suffix in COMMON_SUFFIXES:
        if lowered.endswith(suffix):
            return name[:len(name) - len(suffix)]
    return name


def _has_prefix(name: str) -> bool:
    lowered = name.lower()
    for p in PREFIXES:
        if lowered.startswith(p):
            return p
    return None


class Resolver:
    """Resolves resource names with fuzzy matching + 'did you mean?' logic."""

    @staticmethod
    def _cache_get(key: str):
        entry = _CACHE.get(key)
        if not entry:
            return None
        ts, data = entry
        if time.time() - ts > _CACHE_TTL:
            _CACHE.pop(key, None)
            return None
        return data

    @staticmethod
    def _cache_set(key: str, value: list):
        _CACHE[key] = (time.time(), value)

    # ---------------- list resources ---------------- #

    @staticmethod
    def list_resources(kind: str, namespace: Optional[str] = None) -> List[Dict]:
        """List resources of a kind, with caching."""
        cache_key = f"{kind}:{namespace or '__all__'}"
        cached = Resolver._cache_get(cache_key)
        if cached is not None:
            return cached

        cmd = ["get", kind, "-o", "json"]
        if namespace:
            cmd.extend(["-n", namespace])
        else:
            cmd.append("--all-namespaces")

        raw = kubectl_cmd(cmd)
        try:
            data = json.loads(raw)
            items = data.get("items", [])
        except Exception:
            items = []

        simplified = [
            {
                "name": it.get("metadata", {}).get("name"),
                "namespace": it.get("metadata", {}).get("namespace", "default"),
                "status": it.get("status", {}).get("phase")
                          or it.get("status", {}).get("conditions", [{}])[0].get("type", "Unknown"),
                "kind": kind,
                "raw": it,
            }
            for it in items
            if it.get("metadata", {}).get("name")
        ]
        Resolver._cache_set(cache_key, simplified)
        return simplified

    # ---------------- core resolve ---------------- #

    @staticmethod
    def resolve(
            name: str,
            kind: str = "pod",
            namespace: Optional[str] = None,
            preferred_namespace: Optional[str] = None,
    ) -> Dict:
        """
        Returns:
        {
          "status": "exact" | "suggestions" | "not_found",
          "matches": [ {name, namespace, status, kind, score}, ... ],
          "best_guess": {...} | None,
        }
        """
        # 0. Strip common suffixes like " component", " service"
        stripped = _strip_suffixes(name)
        names_to_try = [name]
        if stripped != name:
            names_to_try = [name, stripped]

        # Expand Manhattan prefix aliases:
        # "component-order" or "comp-order" → also try "order" and "com-manh-cp-order"
        extra = []
        for variant in list(names_to_try):
            lowered = variant.lower()
            for alias in MANH_PREFIX_ALIASES:
                if lowered.startswith(alias):
                    short = variant[len(alias):]
                    if short not in names_to_try:
                        extra.append(short)
                    manh = "com-manh-cp-" + short
                    if manh not in names_to_try:
                        extra.append(manh)
                    break
        names_to_try = names_to_try + extra

        # 1. Try in given namespace first
        candidates = Resolver.list_resources(kind, namespace)

        # 2. If empty + no namespace given, try all namespaces (already done above)
        if not candidates and namespace:
            candidates = Resolver.list_resources(kind, None)

        if not candidates:
            return {"status": "not_found", "matches": [], "best_guess": None}

        names = [c["name"] for c in candidates]

        # Exact match (try each stripped variant)
        for try_name in names_to_try:
            exact = [c for c in candidates if c["name"] == try_name]
            if exact:
                if preferred_namespace:
                    exact.sort(key=lambda c: 0 if c["namespace"] == preferred_namespace else 1)
                return {"status": "exact", "matches": exact, "best_guess": exact[0]}

            # Prefix-aware exact match: try "com-manh-cp-<name>", "all-com-manh-cp-<name>"
            for prefix in ["com-manh-cp-", "all-com-manh-cp-"]:
                prefixed = prefix + try_name
                exact_prefixed = [c for c in candidates if c["name"] == prefixed]
                if exact_prefixed:
                    if preferred_namespace:
                        exact_prefixed.sort(key=lambda c: 0 if c["namespace"] == preferred_namespace else 1)
                    return {"status": "exact", "matches": exact_prefixed, "best_guess": exact_prefixed[0]}

        # Substring match (try each stripped variant)
        for try_name in names_to_try:
            prefix = _has_prefix(try_name)
            if prefix:
                pool = [c for c in candidates if c["name"].lower().startswith(prefix)]
            else:
                pool = candidates
            substring = [c for c in pool if try_name.lower() in c["name"].lower()]
            if substring:
                scored = [
                    {**c, "score": SequenceMatcher(None, try_name, c["name"]).ratio()}
                    for c in substring
                ]
                scored.sort(
                    key=lambda c: (
                        0 if c["namespace"] == preferred_namespace else 1,
                        -c["score"],
                    )
                )
                return {
                    "status": "suggestions",
                    "matches": scored,
                    "best_guess": scored[0],
                }

        # Fuzzy match (difflib) — try each variant
        for try_name in names_to_try:
            prefix = _has_prefix(try_name)
            if prefix:
                names_for_fuzzy = [c["name"] for c in candidates if c["name"].lower().startswith(prefix)]
            else:
                names_for_fuzzy = names
            close = get_close_matches(try_name, names_for_fuzzy, n=5, cutoff=FUZZY_THRESHOLD)
            if close:
                scored = []
                for cn in close:
                    for c in candidates:
                        if c["name"] == cn:
                            scored.append({
                                **c,
                                "score": SequenceMatcher(None, try_name, cn).ratio(),
                            })
                            break
                scored.sort(
                    key=lambda c: (
                        0 if c["namespace"] == preferred_namespace else 1,
                        -c["score"],
                    )
                )
                return {
                    "status": "suggestions",
                    "matches": scored,
                    "best_guess": scored[0],
                }

        return {"status": "not_found", "matches": [], "best_guess": None}

    # ---------------- helper: resolve pod across kinds ---------------- #

    @staticmethod
    def resolve_workload(
            name: str,
            namespace: Optional[str] = None,
            preferred_namespace: Optional[str] = None,
    ) -> Dict:
        """Try resolving as deployment/statefulset first (users usually mean these), then pod."""
        for kind in ["deployment", "statefulset", "daemonset", "pod"]:
            res = Resolver.resolve(name, kind, namespace, preferred_namespace)
            if res["status"] == "exact":
                return res
        # If no exact, return best fuzzy across all kinds
        best = None
        for kind in ["deployment", "statefulset", "daemonset", "pod"]:
            res = Resolver.resolve(name, kind, namespace, preferred_namespace)
            if res["status"] == "suggestions":
                if not best or (
                        res["best_guess"]["score"] > best["best_guess"]["score"]
                ):
                    best = res
        return best or {"status": "not_found", "matches": [], "best_guess": None}

    @staticmethod
    def clear_cache():
        """Clear all cached resource listings. Call after cluster switch."""
        _CACHE.clear()