import subprocess
import json


# ---------------- COMMAND RUNNER ---------------- #

def run_command(cmd, timeout=15):
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.stdout if result.returncode == 0 else f"Error: {result.stderr}"
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout}s"
    except Exception as e:
        return f"Error running command: {str(e)}"


def kubectl_cmd(cmd_args, timeout=15):
    return run_command(["kubectl"] + cmd_args, timeout=timeout)


# ---------------- BASIC TOOLS ---------------- #

def get_pods(namespace=None):
    cmd = ["get", "pods"]
    if namespace:
        cmd.extend(["-n", namespace])
    return kubectl_cmd(cmd)


def get_logs(pod_name, tail=500, namespace=None):
    cmd = ["logs", pod_name, "--tail", str(tail)]
    if namespace:
        cmd.extend(["-n", namespace])
    return kubectl_cmd(cmd, timeout=30)


def describe_pod(pod_name):
    return kubectl_cmd(["describe", "pod", pod_name])


# ---------------- DELETE POD (ALLOWED) ---------------- #

def delete_pod(name, namespace=None):
    cmd = ["delete", "pod", name]
    if namespace:
        cmd.extend(["-n", namespace])
    return kubectl_cmd(cmd)


# ---------------- OWNER RESOLUTION ---------------- #

def _get_pod_json(pod_name, namespace=None):
    cmd = ["get", "pod", pod_name, "-o", "json"]
    if namespace:
        cmd.extend(["-n", namespace])
    data = kubectl_cmd(cmd)
    try:
        return json.loads(data)
    except:
        return None


def _resolve_owner(pod_json, namespace):
    owners = pod_json.get("metadata", {}).get("ownerReferences", [])
    if not owners:
        return None, None

    owner = owners[0]
    kind = owner.get("kind")
    name = owner.get("name")

    if kind == "ReplicaSet":
        rs_data = kubectl_cmd([
            "get", "rs", name,
            "-n", namespace,
            "-o", "json"
        ]) if namespace else kubectl_cmd([
            "get", "rs", name,
            "-o", "json"
        ])

        try:
            rs = json.loads(rs_data)
            rs_owners = rs.get("metadata", {}).get("ownerReferences", [])
            if rs_owners:
                parent = rs_owners[0]
                return parent.get("kind"), parent.get("name")
        except:
            pass

    return kind, name


def get_owner_from_pod(pod_name, namespace=None):
    pod = _get_pod_json(pod_name, namespace)
    if pod:
        kind, owner = _resolve_owner(pod, namespace)
        if kind and owner:
            return kind, owner, namespace

    # Fallback: search all namespaces
    all_data = kubectl_cmd([
        "get", "pod", pod_name,
        "--all-namespaces", "-o", "json"
    ])
    try:
        all_pods = json.loads(all_data)
        items = all_pods.get("items", [])
    except:
        items = []

    for p in items:
        ns = p.get("metadata", {}).get("namespace", "default")
        kind, owner = _resolve_owner(p, ns)
        if kind and owner:
            return kind, owner, ns

    return None, None, None


# ---------------- RESOURCE ADJUSTMENT ---------------- #

def get_deployment_json(kind, name, namespace):
    cmd = ["get", kind.lower(), name, "-o", "json"]
    if namespace:
        cmd.extend(["-n", namespace])
    raw = kubectl_cmd(cmd)
    try:
        return json.loads(raw), None
    except Exception:
        return None, raw


def get_container_resources(kind, name, namespace):
    """Return current CPU/memory requests+limits and JAVA_OPTS for the first container."""
    data, err = get_deployment_json(kind, name, namespace)
    if not data:
        return None, err
    containers = data.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    if not containers:
        return None, "No containers found in deployment spec."
    c = containers[0]
    resources = c.get("resources", {})
    env_list = c.get("env", [])
    env = {e["name"]: e.get("value", "") for e in env_list if "name" in e}
    java_key = next((k for k in ("JAVA_OPTS", "JVM_OPTS", "JAVA_TOOL_OPTIONS") if k in env), None)
    return {
        "container_name": c.get("name"),
        "requests": resources.get("requests", {}),
        "limits": resources.get("limits", {}),
        "java_opts": env.get(java_key) if java_key else None,
        "java_opts_key": java_key or "JAVA_OPTS",
    }, None


