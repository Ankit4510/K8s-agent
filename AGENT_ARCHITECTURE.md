# Kubernetes AI Agent – Project Documentation

## Overview

This project is a production-oriented Kubernetes AI Agent built using:

* Python 3
* FastAPI
* OpenRouter/OpenAI LLMs
* Redis
* Kubernetes (`kubectl`)
* Adaptive Card approvals

The goal of the system is to create a safe, intelligent DevOps assistant capable of:

* Understanding natural language
* Querying Kubernetes clusters
* Performing controlled operations
* Enforcing policy guardrails
* Maintaining memory/context
* Providing human-friendly explanations

This document is intended for:

* AI IDEs (Cursor, Windsurf, OpenCode)
* Future contributors
* LLM-assisted development
* Project onboarding

---

# High-Level Architecture

```text
User
  ↓
FastAPI API Layer (main.py) ── approval/reject, cluster badge endpoints
  ↓
Agent Layer (agent.py) ── try/except safety net, post-fill logic
  ↓
Intent Detection (LLM) ──→ mode: skill / tool / chat / confirm
  │  schema: {mode, skill, name, kind, namespace, field, cluster_name, zone, project, action, explicit_pod, name_filter, confirm_index}
  │  context: session history, pending_confirm, last_action (post-fill only, not LLM prompt)
  ↓
Skill Routing Layer (skills/)
  ├── restart         ──→ fire-and-forget rollout restart, cluster-guarded
  ├── delete_pod      ──→ delete + replacement watch, cluster-guarded
  ├── logs            ──→ fetch/filter logs + fallback to describe
  ├── describe        ──→ field extraction + health messages, workload-first resolution
  ├── status          ──→ health scan + remediation suggestions
  ├── cluster_switch  ──→ gcloud credentials + "Did you mean?" zone retry
  └── list            ──→ auto-detect kind, prefix-aware matching, formatted tables
  ↓
Policy + Execution Layer (tools.py)
  ↓
Redis Memory (session, actions, confirmations, last_action)
  ↓
Human-Friendly Response
```

---

# Project Structure

```text
agent/
│
├── main.py              # FastAPI entrypoint, approval/reject handler, cluster badge endpoints
├── agent.py             # Core agent logic: intent detection + skill routing + post-fill
├── llm.py               # LLM integration (3 system prompts: TOOL, CHAT, INTENT)
├── tools.py             # Kubernetes low-level tool wrappers (kubectl_cmd, gcloud, etc.)
├── memory.py            # Redis-backed storage (sessions, actions, confirmations, last_action)
├── approvals.py         # Adaptive Card approval card generation
├── logger.py            # Structured JSON logging + skill flow event logging
├── cluster_store.py     # Persistent cluster registry with atomic JSON I/O
├── skills/              # Deterministic playbooks, one per user intent
│   ├── __init__.py      # Auto-registers all skills on import
│   ├── base.py          # Skill (ABC), SkillResult, SkillRegistry
│   ├── resolver.py      # Fuzzy resource resolution (prefers deployments/statefulsets)
│   ├── restart_skill.py # Fire-and-forget rollout restart
│   ├── delete_skill.py  # Pod delete + replacement watch
│   ├── logs_skill.py    # Log fetch + filter + fallback to describe
│   ├── describe_skill.py# Resource describe with field extraction + health messages
│   ├── status_skill.py  # Cluster health scan + remediation suggestions
│   ├── cluster_skill.py # Cluster switch/list with "Did you mean?" zone retry
│   └── list_skill.py    # Resource listing with auto-detect kind, prefix-aware matching
│
└── README.md            # Documentation
```

---

# Core Design Principles

## 1. LLM Handles Language

LLM responsibilities:

* Intent understanding
* Reasoning
* Tool selection
* Human-friendly explanations
* Conversation handling

The LLM should NOT:

* Process huge Kubernetes outputs
* Perform filtering/computation
* Execute raw shell commands directly

---

## 2. Python Handles Systems

Python responsibilities:

* Kubernetes execution
* Data filtering
* Policy enforcement
* Safety validation
* Routing
* Parsing
* Structured querying

This separation is critical.

Correct architecture:

```text
LLM = understanding
Python = execution
```

---

# Current Features

## Safe Kubernetes Operations

Supported operations include:

