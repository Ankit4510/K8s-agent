import json
import uuid

from cluster_store import get_active, get_cluster
from tools import AVAILABLE_TOOLS
from skills import SKILL_REGISTRY
from llm import call_llm, CHAT_SYSTEM_PROMPT, INTENT_SYSTEM_PROMPT
from approvals import approval_card
from memory import (
    get_user_session,
    save_user_session,
    get_pending_actions,
    save_pending_actions,
    get_pending_confirmation,
    save_pending_confirmation,
    clear_pending_confirmation,
    set_last_action,
    get_last_action,
)
from logger import log_action

SKILLS_SKIP_HUMANIZE = {"list", "status", "diagnose"}


def safe_parse_json(text):
    try:
        return json.loads(text)
    except:
        return None


# ---------------- INTENT DETECTION ---------------- #

def detect_intent(user_input, session=None, pending_confirm=None, last_action=None):
    try:
        skills_info = SKILL_REGISTRY.list_skills()
        skill_names = [s["name"] for s in skills_info]
        tools = "get_pods, get_pod_image, get_pvc_info, get_pv_info"

        recent = ""
        recent_context = ""
        if session and isinstance(session, list) and len(session) > 0:
            recent = "\n".join(session[-6:])
            recent_context = f"Recent conversation:\n{recent}"

        confirm_context = ""
        if pending_confirm:
            matches_str = "\n".join(
                f"  [{i}] {m['name']} (ns={m['namespace']})"
                for i, m in enumerate(pending_confirm["matches"][:5])
            )
            confirm_context = f"""
There is a PENDING CONFIRMATION for skill '{pending_confirm['skill']}':
Suggested matches:
{matches_str}
- If user confirms (yes/ok/do it/sure/that one), return mode "confirm" with confirm_index 0.
- If user picks a specific index (e.g. "second one"), set confirm_index accordingly.
- If user provides a different name, return the normal mode with that name.
"""

        prompt = f"""Classify this user request into one of these modes.

RULES:
- skill = user wants an action performed or resource queried (restart, delete, logs, describe, status, cluster_switch, list). Includes questions phrased as "can you", "please", "is X up", "how is X", "check X".
- tool = simple listing query (list pods, get PVC/PV info) — but use skill "list" for "list deployments", "list all pods" etc.
- chat = ONLY for greetings, thanks, how-are-you, or completely unrelated conversation. NEVER use chat if the user mentions any resource name or asks about any k8s resource.
- confirm = user confirming a suggested resource match (only during pending confirmation).
- unclear = user's query does not clearly match any skill, tool, chat, or confirm pattern. Do NOT force a guess.

Available skills: {', '.join(skill_names)}
{recent_context}{confirm_context}

User input: "{user_input}"

Examples:
- "restart prometheus"                 → {{"mode": "skill", "skill": "restart", "name": "prometheus", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "restart prometheus pod"             → {{"mode": "skill", "skill": "restart", "name": "prometheus", "kind": "pod", "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": true, "name_filter": null}}
- "restart grafana deployment"         → {{"mode": "skill", "skill": "restart", "name": "grafana", "kind": "deployment", "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "restart app-order"          → {{"mode": "skill", "skill": "restart", "name": "app-order", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "delete pod nginx-abc123"            → {{"mode": "skill", "skill": "delete_pod", "name": "nginx-abc123", "kind": "pod", "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "show logs for pod foo"              → {{"mode": "skill", "skill": "logs", "name": "foo", "kind": "pod", "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "show logs of qp-inventory-pod"      → {{"mode": "skill", "skill": "logs", "name": "qp-inventory-pod", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "check logs of foo"                  → {{"mode": "skill", "skill": "logs", "name": "foo", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "get logs for foo"                   → {{"mode": "skill", "skill": "logs", "name": "foo", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "what image is pod foo using"        → {{"mode": "skill", "skill": "describe", "name": "foo", "kind": "pod", "namespace": null, "field": "image", "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "is grafana deployment restarted"    → {{"mode": "skill", "skill": "describe", "name": "grafana", "kind": "deployment", "namespace": null, "field": "status", "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}

- "is prometheus down"                 → {{"mode": "skill", "skill": "describe", "name": "prometheus", "kind": null, "namespace": null, "field": "status", "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "is prometheus running"              → {{"mode": "skill", "skill": "describe", "name": "prometheus", "kind": null, "namespace": null, "field": "status", "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "is app-order down"          → {{"mode": "skill", "skill": "describe", "name": "app-order", "kind": null, "namespace": null, "field": "status", "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "is app-order running"       → {{"mode": "skill", "skill": "describe", "name": "app-order", "kind": null, "namespace": null, "field": "status", "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "is app-order down in my-cluster cluster" → {{"mode": "skill", "skill": "describe", "name": "app-order", "kind": null, "namespace": null, "field": "status", "cluster_name": "my-cluster", "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}

- "list pods"                          → {{"mode": "skill", "skill": "list", "name": null, "kind": "pod", "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "list all deployments"               → {{"mode": "skill", "skill": "list", "name": null, "kind": "deployment", "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "list deployments in monitoring"     → {{"mode": "skill", "skill": "list", "name": null, "kind": "deployment", "namespace": "monitoring", "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "list deployments in all namespaces"  → {{"mode": "skill", "skill": "list", "name": null, "kind": "deployment", "namespace": "__all__", "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "list deployments matching cp-order"  → {{"mode": "skill", "skill": "list", "name": null, "kind": "deployment", "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": "cp-order"}}
- "list all services"                  → {{"mode": "skill", "skill": "list", "name": null, "kind": "service", "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "show all pods"                      → {{"mode": "skill", "skill": "list", "name": null, "kind": "pod", "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "get all deployments"                → {{"mode": "skill", "skill": "list", "name": null, "kind": "deployment", "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "list app-order"             → {{"mode": "skill", "skill": "list", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": "app-order"}}
- "list app-order pod"         → {{"mode": "skill", "skill": "list", "name": null, "kind": "pod", "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": "app-order"}}
- "list order pods"                    → {{"mode": "skill", "skill": "list", "name": null, "kind": "pod", "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": "order"}}
- "list order rest component"         → {{"mode": "skill", "skill": "list", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": "order rest component"}}
- "list rest stereotype order"        → {{"mode": "skill", "skill": "list", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": "rest stereotype order"}}
- "list rest stereotype"              → {{"mode": "skill", "skill": "list", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": "rest stereotype"}}

- "any pod restarting"                → {{"mode": "skill", "skill": "status", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "any issues"                        → {{"mode": "skill", "skill": "status", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "what's broken"                     → {{"mode": "skill", "skill": "status", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "any pod restarting in my-cluster"  → {{"mode": "skill", "skill": "status", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": "my-cluster", "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "any issues in prod-us"             → {{"mode": "skill", "skill": "status", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": "prod-us", "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "list all restarting pods"          → {{"mode": "skill", "skill": "status", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "list all unhealthy pods"           → {{"mode": "skill", "skill": "status", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "list crashlooping pods"            → {{"mode": "skill", "skill": "status", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}

- "is prometheus up"                   → {{"mode": "skill", "skill": "describe", "name": "prometheus", "kind": null, "namespace": null, "field": "status", "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "is it up" (no resource name given) → {{"mode": "skill", "skill": "describe", "name": null, "kind": null, "namespace": null, "field": "status", "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "is order component up"             → {{"mode": "skill", "skill": "describe", "name": "order", "kind": "deployment", "namespace": null, "field": "status", "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "is order component running"        → {{"mode": "skill", "skill": "describe", "name": "order", "kind": "deployment", "namespace": null, "field": "status", "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "is order rest up"                  → {{"mode": "skill", "skill": "describe", "name": "order", "kind": "deployment", "namespace": null, "field": "status", "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}

- "switch to cluster prod-us"          → {{"mode": "skill", "skill": "cluster_switch", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": "prod-us", "zone": null, "project": null, "action": "switch", "explicit_pod": false, "name_filter": null}}
- "switch to cluster prod-eu zone us-central1-a" → {{"mode": "skill", "skill": "cluster_switch", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": "prod-eu", "zone": "us-central1-a", "project": null, "action": "switch", "explicit_pod": false, "name_filter": null}}
- "switch to freedom in zone us-central1-f project ma-omni-rd-om" → {{"mode": "skill", "skill": "cluster_switch", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": "freedom", "zone": "us-central1-f", "project": "ma-omni-rd-om", "action": "switch", "explicit_pod": false, "name_filter": null}}
- "switch to staging zone us-east1-b project my-project" → {{"mode": "skill", "skill": "cluster_switch", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": "staging", "zone": "us-east1-b", "project": "my-project", "action": "switch", "explicit_pod": false, "name_filter": null}}
- "use project ma-omni-rd-om"         → {{"mode": "skill", "skill": "cluster_switch", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": "ma-omni-rd-om", "action": "switch", "explicit_pod": false, "name_filter": null}}
- "use project my-project for freedom" → {{"mode": "skill", "skill": "cluster_switch", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": "freedom", "zone": null, "project": "my-project", "action": "switch", "explicit_pod": false, "name_filter": null}}
- "list clusters"                     → {{"mode": "skill", "skill": "cluster_switch", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": "list", "explicit_pod": false, "name_filter": null}}
- "what clusters do I have"           → {{"mode": "skill", "skill": "cluster_switch", "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": "list", "explicit_pod": false, "name_filter": null}}

- "restart prometheus in freedom cluster"   → {{"mode": "skill", "skill": "restart", "name": "prometheus", "kind": null, "namespace": null, "field": null, "cluster_name": "freedom", "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "restart prometheus on cluster prod-us"   → {{"mode": "skill", "skill": "restart", "name": "prometheus", "kind": null, "namespace": null, "field": null, "cluster_name": "prod-us", "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "delete pod nginx-abc123 in cluster staging" → {{"mode": "skill", "skill": "delete_pod", "name": "nginx-abc123", "kind": "pod", "namespace": null, "field": null, "cluster_name": "staging", "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}

- "scale order to 2"                        → {{"mode": "skill", "skill": "scale", "name": "order", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null, "replicas": 2}}
- "scale app-snh to 1"             → {{"mode": "skill", "skill": "scale", "name": "app-snh", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null, "replicas": 1}}
- "scale component-order to 3"             → {{"mode": "skill", "skill": "scale", "name": "component-order", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null, "replicas": 3}}
- "scale down prometheus"                  → {{"mode": "skill", "skill": "scale", "name": "prometheus", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null, "replicas": 0}}
- "scale up grafana to 5"                  → {{"mode": "skill", "skill": "scale", "name": "grafana", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null, "replicas": 5}}
- "set replicas of nginx to 4"             → {{"mode": "skill", "skill": "scale", "name": "nginx", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null, "replicas": 4}}
- "scale snh deployment to 1 in my-cluster cluster" → {{"mode": "skill", "skill": "scale", "name": "snh", "kind": "deployment", "namespace": null, "field": null, "cluster_name": "my-cluster", "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null, "replicas": 1}}

- "set cpu of order to 512m"               → {{"mode": "skill", "skill": "resources", "name": "order", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null, "cpu_request": "512m", "cpu_limit": null, "mem_request": null, "mem_limit": null, "heap": null}}
- "set memory of snh to 4Gi"               → {{"mode": "skill", "skill": "resources", "name": "snh", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null, "cpu_request": null, "cpu_limit": null, "mem_request": "4Gi", "mem_limit": null, "heap": null}}
- "set memory limit of order to 8Gi"       → {{"mode": "skill", "skill": "resources", "name": "order", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null, "cpu_request": null, "cpu_limit": null, "mem_request": null, "mem_limit": "8Gi", "heap": null}}
- "set heap of snh to 2048m"               → {{"mode": "skill", "skill": "resources", "name": "snh", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null, "cpu_request": null, "cpu_limit": null, "mem_request": null, "mem_limit": null, "heap": "2048m"}}
- "increase heap of prometheus to 4g"      → {{"mode": "skill", "skill": "resources", "name": "prometheus", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null, "cpu_request": null, "cpu_limit": null, "mem_request": null, "mem_limit": null, "heap": "4g"}}
- "set cpu to 256m and memory to 2Gi for snh" → {{"mode": "skill", "skill": "resources", "name": "snh", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null, "cpu_request": "256m", "cpu_limit": null, "mem_request": "2Gi", "mem_limit": null, "heap": null}}
- "reduce cpu limit of order to 1"         → {{"mode": "skill", "skill": "resources", "name": "order", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null, "cpu_request": null, "cpu_limit": "1", "mem_request": null, "mem_limit": null, "heap": null}}

- "hello"                              → {{"mode": "chat", "skill": null, "tool": null, "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "thanks"                             → {{"mode": "chat", "skill": null, "tool": null, "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "yes" (pending confirm)              → {{"mode": "confirm", "confirm_index": 0, "skill": null, "tool": null, "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}

- "I want to do something unrelated"   → {{"mode": "unclear", "skill": null, "tool": null, "name": null, "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "what issue with order"              → {{"mode": "skill", "skill": "diagnose", "name": "order", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "diagnose order"                     → {{"mode": "skill", "skill": "diagnose", "name": "order", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}
- "check logs of qp-inventory"         → {{"mode": "skill", "skill": "logs", "name": "qp-inventory", "kind": null, "namespace": null, "field": null, "cluster_name": null, "zone": null, "project": null, "action": null, "explicit_pod": false, "name_filter": null}}

IMPORTANT: For logs:
  - "show logs of X", "check logs X", "get logs for X", "view logs X", "see logs of X" → skill="logs", name="X"
  - NEVER route logs queries to chat mode. If the user asks to see logs, it's always a logs skill request.
  - If no pod name is specified in a logs request, still route to skill="logs" with name=null.

IMPORTANT: For component queries:
  - "X component" → extract name="X" (strip "component"), treat kind="deployment"
  - "is X component up/down/running" → skill="describe", name="X", kind="deployment", field="status"
  - "restart X component" → skill="restart", name="X", kind="deployment"
  - "what issue with X component" → skill="diagnose", name="X", kind="deployment"
  - "component" after a name means deployment. Strip "component" from the name.

IMPORTANT: For diagnose:
  - "what issue with X", "any issue with X", "X is broken", "X is failing", "X has problem" → skill="diagnose", name="X"
  - "diagnose X", "root cause X", "why X crashed" → skill="diagnose", name="X"

IMPORTANT: For restart: 
  - If user explicitly says "pod" (e.g. "restart X pod" or "restart pod X"), set explicit_pod=true and kind="pod".
  - If user says just "restart X" without "pod", set explicit_pod=false and do NOT set kind. The skill will check workloads first.
  - When user mentions "deployment" or "statefulset" explicitly, set kind accordingly and explicit_pod=false.

IMPORTANT: For list:
  - "list X", "show all X", "get all X", "show me X" → skill="list", kind=<resource>, mode="skill"
  - "in all namespaces", "across cluster", "everywhere" → namespace="__all__"
  - If no namespace mentioned → namespace=null (skill defaults to "default")
  - "matching X", "with name X", "filter X" → name_filter="X"
  - "list X" (no kind, X looks like a resource name) → kind=null, name_filter="X" — skill auto-detects the kind
  - "list X pod" or "list X pods" → kind="pod", name_filter="X"
  - "rest component", "rest container", "rest stereotype" are descriptive qualifiers for REST stereotype services — NOT resource kinds. Extract the component name and set kind=null.
  - "rest stereotype" alone (no component name) → list all REST stereotype resources; skill handles the rest
  - If X describes a health condition (restarting, unhealthy, crashlooping, stuck, pending, failing), route to skill="status" instead — these are health scans, not resource name filters.

IMPORTANT: When user says "in <name> cluster" or "on cluster <name>", extract the cluster name into cluster_name and the actual resource into name.

IMPORTANT: For "is it up" / "is it running" / "did it come back" WITHOUT a resource name → set name=null. The system auto-fills the name from context.

IMPORTANT: "is X down", "is X running", "is X up", "did X crash" → skill="describe" with field="status". NEVER route to the "status" skill for specific resource queries — "status" is only for cluster-wide health scanning.

IMPORTANT: For status (cluster-wide health):
  - "any pod restarting", "any issues", "what's broken" → skill="status" even when a cluster name is mentioned
  - "list all restarting pods", "list all unhealthy pods", "list crashlooping pods" → also skill="status" (these are health scans, not resource listings)
  - Any query about pod health conditions (restarting, unhealthy, crashlooping, stuck, pending, failing, errors, issues) → skill="status" even if misspelled or phrased as "list all ... which are ..."
  - Cluster-wide queries always use skill="status" regardless of cluster_name

IMPORTANT: If the query doesn't clearly match any mode, set mode="unclear". Do NOT guess between close options.
Return ONLY valid JSON — no other text.
"""
        response = call_llm(prompt, system=INTENT_SYSTEM_PROMPT)
        return json.loads(response)
    except:
        return {"mode": "chat", "skill": None, "tool": None, "name": None, "kind": None, "namespace": None, "field": None, "cluster_name": None, "zone": None, "project": None, "action": None, "explicit_pod": False, "name_filter": None, "ns": None}