def patch_container_resources(kind, name, namespace, container_name,
                               cpu_request=None, cpu_limit=None,
                               mem_request=None, mem_limit=None):
    """Patch CPU/memory requests and limits on the first container via strategic merge patch."""
    data, err = get_deployment_json(kind, name, namespace)
    if not data:
        return f"Error: could not read {kind} {name}: {err}"
    containers = data.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    if not containers:
        return "Error: no containers in spec."
    c_name = container_name or containers[0].get("name")
    existing = containers[0].get("resources", {})
    requests = dict(existing.get("requests", {}))
    limits = dict(existing.get("limits", {}))
    if cpu_request:
        requests["cpu"] = cpu_request
    if mem_request:
        requests["memory"] = mem_request
    if cpu_limit:
        limits["cpu"] = cpu_limit
    if mem_limit:
        limits["memory"] = mem_limit
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [{
                        "name": c_name,
                        "resources": {"requests": requests, "limits": limits},
                    }]
                }
            }
        }
    }
    cmd = ["patch", kind.lower(), name, "--patch", json.dumps(patch), "--type", "strategic"]
    if namespace:
        cmd.extend(["-n", namespace])
    return kubectl_cmd(cmd)


def set_java_opts(kind, name, namespace, new_xmx):
    """Read existing JAVA_OPTS, replace only -Xmx value, write back via kubectl set env."""
    import re
    info, err = get_container_resources(kind, name, namespace)
    if not info:
        return None, f"Error reading deployment: {err}"
    existing = info.get("java_opts") or ""
    key = info.get("java_opts_key", "JAVA_OPTS")
    if re.search(r"-Xmx\S+", existing):
        updated = re.sub(r"-Xmx\S+", f"-Xmx{new_xmx}", existing)
    else:
        updated = (existing + f" -Xmx{new_xmx}").strip()
    cmd = ["set", "env", f"{kind.lower()}/{name}", f"{key}={updated}"]
    if namespace:
        cmd.extend(["-n", namespace])
    out = kubectl_cmd(cmd)
    return out, updated


# ---------------- GENERIC SCALE ---------------- #

def scale_resource(kind, name, namespace, replicas):
    kind = kind.lower()
    if kind not in ["deployment", "statefulset"]:
        return f"Error: Scaling not supported for {kind}. Only deployments and statefulsets."
    cmd = ["scale", kind, name, f"--replicas={replicas}"]
    if namespace:
        cmd.extend(["-n", namespace])
    return kubectl_cmd(cmd)


# ---------------- GENERIC RESTART ---------------- #

def restart_resource(kind, name, namespace=None):
    kind = kind.lower()

    if kind not in ["deployment", "statefulset", "daemonset"]:
        return f"❌ Restart not supported for {kind}"

    cmd = ["rollout", "restart", kind, name]
    if namespace:
        cmd.extend(["-n", namespace])
    return kubectl_cmd(cmd)


# ---------------- POD IMAGE ---------------- #

def get_pod_image(pod_name, namespace=None):
    cmd = ["get", "pod", pod_name, "-o", "json"]
    if namespace:
        cmd.extend(["-n", namespace])
    data = kubectl_cmd(cmd)
    try:
        pod = json.loads(data)
    except:
        return f"Error: Could not get details for pod '{pod_name}'"

    meta = pod.get("metadata", {})
    ns = namespace or meta.get("namespace", "default")
    result = f"Pod: {pod_name} (namespace: {ns})\n"

    for c in pod.get("spec", {}).get("containers", []):
        result += f"  Container '{c['name']}': {c['image']}\n"
    for c in pod.get("spec", {}).get("initContainers", []):
        result += f"  Init Container '{c['name']}': {c['image']}\n"

    return result


# ---------------- PVC / PV ---------------- #

def get_pvc_info(pvc_name, namespace=None):
    cmd = ["get", "pvc", pvc_name, "-o", "json"]
    if namespace:
        cmd.extend(["-n", namespace])
    data = kubectl_cmd(cmd)
    try:
        pvc = json.loads(data)
    except:
        return f"Error: Could not get PVC '{pvc_name}'"

    meta = pvc.get("metadata", {})
    spec = pvc.get("spec", {})
    status = pvc.get("status", {})
    capacity = status.get("capacity", {}).get("storage", "N/A")

    result = f"PVC: {meta.get('name')}\n"
    result += f"  Namespace: {meta.get('namespace', 'default')}\n"
    result += f"  Status: {status.get('phase', 'Unknown')}\n"
    result += f"  Capacity: {capacity}\n"
    result += f"  Requested: {spec.get('resources', {}).get('requests', {}).get('storage', 'N/A')}\n"
    result += f"  Access Modes: {spec.get('accessModes', [])}\n"
    result += f"  Storage Class: {spec.get('storageClassName', 'N/A')}\n"
    result += f"  Volume: {spec.get('volumeName', 'N/A')}\n"
    return result