* Get restarting pods
* Get deployment status
* Describe pods with field extraction
* Fetch logs
* Restart workload owners (fire-and-forget)
* Delete pods (approval required)
* List resources with smart matching (deployments, statefulsets, daemonsets, pods, PVCs, services)
* Switch between Kubernetes clusters
* Health queries ("is X down?", "is X running?")
* Status scanning (cluster-wide issues)

---

## Approval Workflow

Dangerous operations require approval.

Examples:

* Restart workload
* Delete pod

Approval flow:

```text
User Request
   ↓
Agent Detects Dangerous Action
   ↓
Adaptive Card Approval (includes active cluster name)
   ↓
Approve / Reject
   ↓
Execute
```

Destructive ops also require an active cluster — if none is set, the skill returns `needs_confirmation` with "No active cluster" message before any approval is created.

Higher-level destructive actions are blocked.

Blocked actions:

* delete deployment
* delete statefulset
* delete daemonset

Only pod deletion is allowed.

---

# Skill System Architecture

Skills are deterministic Python playbooks — one per category of user intent.
The agent routes to skills via LLM intent detection, not hardcoded if/else chains.

### Skill Lifecycle

```text
User Request
    ↓
LLM Intent Detection → maps to {mode, skill, name, kind, namespace, field, cluster_name, explicit_pod, name_filter}
    context: session history, pending_confirm (Redis), last_action (Redis)
    ↓
SKILL_REGISTRY.run(skill_name, args, context)
    ↓
Skill.execute(args, context)
    ├── cluster_switch → gcloud credential retrieval + "Did you mean?" zone retry
    ├── list           → auto-detect kind, prefix-aware matching, formatted table
    ├── describe with field=status and kind=null → resolve_workload (deployment→statefulset→daemonset→pod)
    │       Resolver.resolve now includes prefix-aware exact match:
    │         query="order" → also checks "com-manh-cp-order" and "all-com-manh-cp-order" as exact
    ├── other skills    → Resolver.resolve(name, kind, namespace) → fuzzy match
    │       Resolution priority by kind:
    │         kind="deployment" → exact deployment match
    │         kind="pod"        → exact pod match
    │         kind=null         → resolve_workload: deployment → statefulset → daemonset → pod
    │       ├── exact match (including prefix-aware) → proceed
    │       ├── fuzzy high (>0.90)→ auto-proceed
    │       ├── fuzzy low (<0.90) → return "needs_confirmation" (saved to Redis)
    │       └── not_found         → return "not_found"
    ├── Execute operation
    └── Return SkillResult(status, message, data)
    ↓
Agent handles result:
    ├── success            → respond to user (store last_action if restart; suggestion footer for pod lists)
    ├── needs_approval     → save to pending_actions (Redis), generate Adaptive Card with cluster tag
    ├── needs_confirmation → save to pending_confirmations (Redis), ask user
    ├── not_found          → report not found (with cross-kind fallback for list)
    └── error              → report error
    ↓
Follow-up handling (next user message):
    ├── "yes" → detect_intent sees pending_confirm → re-run skill with exact match
    ├── "is it up?" → detect_intent sees last_action → route to describe skill with auto-filled name
    └── "any issues?" → general query → route to status skill
```

### Available Skills

| Skill | Name | Triggers | Description |
|-------|------|----------|-------------|
| Restart | `restart` | restart, rollout, bounce | Fire-and-forget rollout restart. Resolves pod → owner. No blocking verify. Cluster-tagged output. |
| Delete Pod | `delete_pod` | delete pod, kill pod, remove pod | Delete a pod (approval required), watch for replacement. Cluster-tagged output. |
| Logs | `logs` | logs, errors, what's wrong | Fetch/filter logs, auto-pick container, fallback to describe on Pending |
| Describe | `describe` | describe, info, image, replicas | Extract specific fields (image, replicas, status). Workload-first resolution when kind=null. Simple up/down for health queries. Auto-include events for unhealthy resources. |
| Status | `status` | status, health, any issues | Cluster/namespace health scan with remediation suggestions. NOT for specific resource status. |
| Cluster Switch | `cluster_switch` | switch, list clusters | Switch active cluster, list known clusters. gcloud credential retrieval with "Did you mean?" zone retry. |
| List | `list` | list, show all, get all | List resources with auto-detect kind, prefix-aware name matching, fuzzy with first-char guard, REST keyword handling, running-pods-only filter, suggestion footer. |

