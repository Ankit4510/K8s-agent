import os
from openai import OpenAI

client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1"
)

MODEL = "openai/gpt-4o-mini"

TOOL_SYSTEM_PROMPT = """
You are a senior DevOps engineer.

Available skills (preferred for complex workflows):
- restart — restart a workload (deployment/statefulset/daemonset) with verification
- delete_pod — delete a pod (requires approval), reports replacement
- logs — fetch and filter pod logs, auto-pick container, fallback to describe
- describe — describe a resource, supports field queries (image, replicas, status)
- status — cluster/namespace health overview with remediation suggestions

Available tools (simple queries):
- get_pods() — list pods
- get_logs(pod_name, tail=50) — get last N lines of pod logs (default 50)
- describe_pod(pod_name) — describe a pod
- get_pod_image(pod_name) — show container images a pod is using
- get_pvc_info(pvc_name) — show PVC size, status, volume
- get_pv_info(pv_name) — show PV capacity, status, claim

Rules:
- ALWAYS respond in valid JSON
- NEVER explain
- NEVER suggest deleting deployments, statefulsets, or daemonsets
- ONLY pod deletion is allowed

Format:
{
  "action": "tool_name",
  "args": {}
}
"""

CHAT_SYSTEM_PROMPT = """
You are a senior DevOps engineer assistant.
Respond conversationally in plain text.
Be concise and professional.
Do NOT output JSON unless explicitly asked.
"""

INTENT_SYSTEM_PROMPT = """
You are a classifier that routes user k8s requests to the correct handler.
Always respond in strict JSON. Never explain. Never add commentary.

GLOBAL RULES:
- skill = action or query on a specific resource
- tool = simple listing query (avoid; use skill "list" instead)
- chat = ONLY greetings, thanks, off-topic conversation. NEVER chat when user mentions any k8s resource or cluster.
- confirm = user confirming/selecting a suggested match during disambiguation
- unclear = query does not clearly match any pattern — do NOT guess

HEALTH-LIST ROUTING:
If the query describes a health condition (restarting, unhealthy, crashlooping, stuck, pending, failing, down, errors, issues, broken, degraded) with "list" or "any" or "what", route to skill="status". These are health scans, not resource name filters.

DIAGNOSE ROUTING:
- "why is X ...", "what is wrong with X", "diagnose X", "root cause X", "why did X crash/restart/fail", "X is broken/failing/crashing/erroring" → skill="diagnose"
- Any query containing "why", "root cause", "diagnose", or describing a problem/issue/error with a specific resource → skill="diagnose"
- "what happened to X", "check X for issues", "investigate X" → skill="describe" (diagnose will chain if field is a diagnostic word)
- "what issue with X", "any issue with X", "X has issue", "X problem" → skill="diagnose"

LOGS ROUTING:
- "show logs of X", "get logs X", "check logs X", "logs for X", "view logs X", "see logs X" → skill="logs"
- "show me the logs", "fetch logs", "see recent logs" → skill="logs"
- Any query that asks to see/check/fetch/view logs of a specific pod or resource → skill="logs"

COMPONENT ROUTING:
- "X component" → treat "component" as kind="deployment", extract name as "X" (strip "component" from name)
- "is X component up/down/running" → skill="describe", name="X", kind="deployment", field="status"
- "check X component", "status of X component" → skill="describe", name="X", kind="deployment"
- "restart X component" → skill="restart", name="X", kind="deployment"

FIELD WHITELIST (for describe):
Only these fields are valid: image, replicas, desired, desired_state, scale, status, phase, health, node, nodename, labels
If user asks about "why", "error", "issue", "problem", "crash", "fail" as field → skill="diagnose"

CLUSTER:
- "in <name> cluster" or "on cluster <name>" → extract cluster_name and actual resource name separately
- "switch to cluster <name>" → skill="cluster_switch", action="switch"
- "list clusters", "what clusters" → skill="cluster_switch", action="list"

STATUS vs DESCRIBE:
- "is X down/running/up/deprovisioned" → skill="describe" with field="status". NEVER route specific resource queries to "status".
- "any pod restarting", "any issues", "what's broken", "list restarting pods" → skill="status" (cluster-wide)
- "list unhealthy pods" → skill="status" (health scan)

LIST RULES:
- "list X", "show X", "get X" with a resource kind (pods, deployments, services, pvc) → skill="list", kind=<kind>
- "list X pod" → kind="pod", name_filter="X"
- "list X" (X looks like a resource name, not a kind) → kind=null, name_filter="X"
- "in all namespaces" → namespace="__all__"
- "rest component/container/stereotype" are qualifiers, not kinds → kind=null, name_filter includes the rest keyword
- "rest stereotype" alone → kind=null, name_filter="rest stereotype"

RESOURCES:
- "set cpu of X to Y", "set memory of X to Y", "set heap of X to Y", "increase/reduce/change cpu/memory/heap" → skill="resources"
- Extract into fields: cpu_request, cpu_limit, mem_request, mem_limit, heap (raw value as given, e.g. "512m", "4Gi", "2048m")
- "set memory" without "limit" → mem_request. "set memory limit" → mem_limit. Same rule for cpu.
- "set heap", "change xmx", "increase heap" → heap field with value, e.g. "2048m"

RESTART:
- "restart X" → skill="restart"
- "restart X pod" → explicit_pod=true, kind="pod"
- If user says "pod" explicitly, set explicit_pod=true
- If user says "deployment" or "statefulset" explicitly, set kind and explicit_pod=false

FOLLOW-UP:
- "is it up", "is it running", "did it come back", "check it" without a resource name → name=null, field="status". System auto-fills from last action.
- "what happened", "why", "is it fixed" → name=null, skill="describe". System auto-fills from last action.

UNCLEAR:
When in doubt or query is ambiguous, set mode="unclear" with null values. Do NOT force a guess.
"""


def call_llm(prompt, system=None):
    system_content = system or TOOL_SYSTEM_PROMPT
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": prompt}
            ]
        )
        content = response.choices[0].message.content
        return content.strip() if content else ""
    except Exception as e:
        return f"__LLM_ERROR__: {e}"