def get_pv_info(pv_name):
    data = kubectl_cmd(["get", "pv", pv_name, "-o", "json"])
    try:
        pv = json.loads(data)
    except:
        return f"Error: Could not get PV '{pv_name}'"

    spec = pv.get("spec", {})
    status = pv.get("status", {})

    result = f"PV: {pv.get('metadata', {}).get('name')}\n"
    result += f"  Status: {status.get('phase', 'Unknown')}\n"
    result += f"  Capacity: {spec.get('capacity', {}).get('storage', 'N/A')}\n"
    result += f"  Access Modes: {spec.get('accessModes', [])}\n"
    result += f"  Reclaim Policy: {spec.get('persistentVolumeReclaimPolicy', 'N/A')}\n"
    result += f"  Storage Class: {spec.get('storageClassName', 'N/A')}\n"
    claim = spec.get("claimRef", {})
    if claim:
        result += f"  Bound to: {claim.get('namespace', '')}/{claim.get('name', '')}\n"
    return result


# ---------------- ANALYSIS ---------------- #

def get_deployments():
    output = kubectl_cmd(["get", "deployments", "-o", "json"])
    try:
        return json.loads(output)
    except:
        return {}


def analyze_deployments():
    data = get_deployments()
    findings = []

    for d in data.get("items", []):
        desired = d["spec"].get("replicas", 1)
        if desired == 0:
            continue

        available = d["status"].get("availableReplicas", 0)

        if available < desired:
            findings.append({
                "name": d["metadata"]["name"],
                "namespace": d["metadata"].get("namespace", "default"),
                "suggestion": "restart"
            })

    return findings or "All deployments are healthy"


# ---------------- MULTI-CLUSTER ---------------- #

def gcloud_get_credentials(cluster_name, zone=None, project=None):
    cmd = ["gcloud", "container", "clusters", "get-credentials", cluster_name]
    if zone:
        cmd.extend(["--zone", zone])
    if project:
        cmd.extend(["--project", project])
    result = run_command(cmd, timeout=30)
    if result.startswith("Error"):
        # Check if gcloud is even installed
        if "not found" in result or "No such file" in result:
            return "Error: gcloud CLI not found. Install from https://cloud.google.com/sdk/docs/install"
        return result
    # Success — now verify we can actually talk to the cluster
    verify = kubectl_cluster_info()
    if verify.startswith("Error"):
        return f"Credentials obtained but cluster unreachable: {verify}"
    return result


def kubectl_cluster_info():
    return kubectl_cmd(["cluster-info"], timeout=10)


# ---------------- DIAGNOSE HELPERS ---------------- #

def get_pod_events(pod_name, namespace=None):
    cmd = ["get", "events", "--field-selector", f"involvedObject.name={pod_name}",
           "--sort-by=.lastTimestamp", "-o", "json"]
    if namespace:
        cmd.extend(["-n", namespace])
    data = kubectl_cmd(cmd)
    try:
        return json.loads(data)
    except:
        return {"items": []}


def get_container_waiting_reasons(pod_json):
    reasons = {"containers": [], "initContainers": []}
    for cs in pod_json.get("status", {}).get("containerStatuses", []):
        state = cs.get("state", {})
        waiting = state.get("waiting", {})
        terminated = state.get("terminated", {})
        entry = {"name": cs.get("name"), "ready": cs.get("ready"), "restartCount": cs.get("restartCount", 0)}
        if waiting:
            entry["reason"] = waiting.get("reason", "")
            entry["message"] = waiting.get("message", "")
        elif terminated:
            entry["reason"] = terminated.get("reason", "")
            entry["message"] = terminated.get("message", "")
            entry["exitCode"] = terminated.get("exitCode")
        else:
            entry["reason"] = "Running"
        reasons["containers"].append(entry)
    for cs in pod_json.get("status", {}).get("initContainerStatuses", []):
        state = cs.get("state", {})
        waiting = state.get("waiting", {})
        terminated = state.get("terminated", {})
        entry = {"name": cs.get("name"), "ready": cs.get("ready"), "restartCount": cs.get("restartCount", 0)}
        if waiting:
            entry["reason"] = waiting.get("reason", "")
            entry["message"] = waiting.get("message", "")
        elif terminated:
            entry["reason"] = terminated.get("reason", "")
            entry["message"] = terminated.get("message", "")
            entry["exitCode"] = terminated.get("exitCode")
        else:
            entry["reason"] = "Running"
        reasons["initContainers"].append(entry)
    return reasons


def get_pod_pvcs(pod_json):
    pvcs = []
    volumes = pod_json.get("spec", {}).get("volumes")
    if not volumes:
        return pvcs
    for vol in volumes:
        pvc = vol.get("persistentVolumeClaim")
        if pvc:
            pvcs.append({"volumeName": vol.get("name"), "claimName": pvc.get("claimName")})
    return pvcs