### SkillResult Statuses

| Status | Meaning | Agent Action |
|--------|---------|-------------|
| `success` | Operation completed | Return message + data to user |
| `needs_approval` | Dangerous action pending | Generate Adaptive Card with Approve/Reject |
| `needs_confirmation` | Ambiguous resource name | Return "Did you mean?" suggestions |
| `not_found` | Resource not found | Return not-found message |
| `error` | Execution failed | Return error message |

### Key Design Decisions

- **Skills encapsulate workflows**: each skill handles resolution, execution, and verification internally
- **Shared resolver**: `resolver.py` provides fuzzy matching (exact → substring → difflib) used by all skills. Also includes prefix-aware exact match: query "order" also checks `com-manh-cp-order` and `all-com-manh-cp-order` as exact matches.
- **Resolver prefers deployments/statefulsets**: when no explicit kind is given, resolution order is deployment → statefulset → daemonset → pod (users typically mean workloads, not individual pods)
- **Describe workload-first**: when `field=status` and `kind=null`, describe uses `resolve_workload` directly (not defaulting to pod), so `"is order down"` finds the deployment first
- **List auto-detect kind**: when `kind=null`, ListSkill iterates all supported kinds (pod → deployment → statefulset → daemonset → pvc → service) and returns the best match using prefix-aware scoring
- **Prefix-aware matching**: known prefixes `com-manh-cp-` and `all-com-manh-cp-` are stripped from query/resource names during matching, and prepended to short queries. This enables `"order"` to match `com-manh-cp-order` with score 1.0.
- **First-char guard**: fuzzy matching (SequenceMatcher) only runs when the first character of the query matches the first character of the candidate name. Prevents `"order"` from fuzzy-matching `porter` or `pdcsi-node`.
- **REST keyword detection**: standalone `rest`, `rest component`, `rest container`, `rest stereotype` are recognized as REST stereotype qualifiers (word-bounded regex). The skill strips the keyword and treats the remainder as the component name. `"rest stereotype"` alone lists all resources matching known prefixes.
- **Running-pods filter**: only pods with status Running or Pending are included in list results. Succeeded/Failed pods are hidden.
- **Suggestion footer**: every pod list result includes a footer suggesting prefix/REST keywords when the query didn't use them.
- **List performance**: `_find_best_kind` collects all matches in a single pass through kinds and returns immediately on exact match (score ≥ 1.0), eliminating double kubectl calls.
- **Flow logging**: every step within a skill is logged via `_log_step()` for audit
- **Auto-proceed**: skills auto-proceed on high-confidence fuzzy matches (score ≥ 0.90) without asking
- **Chaining**: skills can call other skills (e.g., DescribeSkill chains LogsSkill on CrashLoopBackOff)
- **Fire-and-forget restart**: the restart skill issues `kubectl rollout restart` and returns immediately. No blocking verification — user follows up with "is it up?" later
- **Context tracking via last_action**: after a restart, the agent stores the resource info in Redis. Follow-up queries like "is it up?" automatically route to describe skill with the stored resource name. `last_action` is never injected into the LLM prompt — only used as post-processing fallback when `describe` has no name.
- **Pending confirmations**: when fuzzy match is low-confidence, the state is saved to Redis. A subsequent "yes" re-runs the skill with the exact matched name
- **Error resilience**: all LLM calls are wrapped in try/except. API failures return friendly messages instead of crashing
- **Cluster guard**: destructive skills (restart, delete) block with `needs_confirmation` if no active cluster is set. Approval payloads include the active cluster name. Output includes `[cluster: <name>]` tag.
- **Cluster badge**: the web UI shows a green/red online status indicator with cluster name and zone. Refreshes every 30s and after every message. Dropdown lets users switch clusters.
- **Atomic cluster storage**: `cluster_store.py` uses write-then-rename for crash safety. Case-insensitive cluster name matching. Default zone `us-central1-f` for unknown clusters.

---

# Intent Detection Architecture

The system uses LLM-based intent detection to route to skills.

Example intent schema:

```json
{
  "mode": "skill | tool | chat",
  "skill": "restart | delete_pod | logs | describe | status",
  "tool": "get_pods | get_pod_image | get_pvc_info | get_pv_info",
  "name": "resource-name",
  "namespace": "namespace",
  "field": "image | replicas | status"
}
```

This avoids brittle string matching.