# ---------------- SKILL RESULT HANDLER ---------------- #

def _handle_skill_result(result, user_id, skill_name, args, user_input):
    log_action(user_id, f"skill:{skill_name}", args, result.status)

    if result.status == "success":
        # Track last action for follow-up queries
        if skill_name in ("restart", "diagnose"):
            kind = result.data.get("kind")
            res_name = result.data.get("name")
            res_ns = result.data.get("namespace")
            if kind and res_name:
                set_last_action(user_id, {
                    "type": skill_name,
                    "kind": kind,
                    "name": res_name,
                    "namespace": res_ns or "default",
                })
        # For logs: summarize content via LLM instead of dumping raw output
        if skill_name == "logs" and result.data and result.data.get("logs"):
            log_content = result.data["logs"]
            pod = result.data.get("pod", "")
            summary_prompt = f"""You are a senior DevOps engineer analyzing Kubernetes pod logs.

Pod: {pod}
User asked: {user_input}

Logs (last {result.data.get('lines', '?')} lines):
{log_content}

Provide a concise summary:
1. What is the pod doing / current state?
2. Any errors, failures, or warnings? Quote the key lines.
3. Root cause if identifiable.
4. Recommended next steps.

Be specific and actionable. Do NOT say "run kubectl logs" — the logs are already provided above."""
            summary = call_llm(summary_prompt, system=CHAT_SYSTEM_PROMPT)
            if not summary or summary.startswith("__LLM_ERROR__"):
                return {"type": "message", "text": result.message + f"\n\n```\n{log_content[-3000:]}\n```", "data": result.data}
            return {"type": "message", "text": summary, "data": result.data}

        return {"type": "message", "text": result.message, "data": result.data}

    if result.status == "needs_approval":
        action_id = str(uuid.uuid4())
        actions = get_pending_actions(user_id)
        actions.append({
            "id": action_id,
            "skill": skill_name,
            "args": args,
            "original_input": user_input,
        })
        save_pending_actions(user_id, actions)
        log_action(user_id, f"skill:{skill_name}", args, "PENDING_APPROVAL")
        return approval_card(result.message, action_id)

    if result.status == "needs_confirmation":
        save_pending_confirmation(user_id, {
            "skill": skill_name,
            "args": args,
            "matches": result.data.get("matches", []),
            "original_input": user_input,
        })
        text = result.message
        if result.suggestions:
            text += "\n" + "\n".join(result.suggestions)
        return {"type": "message", "text": text}

    return {"type": "message", "text": result.message}


