"""
Persistent cluster registry with atomic file I/O.
Stores known clusters, tracks active cluster, and persists verified credentials.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional

CLUSTERS_FILE = os.path.expanduser("~/.k8s_agent/clusters.json")

DEFAULT_CLUSTER_ZONE = "us-central1-f"


def _ensure_dir():
    os.makedirs(os.path.dirname(CLUSTERS_FILE), exist_ok=True)


def load() -> Dict:
    _ensure_dir()
    if not os.path.exists(CLUSTERS_FILE):
        return {"active": None, "clusters": {}}
    try:
        with open(CLUSTERS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"active": None, "clusters": {}}


def save(data: Dict):
    _ensure_dir()
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        dir=os.path.dirname(CLUSTERS_FILE),
        prefix=".clusters_tmp_",
        delete=False,
    )
    try:
        json.dump(data, tmp, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, CLUSTERS_FILE)
    except Exception:
        os.unlink(tmp.name)
        raise


def list_clusters() -> List[Dict]:
    data = load()
    active = data.get("active")
    return [
        {
            "name": name,
            "zone": info.get("zone"),
            "project": info.get("project"),
            "last_used": info.get("last_used"),
            "verified": info.get("verified", False),
            "active": name == active,
        }
        for name, info in data.get("clusters", {}).items()
    ]


def get_active() -> Optional[str]:
    return load().get("active")


def get_cluster(name: str) -> Optional[Dict]:
    if not name:
        return None
    data = load()
    key = name.strip().lower()
    for stored_name, info in data.get("clusters", {}).items():
        if stored_name.lower() == key:
            return {**info, "name": stored_name}
    return None


def set_active(cluster_name: Optional[str]):
    data = load()
    data["active"] = cluster_name
    if cluster_name:
        key = cluster_name.strip().lower()
        for stored_name in list(data.get("clusters", {}).keys()):
            if stored_name.lower() == key:
                data["clusters"][stored_name]["last_used"] = datetime.now(
                    timezone.utc
                ).isoformat()
                break
    save(data)


def add_cluster(
    cluster_name: str,
    zone: Optional[str] = None,
    project: Optional[str] = None,
    verified: bool = False,
):
    data = load()
    clusters = data.setdefault("clusters", {})
    # Case-insensitive lookup for existing entry
    existing = None
    existing_key = None
    key = cluster_name.strip().lower()
    for stored_name in list(clusters.keys()):
        if stored_name.lower() == key:
            existing = clusters[stored_name]
            existing_key = stored_name
            break
    if existing_key:
        clusters[cluster_name] = {
            "zone": zone or existing.get("zone") or DEFAULT_CLUSTER_ZONE,
            "project": project or existing.get("project"),
            "last_used": datetime.now(timezone.utc).isoformat(),
            "verified": verified or existing.get("verified", False),
        }
        if existing_key != cluster_name:
            del clusters[existing_key]
    else:
        clusters[cluster_name] = {
            "zone": zone or DEFAULT_CLUSTER_ZONE,
            "project": project,
            "last_used": datetime.now(timezone.utc).isoformat(),
            "verified": verified,
        }
    save(data)