Example problem solved:

Bad approach:

```python
if "restart" in text:
```

This incorrectly matches:

* restart
* restarting
* restarted

Correct approach:

* Let LLM classify intent
* Route to the correct skill
* Skill handles resolution, execution, and verification

### Routing Decision

Four routing modes exist:

| Mode | Trigger | Handler |
|------|---------|---------|
| `skill` | Complex workflow or resource query | Skill system (skills/) |
| `tool` | Simple listing query | Direct tool call (tools.py) |
| `chat` | Greetings, thanks, unrelated conversation | LLM chat response |
| `confirm` | User confirming a suggested resource match | Re-run skill with exact match |

### Intent Schema

```json
{
  "mode": "skill | tool | chat | confirm",
  "skill": "restart | delete_pod | logs | describe | status | cluster_switch | list | null",
  "tool": "get_pods | get_pod_image | get_pvc_info | get_pv_info | null",
  "name": "resource name | null",
  "kind": "deployment | statefulset | daemonset | pod | null",
  "namespace": "namespace | null",
  "field": "image | replicas | status | null",
  "cluster_name": "cluster name | null",
  "zone": "GKE zone | null",
  "project": "GCP project | null",
  "action": "switch | list | null",
  "explicit_pod": false,
  "name_filter": "filter text | null",
  "confirm_index": 0
}
```

### Intent Detection Prompt Strategy

The prompt includes three dynamic context blocks:

1. **session history**: last 6 conversation turns (helps with "yes" follow-ups)
2. **pending_confirm**: if a previous skill returned `needs_confirmation`, includes the matches with "did you mean?" context
3. **last_action**: if the user recently restarted a resource, includes the resource info and instructs the LLM to use it for follow-up queries

The prompt uses `INTENT_SYSTEM_PROMPT` (not `TOOL_SYSTEM_PROMPT`) to avoid schema conflicts.

---

# Smart Query Routing

The system avoids sending huge Kubernetes outputs to the LLM.

Instead:

```text
kubectl JSON
   ↓
Python filtering
   ↓
Small structured result
   ↓
Optional LLM explanation
```

This dramatically reduces:

* Token usage
* Cost
* Latency

---

# Specialized Skills over Generic Tools

The project prefers specialized skills over generic tools.

Example:

Instead of:

```text
get_pods()
```

Use:

```text
RestartSkill   — full restart workflow with verification
DescribeSkill  — field-specific queries with event context
StatusSkill    — health scan with remediation suggestions
```

Benefits:

* Smaller outputs
* Built-in fuzzy resolution
* Deterministic execution
* Audit trail via flow logging
* Lower token usage (Python filtering before LLM)

---

# Restart Logic

Important design decision:

Users often say:

```text
restart prometheus pod
```

But pods are usually owned by:

* Deployment
* StatefulSet
* DaemonSet

The system resolves:

```text
Pod → Owning Controller
```

Then executes:

```bash
kubectl rollout restart <kind> <name>
```

This is implemented dynamically using Kubernetes ownerReferences.

No hardcoded workload names exist in the codebase.

## Fire-and-Forget with Context Tracking

The restart is fire-and-forget — the skill issues the rollout restart command and returns immediately without blocking on verification. The user can later ask "is it up?" or "did the restart complete?" and the agent checks the resource status.

Flow:

```text
User: "restart prometheus"
  → Issue rollout restart (immediate)
  → "Restart initiated for StatefulSet 'prometheus'"
  → Store last action in Redis (type=restart, kind=StatefulSet, name=prometheus)

User: "is it up?"
  → detect_intent sees last_action context
  → Routes to DescribeSkill with name="prometheus", field="status"
  → "StatefulSet 'prometheus': 1/1 replicas ready, all Running"
```

The last action is stored per-user in Redis under `last_action:{user_id}` — but is **never injected into the LLM prompt**. Instead, it's used as a post-processing fallback: if `detect_intent` returns `describe` with `name=null`, and `last_action.type=restart`, the system auto-fills the resource name/kind/namespace from the stored action. This prevents LLM bias while still supporting follow-ups like "is it up?".

---

# Health Query Routing

When a user asks "is X down?" or "is X running?", the system uses a special path:

1. **Intent detection**: routes to `describe` skill with `field="status"` and `name=<resource>`
2. **Workload-first resolution**: when `kind=null`, `describe` calls `resolve_workload` (deployment → statefulset → daemonset → pod) instead of defaulting to pod
3. **Health message**: for workloads (deployment/statefulset/daemonset), the response is a simple up/down message:
   - ✅ `deployment 'name' is up. 2/2 ready.` (readyReplicas > 0)
   - ❌ `deployment 'name' is down. 0/1 ready.` (readyReplicas == 0)
4. For pods: returns phase directly (`Running`, `Pending`, etc.)

The `status` skill is strictly for cluster-wide health scanning — NOT for individual resource queries.

---

# Multi-Cluster Support

The system supports switching between multiple Kubernetes clusters via gcloud credentials.

## Cluster Store

`cluster_store.py` manages a persistent JSON file at `~/.k8s_agent/clusters.json`:

```json
{
  "active": "prod-us",
  "clusters": {
    "prod-us": {
      "zone": "us-central1-a",
      "project": "my-project",
      "last_used": "2026-05-18T12:00:00Z",
      "verified": true
    }
  }
}
```

Key behaviors:
- **Atomic writes**: uses write-to-temp-file + rename for crash safety
- **Case-insensitive**: cluster name matching is case-insensitive (`GetCluster`, `SetActive`, `AddCluster`)
- **Default zone**: unknown clusters default to `us-central1-f`
- **Verification**: cluster info is persisted only after `kubectl cluster-info` succeeds

## Cluster Switch Skill

`cluster_skill.py` (`ClusterSwitchSkill`):
- Resolves the cluster by name from stored info (zone/project)
- Calls `gcloud_get_credentials()` to fetch GKE credentials
- Calls `kubectl_cluster_info()` to verify connectivity
- Persists verified cluster info to `clusters.json`
- "Did you mean?" zone retry: if gcloud suggests a zone (`_parse_did_you_mean()`), auto-retries with the suggested zone

## Cluster Badge (UI)

The web interface shows the active cluster status:
- **Green dot**: cluster is reachable (cached for 15s)
- **Red dot**: cluster is unreachable
- **Dropdown**: lists all saved clusters for quick switching
- Refreshes every 30s and after every message

## Destructive Action Guard

Skills that modify resources (restart, delete) check for an active cluster:
- If no active cluster is set → returns `needs_confirmation` ("No active cluster. Use `switch to cluster <name>` first.")
- Approval payloads include the active cluster name
- All user-facing messages include `[cluster: <name>]` tag

---

# Prefix-Aware Matching & Company Conventions

The system supports company-specific naming conventions for REST stereotype components.

## Known Prefixes

```python
PREFIXES = ["all-com-manh-cp-", "com-manh-cp-"]
```

These are standard prefixes for REST stereotype microservices. The system handles them in two directions:

### Strip Prefix (query has prefix)
When the query includes a prefix (e.g., `"list com-manh-cp-order"`):
1. Strip the prefix → component name `"order"`
2. Match stripped name against stripped resource names
3. Exact match → score 1.0
4. Substring match → score 0.9
5. **No fuzzy matching** — if neither exact nor substring matches, score is 0.0

### Prepend Prefix (query is short name)
When the query is a short name without prefix (e.g., `"list order"`):
1. Also check if any resource name matches `com-manh-cp-<query>` or `all-com-manh-cp-<query>` exactly
2. Also match query against prefix-stripped resource names
3. Example: query `"order"` matches resource `com-manh-cp-order` with score 1.0

### Resolver Integration
`Resolver.resolve()` also includes prefix-aware exact matching: after checking `"order" == candidate`, it checks `"com-manh-cp-order" == candidate` and `"all-com-manh-cp-order" == candidate`. When found, returns `"exact"` status so the skill auto-proceeds without asking for confirmation.

## REST Keyword Detection

```python
_REST_PATTERN = re.compile(r'\brest\b(?:\s+(?:component|container|stereotype))?', re.IGNORECASE)
```

Recognized patterns:
| User says | Extracted component | Behavior |
|-----------|-------------------|----------|
| `"order rest component"` | `"order"` | List/describe with prefix awareness |
| `"order rest container"` | `"order"` | Same as above |
| `"rest stereotype order"` | `"order"` | Same (REST keyword can appear anywhere) |
| `"rest"` | `""` | Keyword stripped, component is remainder |
| `"rest stereotype"` | `""` | List all resources matching known prefixes |
| `"order rest"` | `"order"` | Standalone `rest` recognized via word boundary |