# ---------------- TOOL HELPERS ---------------- #

def _build_tool_kwargs(tool_name, name, namespace):
    if tool_name in ("get_logs", "describe_pod", "get_pod_image"):
        kwargs = {"pod_name": name}
    elif tool_name == "get_pods":
        kwargs = {}
    elif tool_name == "get_pvc_info":
        kwargs = {"pvc_name": name}
    elif tool_name == "get_pv_info":
        kwargs = {"pv_name": name}
    else:
        kwargs = {}
    if namespace:
        kwargs["namespace"] = namespace
    return kwargs


def _explain_tool_result(user_input, tool_name, result):
    try:
        prompt = f"""
User asked: {user_input}
Tool: {tool_name}
Result: {result}

Respond like a helpful DevOps engineer:
- Summarize clearly
- Highlight important info
- Keep it concise
"""
        text = call_llm(prompt, system=CHAT_SYSTEM_PROMPT)
        if text.startswith("__LLM_ERROR__"):
            return f"Result: {result}"
        return text
    except Exception:
        return f"Result: {result}"


# ---------------- MAIN AGENT ---------------- #

def run_agent(user_id, user_input):
    try:
        return _run_agent(user_id, user_input)
    except Exception as e:
        log_action(user_id, "agent_error", {"input": user_input}, str(e))
        return {"type": "message", "text": f"Something went wrong: {e}"}