def get_pvc_status(pvc_name, namespace=None):
    cmd = ["get", "pvc", pvc_name, "-o", "json"]
    if namespace:
        cmd.extend(["-n", namespace])
    data = kubectl_cmd(cmd)
    try:
        return json.loads(data)
    except:
        return None


def categorize_failure(pod_json):
    phase = pod_json.get("status", {}).get("phase", "")
    reasons = get_container_waiting_reasons(pod_json)

    all_reasons = reasons["containers"] + reasons["initContainers"]
    all_waiting = [r for r in all_reasons if r.get("reason")]
    reasons_priority = [r["reason"] for r in all_waiting]

    # Check init containers first
    for r in reasons["initContainers"]:
        reason = r.get("reason", "")
        if reason and reason not in ("Running", "Completed"):
            if "CrashLoop" in reason:
                return "Init"
            if "ImagePull" in reason or "ErrImage" in reason:
                return "Init"
            return "Init"

    # Check OOM in lastState
    for cs in pod_json.get("status", {}).get("containerStatuses", []):
        last = cs.get("lastState", {}).get("terminated", {})
        if last.get("reason") == "OOMKilled":
            return "OOM"

    # Categorize by waiting reason priority
    for reason in reasons_priority:
        if reason == "Running":
            continue
        if "ImagePull" in reason or "ErrImage" in reason or "InvalidImage" in reason:
            return "ImagePull"
        if "CrashLoop" in reason:
            return "CrashLoop"
        if "CreateContainerConfigError" in reason or "InvalidImageName" in reason:
            return "Config"

    # Check scheduling
    for cond in pod_json.get("status", {}).get("conditions", []):
        if cond.get("type") == "PodScheduled" and cond.get("status") == "False":
            if "Unschedulable" in cond.get("reason", ""):
                msg = cond.get("message", "")
                if "PVC" in msg or "volume" in msg.lower() or "mount" in msg.lower():
                    return "Volume"
                if "memory" in msg.lower() or "cpu" in msg.lower() or "Insufficient" in msg:
                    return "Scheduling"
                return "Scheduling"

    # Check volume conditions
    for cond in pod_json.get("status", {}).get("conditions", []):
        msg = cond.get("message", "")
        if "PVC" in msg or "volume" in msg.lower() or "mount" in msg.lower():
            return "Volume"

    # Check for Running-but-not-ready containers
    if phase == "Running":
        for r in all_waiting:
            if r.get("reason") in ("CrashLoopBackOff", "Error"):
                return "CrashLoop"
        # Container is Running but not ready
        for cs in pod_json.get("status", {}).get("containerStatuses", []):
            if not cs.get("ready", True) and cs.get("state", {}).get("running"):
                return "Readiness"
        # Running with restart > 0 but currently okay
        for cs in pod_json.get("status", {}).get("containerStatuses", []):
            if cs.get("restartCount", 0) > 0:
                return "CrashLoop"

    # Check phase
    if phase == "Pending":
        # Check for PVC issues via volumes
        pvcs = get_pod_pvcs(pod_json)
        if pvcs:
            return "Volume"
        return "Unknown"

    return "Unknown"


def get_resource_yaml(kind, name, namespace=None):
    cmd = ["get", kind, name, "-o", "json"]
    if namespace:
        cmd.extend(["-n", namespace])
    data = kubectl_cmd(cmd)
    try:
        return json.loads(data)
    except:
        return None


def get_deployment_pods(deployment_name, namespace=None):
    cmd = ["get", "pods", "-l", f"app={deployment_name}", "-o", "json"]
    if namespace:
        cmd.extend(["-n", namespace])
    data = kubectl_cmd(cmd)
    try:
        return json.loads(data)
    except:
        return {"items": []}


# ---------------- TOOL REGISTRY ---------------- #

AVAILABLE_TOOLS = {
    "get_pods": get_pods,
    "get_logs": get_logs,
    "describe_pod": describe_pod,
    "delete_pod": delete_pod,
    "restart_resource": restart_resource,
    "analyze_deployments": analyze_deployments,
    "get_pod_image": get_pod_image,
    "get_pvc_info": get_pvc_info,
    "get_pv_info": get_pv_info
}

SAFE_TOOLS = {
    "get_pods",
    "get_logs",
    "describe_pod",
    "analyze_deployments",
    "get_pod_image",
    "get_pvc_info",
    "get_pv_info"
}

DANGEROUS_TOOLS = {
    "delete_pod",
    "restart_resource"
}