The word boundary (`\b`) prevents false matches on words like `restart` or `restore`.

## Running Pods Filter

When listing pods, only pods with phase `Running` or `Pending` are shown. Succeeded, Failed, and other terminal states are hidden. This is applied globally to all pod list operations.

## First-Character Fuzzy Guard

Fuzzy matching (SequenceMatcher) only runs when the first character of the query matches the first character of the candidate name:
```python
if query_l and name_l and query_l[0] == name_l[0]:
    return SequenceMatcher(None, query_l, name_l).ratio()
return 0.0
```
This prevents `"order"` from fuzzy-matching `porter` or `pdcsi-node` (different first letters).

---

# List Skill

`ListSkill` is a specialized skill for listing Kubernetes resources with smart matching.

## Supported Kinds

```python
SUPPORTED_KINDS = {"deployment", "statefulset", "daemonset", "pod", "pvc", "service"}
```

## Auto-Detect Kind

When `kind=null`, ListSkill iterates all supported kinds in priority order (pod → deployment → statefulset → daemonset → pvc → service), filters by `name_filter`, and returns the best match. No more "missing resource kind" errors.

## Name Matching Score Priority

For each resource name, the system computes a match score using this priority:

| Step | Condition | Score |
|------|-----------|-------|
| 1 | Query had prefix, stripped matches exactly | 1.0 |
| 2 | Query had prefix, stripped is substring | 0.9 |
| 3 | Direct exact match (name == query) | 1.0 |
| 4 | Direct substring match (query in name) | 0.5–0.95 |
| 5 | Name has prefix, stripped name matches query | 1.0 |
| 6 | Name has prefix, query is substring of stripped | 0.8–0.95 |
| 7 | Prepend prefix to query, matches name | 1.0 |
| 8 | Prepend prefix, prefixed in name | 0.9 |
| 9 | Fuzzy (first char must match) | 0.0–1.0 |

Results below the 0.40 threshold are excluded.

## Performance Optimization

`_find_best_kind()` replaces the old two-pass approach:
- Collects all matches from all kinds in a single iteration
- Returns immediately when a kind produces an exact match (score ≥ 1.0)
- Eliminates duplicated kubectl calls between best-match search and cross-kind fallback

## Cross-Kind Fallback

If nothing is found in the requested kind, the skill:
1. Searches across all workload kinds (deployment, statefulset, daemonset) with the same name_filter
2. Reports what was found across kinds
3. If absolutely nothing matches, returns closest fuzzy matches with "nothing found, check spelling" message and suggests scaling

## Suggestion Footer

Every pod list result includes a suggestion footer if the query didn't already use prefix or REST keywords:

```
---
Not what you're looking for? Try:
  • `list com-manh-cp-<name>` — exact prefix match for REST components
  • `list <name> rest component` — REST stereotype keyword search
```

---

# Redis Memory System

Redis is used instead of in-memory Python dictionaries.

Used for:

| Key Pattern | Purpose | TTL |
|-------------|---------|-----|
| `session:{user_id}` | Conversation history (list of user messages) | None |
| `pending:{user_id}` | Pending approval actions (dangerous operations awaiting confirm) | None |
| `confirm:{user_id}` | Pending resource disambiguation (fuzzy match suggestions) | None |
| `last_action:{user_id}` | Most recent action context (e.g. restart info for follow-ups) | None |

Benefits:

* Survives app restart
* Multi-instance ready
* Kubernetes-ready
* Fast access

Current Redis setup:

```python
redis.Redis(host="localhost", port=6379)
```

Future Kubernetes deployment:

```text
App Pod ↔ Redis Service
```

---

# Logging & Audit Trail

Structured JSON logging is implemented using `RotatingFileHandler` (5MB per file, 3 backups).

Two log functions:

| Function | Called By | Fields |
|----------|-----------|--------|
| `log_action(user, tool, args, result)` | agent.py, main.py | timestamp, user, tool, args, result |
| `log_event(skill, step, details)` | skills/base.py (each _log_step) | timestamp, skill, step, details |

Logs are stored in:

```text
agent.log
```

Example:

```json
{
  "timestamp": "2026-04-27T12:10:23Z",
  "user": "ankit",
  "tool": "restart_resource",
  "args": {
    "kind": "statefulset",
    "name": "prometheus"
  },
  "result": "success"
}
```