def _run_agent(user_id, user_input):

    session = get_user_session(user_id)
    pending_confirm = get_pending_confirmation(user_id)
    last_action = get_last_action(user_id)

    # Always record the user turn regardless of routing path
    if isinstance(session, list):
        session.append(f"User: {user_input}")
    save_user_session(user_id, session)

    # ---- CONFIRMATION HANDLING ----
    # If there's a pending disambiguation and the user is responding to it,
    # route back to the skill with the confirmed resource name.

    if pending_confirm:
        intent = detect_intent(user_input, session, pending_confirm, last_action)

        # Special case: cluster_switch pending — user is providing project/cluster info
        if (
            pending_confirm.get("skill") == "cluster_switch"
            and not pending_confirm.get("matches")
        ):
            pargs = pending_confirm.get("args", {})
            new_intent = detect_intent(user_input, session, None, last_action)

            # Extract project from current input or existing pending state
            new_project = (
                new_intent.get("project")
                or pargs.get("pending_project")
                or user_input.strip()
            )
            # Extract cluster: from pending or current input
            new_cluster = (
                pargs.get("pending_cluster")
                or new_intent.get("cluster_name")
                or new_intent.get("name")
            )
            new_zone = new_intent.get("zone") or pargs.get("pending_zone")

            if not new_cluster:
                # Still no cluster name — ask for it
                save_pending_confirmation(user_id, {
                    **pending_confirm,
                    "args": {**pargs, "pending_project": new_project},
                })
                msg = f"Got it — project is `{new_project}`. Which cluster would you like to connect to?"
                if isinstance(session, list):
                    session.append(f"Assistant: {msg}")
                save_user_session(user_id, session)
                return {"type": "message", "text": msg}

            merged_switch_args = {
                "action": "switch",
                "cluster_name": new_cluster,
                "zone": new_zone,
                "project": new_project,
            }
            clear_pending_confirmation(user_id)
            log_action(user_id, "skill:cluster_switch:project_followup", merged_switch_args, "proceeding")
            switch_result = SKILL_REGISTRY.run("cluster_switch", merged_switch_args, {})

            if switch_result.status != "success":
                response = {"type": "message", "text": switch_result.message}
                if isinstance(session, list):
                    session.append(f"Assistant: {switch_result.message}")
                save_user_session(user_id, session)
                return response

            # Switch succeeded — now run the original skill if one was pending
            pending_skill = pargs.get("pending_skill")
            pending_skill_args = pargs.get("pending_skill_args", {})
            if pending_skill and SKILL_REGISTRY.get(pending_skill):
                log_action(user_id, f"skill:{pending_skill}:after_switch", pending_skill_args, "proceeding")
                result = SKILL_REGISTRY.run(pending_skill, pending_skill_args, {"last_namespace": pending_skill_args.get("namespace")})
                response = _handle_skill_result(result, user_id, pending_skill, pending_skill_args, user_input)
                combined = f"{switch_result.message}\n\n{response.get('text', '')}"
                response["text"] = combined
            else:
                response = {"type": "message", "text": switch_result.message}

            if isinstance(session, list):
                session.append(f"Assistant: {response.get('text', '')}")
            save_user_session(user_id, session)
            return response

        if intent.get("mode") == "confirm":
            matches = pending_confirm["matches"]
            idx = intent.get("confirm_index", 0)
            if idx >= len(matches):
                idx = 0
            match = matches[idx]
            skill_name = pending_confirm["skill"]
            args = {
                **pending_confirm["args"],
                "name": match["name"],
                "namespace": match["namespace"],
            }
            clear_pending_confirmation(user_id)

            log_action(user_id, f"skill:{skill_name}:confirm", args, "proceeding")
            result = SKILL_REGISTRY.run(
                skill_name, args, {"last_namespace": match["namespace"]}
            )
            response = _handle_skill_result(result, user_id, skill_name, args, match["name"])
            if isinstance(session, list):
                session.append(f"Assistant: {response.get('text', '')}")
            save_user_session(user_id, session)
            return response

        # User said something that is not a confirmation → clear it
        clear_pending_confirmation(user_id)

    # ---- NORMAL INTENT DETECTION ---- #

    intent = detect_intent(user_input, session, last_action=last_action)

    mode = intent.get("mode", "chat")
    skill_name = intent.get("skill")
    tool_name = intent.get("tool")
    name = intent.get("name")
    kind = intent.get("kind")
    namespace = intent.get("namespace")
    field = intent.get("field")

    # ---- AUTO-FILL NAME FROM LAST ACTION ---- #
    # If user says "is it up" or "why" without naming a resource, fill from last_action
    # Never auto-fill for status skill (cluster-wide queries)
    if mode == "skill" and skill_name in ("describe", "diagnose") and not name:
        if last_action and last_action.get("type") in ("restart", "diagnose"):
            name = last_action.get("name")
            kind = last_action.get("kind")
            namespace = last_action.get("namespace")

    # ---- UNCLEAR INTENT ---- #

    if mode == "unclear":
        suggestions = [
            "Check cluster health: `any issues?` or `any pod restarting?`",
            "List resources: `list pods`, `list deployments`",
            "Describe a resource: `describe <name>`, `is <name> running?`",
            "Restart a resource: `restart <name>`",
            "Switch clusters: `switch to cluster <name>`",
            "List cluster info: `list clusters`",
        ]
        msg = (
            "I wasn't able to understand your query. Here are some things I can help with:\n"
            + "\n".join(f"  \u2022 {s}" for s in suggestions)
            + "\n\nIf none of these match, please try rephrasing your query."
        )
        if isinstance(session, list):
            session.append(f"Assistant: {msg}")
        save_user_session(user_id, session)
        return {"type": "message", "text": msg}

    # ---- SKILL ROUTING ---- #

    if mode == "skill" and skill_name and SKILL_REGISTRY.get(skill_name):
        args = {}
        if name:
            args["name"] = name
        if kind:
            args["kind"] = kind
        if namespace:
            args["namespace"] = namespace
        if field:
            args["field"] = field
        if intent.get("cluster_name"):
            args["cluster_name"] = intent["cluster_name"]
        if intent.get("zone"):
            args["zone"] = intent["zone"]
        if intent.get("project"):
            args["project"] = intent["project"]
        if intent.get("action"):
            args["action"] = intent["action"]
        if intent.get("explicit_pod"):
            args["explicit_pod"] = True
        if intent.get("name_filter"):
            args["name_filter"] = intent["name_filter"]
        if intent.get("replicas") is not None:
            args["replicas"] = intent["replicas"]
        for _field in ("cpu_request", "cpu_limit", "mem_request", "mem_limit", "heap"):
            if intent.get(_field):
                args[_field] = intent[_field]

        # ---- CLUSTER AUTO-SWITCH ---- #
        # Don't auto-switch when the intent is itself a cluster_switch
        cluster_name_from_intent = intent.get("cluster_name")
        if cluster_name_from_intent and skill_name != "cluster_switch":
            active = get_active()
            if cluster_name_from_intent != active:
                cluster_info = get_cluster(cluster_name_from_intent)
                if cluster_info:
                    switch_result = SKILL_REGISTRY.run("cluster_switch", {
                        "action": "switch",
                        "cluster_name": cluster_name_from_intent,
                    }, {})
                    if switch_result.status != "success":
                        # Save pending state so user can provide missing info (e.g. project)
                        save_pending_confirmation(user_id, {
                            "skill": "cluster_switch",
                            "args": {
                                "pending_project": None,
                                "pending_zone": cluster_info.get("zone"),
                                "pending_cluster": cluster_name_from_intent,
                                "pending_skill": skill_name,
                                "pending_skill_args": args,
                            },
                            "matches": [],
                            "original_input": user_input,
                        })
                        msg = switch_result.message + f"\n\nProvide the project name and I'll retry: **{user_input}**"
                        if isinstance(session, list):
                            session.append(f"Assistant: {msg}")
                        save_user_session(user_id, session)
                        return {"type": "message", "text": msg}
                else:
                    return {
                        "type": "message",
                        "text": (
                            f"Cluster '{cluster_name_from_intent}' is not in my registry. "
                            f"Please tell me the zone and project, e.g.: "
                            f"'switch to {cluster_name_from_intent} in zone us-central1-a project my-project'"
                        )
                    }

        result = SKILL_REGISTRY.run(skill_name, args, {"last_namespace": namespace})
        response = _handle_skill_result(result, user_id, skill_name, args, user_input)
        if isinstance(session, list):
            session.append(f"Assistant: {response.get('text', '')}")
        save_user_session(user_id, session)
        return response

    # ---- TOOL ROUTING (simple listing queries) ---- #

    if mode == "tool" and tool_name and tool_name in AVAILABLE_TOOLS:
        kwargs = _build_tool_kwargs(tool_name, name, namespace)

        if tool_name in {"delete_pod", "restart_resource"}:
            action_id = str(uuid.uuid4())
            actions = get_pending_actions(user_id)
            actions.append({
                "id": action_id,
                "tool_name": tool_name,
                "args": kwargs,
                "original_input": user_input,
            })
            save_pending_actions(user_id, actions)
            log_action(user_id, tool_name, kwargs, "PENDING_APPROVAL")
            cluster_tag = f" [cluster: {get_active()}]" if get_active() else ""
            return approval_card(f"Execute {tool_name} with {kwargs}?{cluster_tag}", action_id)

        try:
            result = AVAILABLE_TOOLS[tool_name](**kwargs)
        except Exception as e:
            result = str(e)

        log_action(user_id, tool_name, kwargs, result)
        explanation = _explain_tool_result(user_input, tool_name, result)
        if isinstance(session, list):
            session.append(f"Assistant: {explanation}")
        save_user_session(user_id, session)
        return {"type": "message", "text": explanation}

    # ---- CHAT / FALLBACK ---- #

    prompt = f"""
Conversation:
{chr(10).join(session[:-1])}

User: {user_input}

Respond helpfully as a DevOps engineer.
"""
    text = call_llm(prompt, system=CHAT_SYSTEM_PROMPT)
    if text.startswith("__LLM_ERROR__"):
        return {"type": "message", "text": f"LLM temporarily unavailable. Please try again."}
    if isinstance(session, list):
        session.append(f"Assistant: {text}")
    save_user_session(user_id, session)
    return {"type": "message", "text": text}