Skill flow log example:

```json
{
  "timestamp": "2026-04-27T12:10:23Z",
  "skill": "restart",
  "step": "resolve_pod",
  "details": {"name": "prometheus"}
}
```

---

# Human-Friendly Responses

The system avoids returning raw kubectl output directly.

Instead:

```text
Tool Result
   ↓
LLM Explanation
   ↓
Human Response
```

Example:

Instead of:

```text
NAME READY STATUS
```

The system returns:

```text
No pods are currently restarting. All pods appear healthy.
```

---

# Token Optimization Strategy

The project intentionally minimizes token usage.

Key strategies:

## DO

* Filter data in Python
* Use specialized tools
* Send summaries to LLM
* Route known queries directly

## DO NOT

* Send massive pod lists to LLM
* Use LLM for filtering
* Use raw `kubectl get pods` output unnecessarily

---

# Safety Model

The project follows a layered safety model.

## Layer 1 – Intent Detection

LLM determines:

* action vs query
* requested resource
* requested operation

---

## Layer 2 – Policy Enforcement

Python validates:

* allowed actions
* blocked actions
* dangerous operations

---

## Layer 3 – Approval Workflow

Dangerous actions require manual approval.

---

## Layer 4 – Controlled Execution

Python builds kubectl commands.

LLM NEVER executes raw shell commands directly.

---

# Important Engineering Decisions

## Why Not Raw Grep?

Using:

```bash
kubectl get pods | grep ...
```

is brittle because:

* human-readable format may change
* difficult to scale
* parsing errors possible

Preferred approach:

```text
kubectl -o json
   ↓
Python filtering
```

---

# Kubernetes Philosophy Used

Important Kubernetes concepts implemented:

## Pods are Ephemeral

Pods are not truly "restarted".

Instead:

* controllers recreate pods
* workloads are restarted via rollout restart

---

## Owners Matter

Pods may belong to:

* Deployment
* StatefulSet
* DaemonSet
* Job
* CronJob

The system dynamically resolves ownership.

---

# Future Improvements

Potential next steps:

## Infrastructure

* Helm deployment
* Kubernetes deployment manifests
* Redis inside cluster
* RBAC integration
* JWT/API auth

---

## Observability

* Prometheus metrics
* Grafana dashboards
* Loki/ELK integration

---

## Agent Intelligence

* Multi-step planning
* Confidence scoring
* Ambiguity handling
* Bulk actions
* Auto-remediation

---

## UX

* Streaming logs
* Rich adaptive cards
* Slack/Teams integration

---

# Development Guidelines for AI IDEs

Important instructions for future AI-assisted development.

## Follow Existing Architecture

DO NOT:

* Add large monolithic logic
* Hardcode workload names
* Use raw string matching for intent
* Send large outputs to LLM
* Add if/else chains for routing — use the skill system
* Add blocking verification after actions — use fire-and-forget + follow-up queries
* Use TOOL_SYSTEM_PROMPT for intent detection — use INTENT_SYSTEM_PROMPT (separate concerns)
* Leave LLM calls unprotected — always wrap in try/except with fallback

DO:

* Create new skills for new workflows (extend skills/)
* Add **kind** field to detect_intent schema if adding new resource type
* Include **examples** in detect_intent prompt for every new routing case
* Store context in Redis (last_action, pending_confirm) for follow-up queries
* Format skill output messages to include the data inline (not just in `data` dict)
* Prefer deployments/statefulsets over pods in resolve_workload
* Filter data in Python
* Keep LLM focused on reasoning
* Maintain approval workflow
* Preserve policy enforcement
* Use SkillRegistry.run() instead of direct tool calls
* Use Resolver for fuzzy resource matching

---

# Coding Philosophy

Preferred style:

* Modular
* Explicit
* Safe
* Observable
* Token-efficient
* Kubernetes-aware

Avoid:

* Magic behavior
* Hidden execution
* Raw shell injection
* LLM-only logic

---

# Final Project Goal

The long-term goal is to evolve this system into:

```text
Production-grade AI-powered Kubernetes Operations Assistant
```

Capabilities should eventually include:

* Intelligent troubleshooting
* Safe autonomous operations
* Infrastructure awareness
* Human approvals
* Observability integration
* Multi-cluster support

while maintaining:

* safety
* auditability
* explainability
* operational trust
